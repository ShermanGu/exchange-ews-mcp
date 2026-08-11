from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from exchange_ews_mcp import server, service
from exchange_ews_mcp.calendar_workflow import CalendarWorkflow
from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import AttachmentResult, CalendarItemResult, DraftResult, EwsClient
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.tool_profiles import tool_names
from exchange_ews_mcp.workflow import SemanticMailWorkflow


def config(**overrides) -> AppConfig:
    values = {
        "ews_url": "https://mail.company.com/EWS/Exchange.asmx",
        "username": "DOMAIN\\user",
        "primary_email": "self@company.com",
        "company_email_domains": ["company.com"],
        "calendar_time_zone": "UTC",
        "calendar_workday_start": "09:00",
        "calendar_workday_end": "18:00",
    }
    values.update(overrides)
    return AppConfig(**values)


class DirectRecipientClient:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.validated: list[str] = []
        self.attached: list[dict] = []

    def resolve_names(self, *, query: str, limit: int = 100, **kwargs):
        return {
            "query": query,
            "status": "resolved",
            "returned": 1,
            "candidates": [{"display_name": "User", "email": query, "mailbox_type": "Mailbox"}],
        }

    def search_emails_multi_folder(self, **kwargs):
        return {"returned": 0, "items": [], "per_folder": []}

    def search_emails(self, **kwargs):
        return {"returned": 0, "items": []}

    def validate_attachment_path(self, path: str) -> str:
        self.validated.append(path)
        if path == "bad.txt":
            raise ValueError("invalid attachment")
        return f"C:/safe/{path}"

    def create_draft(self, **kwargs):
        self.created.append(kwargs)
        return DraftResult(item_id="D1", change_key="CK1", subject=kwargs["subject"], to=kwargs["to"])

    def add_attachment_to_draft(self, **kwargs):
        self.attached.append(kwargs)
        index = len(self.attached)
        return AttachmentResult(
            attachment_id=f"A{index}", root_item_id="D1", root_item_change_key=f"CK{index + 1}",
            filename=Path(kwargs["file_path"]).name, size=10, content_type="text/plain",
        )


def test_compose_prevalidates_all_attachments_before_creating_draft(tmp_path: Path) -> None:
    client = DirectRecipientClient()
    workflow = SemanticMailWorkflow(client, ReferenceStore(tmp_path / "state.db"), config())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid attachment"):
        workflow.compose_email(
            to_queries=["user@company.com"], subject="s", body_html="<p>b</p>",
            attachments=["good.txt", "bad.txt"],
        )
    assert client.validated == ["good.txt", "bad.txt"]
    assert client.created == []
    assert client.attached == []


def test_compose_attachment_chain_uses_latest_changekey(tmp_path: Path) -> None:
    client = DirectRecipientClient()
    store = ReferenceStore(tmp_path / "state.db")
    workflow = SemanticMailWorkflow(client, store, config())  # type: ignore[arg-type]
    result = workflow.compose_email(
        to_queries=["user@company.com"], subject="s", body_html="<p>b</p>",
        attachments=["one.txt", "two.txt"],
    )
    assert [call["change_key"] for call in client.attached] == ["CK1", "CK2"]
    assert result["draft"]["change_key"] == "CK3"
    assert result["draft"]["reference_kind"] == "draft"
    assert result["draft"]["update_tool"] == "edit_mail_draft"
    stored = store.get_reference(result["draft"]["draft_ref"], expected_kind="draft")
    assert stored.payload["change_key"] == "CK3"


class MultipleMessageClient(DirectRecipientClient):
    def __init__(self) -> None:
        super().__init__()
        self.replied: list[dict] = []

    def search_emails_multi_folder(self, *, participant_contains=None, **kwargs):
        if participant_contains:
            return {"returned": 0, "items": [], "per_folder": []}
        return {
            "returned": 2,
            "items": [
                {"item_id": "M1", "change_key": "K1", "subject": "周报 A", "folder": "inbox"},
                {"item_id": "M2", "change_key": "K2", "subject": "周报 B", "folder": "inbox"},
            ],
            "per_folder": [],
        }

    def reply_as_draft(self, **kwargs):
        self.replied.append(kwargs)
        return DraftResult(item_id="R1", change_key="RK1", draft_type="reply")


def test_resume_message_must_belong_to_pending_candidates(tmp_path: Path) -> None:
    client = MultipleMessageClient()
    store = ReferenceStore(tmp_path / "state.db")
    workflow = SemanticMailWorkflow(client, store, config())  # type: ignore[arg-type]
    pending = workflow.reply_to_email(subject_contains="周报", body_html="<p>reply</p>")
    assert pending["status"] == "needs_confirmation"
    outsider = store.upsert_reference(
        kind="message", external_key="OUTSIDE", payload={"item_id": "OUTSIDE", "change_key": "KO"}
    )
    with pytest.raises(ValueError, match="不属于本次"):
        workflow.continue_action(
            resume_token=pending["resume_token"], selections={"message_ref": outsider}
        )
    valid = pending["message_resolution"]["items"][1]["message_ref"]
    result = workflow.continue_action(
        resume_token=pending["resume_token"], selections={"message_ref": valid}
    )
    assert result["status"] == "draft_created"
    assert client.replied[0]["item_id"] == "M2"


