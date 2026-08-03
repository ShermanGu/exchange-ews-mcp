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
