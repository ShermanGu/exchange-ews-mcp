from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from exchange_ews_mcp import service
from exchange_ews_mcp.server import create_mcp
from exchange_ews_mcp.state_store import ReferenceStore


def test_resolve_people_remains_independent_semantic_tool(monkeypatch) -> None:
    received = {}

    class Workflow:
        def resolve_people(self, **kwargs):
            received.update(kwargs)
            return {"selection_status": "resolved", "selected": {"email": "alice@example.com"}}

    monkeypatch.setattr(service, "configured_workflow", lambda: Workflow())
    result = service.resolve_people(query="alice", limit=25, lookback_days=90, auto_select=False)
    assert result["selected"]["email"] == "alice@example.com"
    assert received == {
        "query": "alice",
        "limit": 25,
        "lookback_days": 90,
        "auto_select": False,
    }


def test_search_mail_routes_all_filters_to_semantic_finder(monkeypatch) -> None:
    received = {}

    def fake_find_email(**kwargs):
        received.update(kwargs)
        return {"status": "multiple_matches", "items": []}

    monkeypatch.setattr(service, "find_email", fake_find_email)
    result = service.search_mail(
        folders=["inbox"],
        sender_query="alice",
        subject_contains="status",
        unread_only=True,
        has_attachments=False,
        limit=5,
        offset=10,
    )
    assert result["status"] == "multiple_matches"
    assert received["folders"] == ["inbox"]
    assert received["sender_query"] == "alice"
    assert received["unread_only"] is True
    assert received["has_attachments"] is False
    assert received["limit"] == 5
    assert received["offset"] == 10


def test_read_mail_accepts_only_agent_references(monkeypatch) -> None:
    received = {}

    def fake_get_email(**kwargs):
        received.update(kwargs)
        return {"subject": "hello"}

    monkeypatch.setattr(service, "get_email", fake_get_email)
    assert service.read_mail(message_ref="msg_x")["subject"] == "hello"
    assert received == {
        "message_ref": "msg_x",
        "draft_ref": None,
        "max_body_chars": 50000,
    }