def test_reference_store_closes_every_connection(monkeypatch, tmp_path: Path) -> None:
    import exchange_ews_mcp.state_store as state_store_module

    real_connect = sqlite3.connect
    proxies = []

    class Proxy:
        def __init__(self, inner):
            object.__setattr__(self, "inner", inner)
            object.__setattr__(self, "closed", False)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def __setattr__(self, name, value):
            if name in {"inner", "closed"}:
                object.__setattr__(self, name, value)
            else:
                setattr(self.inner, name, value)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self.inner.__exit__(exc_type, exc, tb)

        def close(self):
            self.closed = True
            self.inner.close()

    def tracking_connect(*args, **kwargs):
        proxy = Proxy(real_connect(*args, **kwargs))
        proxies.append(proxy)
        return proxy

    monkeypatch.setattr(state_store_module.sqlite3, "connect", tracking_connect)
    store = ReferenceStore(tmp_path / "state.db")
    ref = store.upsert_reference(kind="message", external_key="M1", payload={"item_id": "M1"})
    store.get_reference(ref)
    token = store.create_action_session({"action": "x"})
    store.update_action_session(token, status="needs_confirmation")
    store.delete_action_session(token)
    assert proxies and all(proxy.closed for proxy in proxies)


def test_attachment_allowlist_rejects_path_outside_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "inside.txt"
    inside.write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    client = EwsClient(config(attachment_roots=[str(allowed)]), "password")
    assert client.validate_attachment_path(str(inside)) == str(inside.resolve())
    with pytest.raises(ValueError, match="不在允许目录"):
        client.validate_attachment_path(str(outside))


class FakeMailResolver:
    def resolve_people(self, *, query: str, **kwargs):
        email = query if "@" in query else f"{query}@company.com"
        return {
            "selection_status": "resolved", "selected": {"email": email, "person_ref": f"p_{query}"},
            "candidates": [{"email": email, "person_ref": f"p_{query}"}],
            "default_rule_applied": None, "user_notice": None,
        }


class MultiSlotCalendarClient:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def get_user_availability(self, *, attendees, start, end, interval_minutes):
        return {
            "status": "success", "start": start, "end": end,
            "attendees": [
                {**item, "status": "success", "response_code": "NoError", "events": [], "working_hours": None}
                for item in attendees
            ],
        }

    def create_meeting(self, **kwargs):
        self.created.append(kwargs)
        return CalendarItemResult(
            item_id="CAL1", change_key="C1", subject=kwargs["subject"], start=kwargs["start"],
            end=kwargs["end"], required_attendees=kwargs["required_attendees"],
            optional_attendees=kwargs["optional_attendees"], sent=kwargs["send_invitations"],
        )


def test_window_schedule_requires_slot_then_send_confirmation(tmp_path: Path) -> None:
    client = MultiSlotCalendarClient()
    workflow = CalendarWorkflow(
        client, ReferenceStore(tmp_path / "state.db"), config(), FakeMailResolver()  # type: ignore[arg-type]
    )
    pending_slot = workflow.schedule_meeting(
        attendee_queries=["alice"], subject="Planning", body_html="<p>x</p>",
        window_start="2026-08-03T09:00:00Z", window_end="2026-08-03T12:00:00Z",
        duration_minutes=60, send_invitations=True,
    )
    assert pending_slot["confirmation_type"] == "meeting_time"
    selected_start = pending_slot["pending_slots"][0]["start"]
    pending_send = workflow.continue_action(
        resume_token=pending_slot["resume_token"], selections={"slot": selected_start}
    )
    assert pending_send["confirmation_type"] == "send_invitations"
    assert client.created == []
    saved = workflow.continue_action(
        resume_token=pending_send["resume_token"], selections={"confirm": "no"}
    )
    assert saved["status"] == "meeting_saved_not_sent"
    assert client.created[0]["send_invitations"] is False


def test_all_server_wrappers_delegate_to_matching_service(monkeypatch) -> None:
    required_values = {
        "to": ["a@company.com"], "to_queries": ["alice"], "attendee_queries": ["alice"],
        "attendees": [{"email": "a@company.com", "attendee_type": "Required"}],
        "subject": "s", "body_html": "<p>b</p>", "file_path": "x.txt",
        "resume_token": "resume_x", "selections": {}, "draft_ref": "draft_x",
        "start": "2026-08-03T09:00:00Z", "end": "2026-08-03T10:00:00Z",
        "window_start": "2026-08-03T09:00:00Z", "window_end": "2026-08-03T12:00:00Z",
        "required_attendees": ["a@company.com"], "query": "alice",
        "weekly_report_ref": "weekly_x",
        "weekly_flow_token": "weeklyflow_x",
        "user_input": "更新项目进展",
        "changes": [{"id": "s1", "text": "新周报"}],
        "mode": "compose",
        "calendar_ref": "cal_x",
    }
    for name in tool_names(include_debug_tools=True):
        received = {}

        def fake(**kwargs):
            received.update(kwargs)
            return {"tool": name}

        monkeypatch.setattr(service, name, fake)
        function = getattr(server, name)
        kwargs = {}
        for parameter in inspect.signature(function).parameters.values():
            if parameter.default is inspect.Parameter.empty:
                kwargs[parameter.name] = required_values[parameter.name]
        assert function(**kwargs) == {"tool": name}
        assert set(kwargs) <= set(received)
