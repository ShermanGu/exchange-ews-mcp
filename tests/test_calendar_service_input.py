from exchange_ews_mcp import service
from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import CalendarItemResult
from exchange_ews_mcp.state_store import ReferenceStore


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def get_user_availability(self, **kwargs):
        self.calls.append(("availability", kwargs))
        return {
            "status": "success", "start": kwargs["start"], "end": kwargs["end"],
            "attendees": [],
        }

    def list_calendar_events(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {
            "status": "success", "start": kwargs["start"], "end": kwargs["end"],
            "items": [], "returned": 0,
        }

    def create_meeting(self, **kwargs):
        self.calls.append(("create", kwargs))
        return CalendarItemResult(
            item_id="CAL1", change_key="CK1", subject=kwargs["subject"],
            start=kwargs["start"], end=kwargs["end"],
            required_attendees=kwargs["required_attendees"],
            optional_attendees=kwargs.get("optional_attendees") or [],
            location=kwargs.get("location"), sent=kwargs["send_invitations"],
        )

    def get_calendar_item(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {
            "item_id": kwargs["item_id"],
            "change_key": "CURRENT_CK",
            "subject": "Planning",
            "body_html": "<p>current</p>",
            "start": "2026-08-03T01:00:00Z",
            "end": "2026-08-03T02:00:00Z",
            "location": "Room 1",
            "is_meeting": True,
            "is_cancelled": False,
            "meeting_request_was_sent": False,
            "required_attendees": [{"email": "alice@example.com"}],
            "optional_attendees": [],
        }

    def update_meeting(self, **kwargs):
        self.calls.append(("update", kwargs))
        return {
            "status": "meeting_updated_not_sent",
            "item_id": kwargs["item_id"],
            "change_key": "UPDATED_CK",
            "sent": False,
        }

    def send_meeting_invitation(self, **kwargs):
        self.calls.append(("send", kwargs))
        return {
            "status": "meeting_invitation_sent",
            "item_id": kwargs["item_id"],
            "change_key": "SENT_CK",
            "sent": True,
        }


def configured() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx", username="D\\u",
        primary_email="self@example.com", calendar_time_zone="Asia/Shanghai",
    )


def install_fakes(monkeypatch, tmp_path):
    client = FakeClient()
    store = ReferenceStore(tmp_path / "state.db")
    monkeypatch.setattr(service, "load_config", configured)
    monkeypatch.setattr(service, "configured_client", lambda: client)
    monkeypatch.setattr(service, "configured_store", lambda: store)
    return client


def test_atomic_availability_accepts_local_time(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    result = service.get_user_availability(
        attendees=[{"email": "room@example.com", "attendee_type": "Room"}],
        start="2026-08-03T09:00:00", end="2026-08-03T10:00:00",
    )
    assert client.calls[0][1]["start"] == "2026-08-03T01:00:00Z"
    assert result["local_start"] == "2026-08-03T09:00:00+08:00"


def test_atomic_calendar_list_accepts_local_time(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    service.list_calendar_events(
        start="2026-08-03T09:00:00", end="2026-08-03T18:00:00"
    )
    assert client.calls[0][1]["start"] == "2026-08-03T01:00:00Z"
    assert client.calls[0][1]["end"] == "2026-08-03T10:00:00Z"


def test_atomic_create_meeting_accepts_local_time(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    result = service.create_meeting(
        subject="Planning", body_html="<p>x</p>",
        start="2026-08-03T09:00:00", end="2026-08-03T10:00:00",
        required_attendees=["alice@example.com"],
    )
    assert client.calls[0][1]["start"] == "2026-08-03T01:00:00Z"
    assert result["local_start"] == "2026-08-03T09:00:00+08:00"


def test_update_meeting_refreshes_current_change_key_and_keeps_unsent(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    result = service.update_meeting(
        item_id="CAL1",
        subject="Updated",
        start="2026-08-03T10:00:00",
        end="2026-08-03T11:00:00",
    )
    update_call = next(call for call in client.calls if call[0] == "update")
    assert update_call[1]["change_key"] == "CURRENT_CK"
    assert update_call[1]["start"] == "2026-08-03T02:00:00Z"
    assert update_call[1]["send_invitations"] is False
    assert result["status"] == "meeting_updated_not_sent"
    assert result["sent"] is False


def test_send_meeting_invitation_requires_explicit_confirmation(monkeypatch, tmp_path) -> None:
    install_fakes(monkeypatch, tmp_path)
    try:
        service.send_meeting_invitation(item_id="CAL1")
    except ValueError as exc:
        assert "confirm_send=true" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_send_meeting_invitation_uses_current_change_key(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    result = service.send_meeting_invitation(item_id="CAL1", confirm_send=True)
    send_call = next(call for call in client.calls if call[0] == "send")
    assert send_call[1]["change_key"] == "CURRENT_CK"
    assert send_call[1]["subject"] == "Planning"
    assert result["status"] == "meeting_invitation_sent"
    assert result["sent"] is True
    assert result["calendar_item"]["meeting_request_was_sent"] is True


def test_send_meeting_invitation_rejects_already_sent_meeting(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    original = client.get_calendar_item

    def already_sent(**kwargs):
        item = original(**kwargs)
        item["meeting_request_was_sent"] = True
        return item

    client.get_calendar_item = already_sent  # type: ignore[method-assign]
    try:
        service.send_meeting_invitation(item_id="CAL1", confirm_send=True)
    except ValueError as exc:
        assert "已经发送" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert not any(name == "send" for name, _ in client.calls)


def test_update_meeting_accepts_false_is_meeting_when_attendees_exist(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    original = client.get_calendar_item

    def inconsistent_exchange_flag(**kwargs):
        item = original(**kwargs)
        item["is_meeting"] = False
        return item

    client.get_calendar_item = inconsistent_exchange_flag  # type: ignore[method-assign]
    result = service.update_meeting(item_id="CAL1", body_html="<p>updated</p>")
    assert result["status"] == "meeting_updated_not_sent"
    assert any(name == "update" for name, _ in client.calls)


def test_update_meeting_rejects_true_appointment_without_attendees(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    original = client.get_calendar_item

    def appointment(**kwargs):
        item = original(**kwargs)
        item["is_meeting"] = False
        item["required_attendees"] = []
        item["optional_attendees"] = []
        return item

    client.get_calendar_item = appointment  # type: ignore[method-assign]
    try:
        service.update_meeting(item_id="CAL1", subject="Updated")
    except ValueError as exc:
        assert "未发现任何参会人" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert not any(name == "update" for name, _ in client.calls)


def test_update_email_draft_routes_calendar_reference_without_mutation(monkeypatch, tmp_path) -> None:
    client = install_fakes(monkeypatch, tmp_path)
    store = service.configured_store()
    calendar_ref = store.upsert_reference(
        kind="calendar",
        external_key="CAL1",
        payload={"item_id": "CAL1", "item_kind": "meeting"},
        ttl_days=30,
    )
    result = service.update_email_draft(
        draft_ref=calendar_ref,
        body_html="<p>do not apply through mail tool</p>",
    )
    assert result["status"] == "wrong_reference_type"
    assert result["recommended_tool"] == "save_meeting"
    assert result["calendar_ref"] == calendar_ref
    assert client.calls == []