def test_message_ref_parameter_rejects_draft_reference(monkeypatch, tmp_path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    draft_ref = store.upsert_reference(
        kind="draft", external_key="D1", payload={"item_id": "D1"}, ttl_days=30
    )
    monkeypatch.setattr(service, "configured_store", lambda: store)

    class Client:
        def get_email(self, **kwargs):
            raise AssertionError("Exchange must not be called for the wrong reference kind")

    monkeypatch.setattr(service, "configured_client", lambda: Client())
    with pytest.raises(ValueError, match="不是 message"):
        service.get_email(message_ref=draft_ref)


def test_save_mail_draft_dispatches_compose_reply_and_forward(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        service,
        "compose_email",
        lambda **kwargs: calls.append(("compose", kwargs)) or {"status": "draft_created"},
    )
    monkeypatch.setattr(
        service,
        "reply_to_email",
        lambda **kwargs: calls.append(("reply", kwargs)) or {"status": "draft_created"},
    )
    monkeypatch.setattr(
        service,
        "forward_email",
        lambda **kwargs: calls.append(("forward", kwargs)) or {"status": "draft_created"},
    )

    composed = service.save_mail_draft(
        mode="compose",
        to_queries=["alice"],
        subject="Hello",
        body_html="<p>Hi</p>",
    )
    replied = service.save_mail_draft(
        mode="reply_all",
        source_message_ref="msg_x",
        body_html="<p>Thanks</p>",
    )
    forwarded = service.save_mail_draft(
        mode="forward",
        source_message_ref="msg_x",
        to_queries=["bob"],
        body_html="<p>FYI</p>",
    )

    assert [name for name, _ in calls] == ["compose", "reply", "forward"]
    assert calls[1][1]["reply_all"] is True
    assert calls[2][1]["message_ref"] == "msg_x"
    assert composed["mail_draft_mode"] == "compose" and composed["sent"] is False
    assert replied["mail_draft_mode"] == "reply_all"
    assert forwarded["mail_draft_mode"] == "forward"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "compose", "body_html": "<p>x</p>"}, "to_queries"),
        ({"mode": "reply", "body_html": "<p>x</p>"}, "source_message_ref"),
        (
            {
                "mode": "reply",
                "body_html": "<p>x</p>",
                "source_message_ref": "msg_x",
                "attachments": ["x.txt"],
            },
            "edit_mail_draft",
        ),
    ],
)
def test_save_mail_draft_rejects_mode_incompatible_fields(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        service.save_mail_draft(**kwargs)


def test_edit_mail_draft_prevalidates_every_attachment_before_writing(monkeypatch, tmp_path) -> None:
    events: list[str] = []
    store = ReferenceStore(tmp_path / "state.db")
    draft_ref = store.upsert_reference(kind="draft", external_key="D1", payload={"item_id": "D1"}, ttl_days=30)
    monkeypatch.setattr(service, "configured_store", lambda: store)

    class Validator:
        def validate_attachment_path(self, path: str) -> str:
            events.append(f"validate:{path}")
            return f"C:/safe/{path}"

    monkeypatch.setattr(service, "configured_client", lambda: Validator())
    monkeypatch.setattr(
        service,
        "update_email_draft",
        lambda **kwargs: events.append("update") or {"draft_ref": kwargs["draft_ref"]},
    )

    def attach(**kwargs):
        events.append(f"attach:{kwargs['file_path']}")
        return {"draft_ref": "draft_x", "filename": kwargs["file_path"]}

    monkeypatch.setattr(service, "add_attachment_to_draft", attach)
    result = service.edit_mail_draft(
        draft_ref=draft_ref,
        subject="Updated",
        attachments=["one.txt", "two.txt"],
    )
    assert events == [
        "validate:one.txt",
        "validate:two.txt",
        "update",
        "attach:C:/safe/one.txt",
        "attach:C:/safe/two.txt",
    ]
    assert len(result["attachments"]) == 2
    assert result["sent"] is False


def test_edit_mail_draft_invalid_later_attachment_blocks_every_write(monkeypatch, tmp_path) -> None:
    writes: list[str] = []
    store = ReferenceStore(tmp_path / "state.db")
    draft_ref = store.upsert_reference(kind="draft", external_key="D1", payload={"item_id": "D1"}, ttl_days=30)
    monkeypatch.setattr(service, "configured_store", lambda: store)

    class Validator:
        def validate_attachment_path(self, path: str) -> str:
            if path == "bad.txt":
                raise ValueError("bad attachment")
            return path

    monkeypatch.setattr(service, "configured_client", lambda: Validator())
    monkeypatch.setattr(
        service,
        "update_email_draft",
        lambda **kwargs: writes.append("update") or {},
    )
    monkeypatch.setattr(
        service,
        "add_attachment_to_draft",
        lambda **kwargs: writes.append("attach") or {},
    )
    with pytest.raises(ValueError, match="bad attachment"):
        service.edit_mail_draft(
            draft_ref=draft_ref,
            subject="Updated",
            attachments=["good.txt", "bad.txt"],
        )
    assert writes == []


def test_edit_mail_draft_attachment_only_rejects_calendar_ref_before_file_preflight(monkeypatch, tmp_path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    calendar_ref = store.upsert_reference(
        kind="calendar", external_key="CAL1", payload={"item_id": "CAL1"}, ttl_days=30
    )
    monkeypatch.setattr(service, "configured_store", lambda: store)

    class Validator:
        def validate_attachment_path(self, path: str) -> str:
            raise AssertionError("file preflight must not run for a calendar_ref")

    monkeypatch.setattr(service, "configured_client", lambda: Validator())
    result = service.edit_mail_draft(draft_ref=calendar_ref, attachments=["x.txt"])
    assert result["status"] == "wrong_reference_type"
    assert result["recommended_tool"] == "save_meeting"
    assert result["sent"] is False


def test_continue_action_rejects_unknown_route_without_guessing(monkeypatch, tmp_path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    token = store.create_action_session({"action": "future_weekly_action", "payload": {}}, ttl_hours=1)
    monkeypatch.setattr(service, "configured_store", lambda: store)
    monkeypatch.setattr(
        service, "configured_workflow",
        lambda: (_ for _ in ()).throw(AssertionError("mail workflow must not be guessed")),
    )
    monkeypatch.setattr(
        service, "configured_calendar_workflow",
        lambda: (_ for _ in ()).throw(AssertionError("calendar workflow must not be guessed")),
    )
    with pytest.raises(ValueError, match="未知或不支持"):
        service.continue_action(resume_token=token, selections={})


def test_read_calendar_dispatches_reference_or_window(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "get_calendar_item",
        lambda **kwargs: {"calendar_ref": kwargs["calendar_ref"]},
    )
    monkeypatch.setattr(
        service,
        "list_calendar_events",
        lambda **kwargs: {"items": [], **kwargs},
    )
    item = service.read_calendar(calendar_ref="cal_x")
    window = service.read_calendar(start="2026-08-10T09:00:00+08:00", end="2026-08-10T18:00:00+08:00")
    assert item["read_mode"] == "item"
    assert window["read_mode"] == "window"
    with pytest.raises(ValueError, match="不能同时"):
        service.read_calendar(calendar_ref="cal_x", start="x", end="y")


def test_save_meeting_creates_or_updates_without_sending(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        service,
        "schedule_meeting",
        lambda **kwargs: calls.append(("create", kwargs)) or {"status": "meeting_saved_not_sent"},
    )
    monkeypatch.setattr(
        service,
        "update_meeting",
        lambda **kwargs: calls.append(("update", kwargs)) or {"status": "meeting_updated_not_sent"},
    )

    created = service.save_meeting(
        attendee_queries=["alice"],
        subject="Planning",
        body_html="<p>x</p>",
        start="2026-08-11T09:00:00+08:00",
        end="2026-08-11T10:00:00+08:00",
    )
    updated = service.save_meeting(
        calendar_ref="cal_x",
        subject="Updated",
        attendee_queries=["alice@company.com"],
    )
    assert created["status"] == "meeting_saved_not_sent"
    assert calls[0][1]["send_invitations"] is False
    assert calls[0][1]["confirm_send"] is False
    assert updated["status"] == "meeting_updated_not_sent"
    assert calls[1][1]["required_attendees"] == ["alice@company.com"]


def test_save_meeting_requires_exact_time_and_full_email_on_update(monkeypatch) -> None:
    with pytest.raises(ValueError, match="find_meeting_times"):
        service.save_meeting(
            attendee_queries=["alice"],
            subject="Planning",
            body_html="<p>x</p>",
        )
    with pytest.raises(ValueError, match="完整邮箱"):
        service.save_meeting(
            calendar_ref="cal_x",
            attendee_queries=["alice"],
        )


def test_compact_mcp_schema_stays_below_context_budget() -> None:
    instance = create_mcp()
    if hasattr(instance, "list_tools"):
        tools = asyncio.run(instance.list_tools())
        serialized = [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
    else:
        # Minimal FastMCP fallback used by unit tests when the real dependency is
        # absent. Keep a deterministic approximation of the Agent-visible schema
        # instead of failing on a test-stub implementation detail.
        tools = list(instance._tools)
        serialized = [
            {
                "name": tool.__name__,
                "description": inspect.getdoc(tool) or "",
                "signature": str(inspect.signature(tool)),
            }
            for tool in tools
        ]
    payload = json.dumps(
        {"tools": serialized},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(tools) == 11
    assert len(payload) < 12_000
