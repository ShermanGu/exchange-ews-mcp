from __future__ import annotations

from pathlib import Path

from exchange_ews_mcp import __version__
from exchange_ews_mcp.dt_runner import run_phase2_integration_tests
from exchange_ews_mcp.ews import AttachmentResult, DraftResult
from exchange_ews_mcp.phase2_config import Phase2TestConfig


class FakeClient:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.drafts: dict[str, dict] = {}
        self.attachments: dict[str, list[dict]] = {}
        self.reply_change_keys: list[str | None] = []
        self.forward_change_keys: list[str | None] = []

    def test_connection(self) -> str:
        return "Inbox"

    def list_emails(self, **kwargs):
        return {
            "returned": 1,
            "total_items_in_view": 1,
            "includes_last_item": True,
            "items": [],
        }

    def search_emails(self, *, sender: str, **kwargs):
        return {
            "returned": 1,
            "items": [
                {
                    "item_id": f"SOURCE-{sender}",
                    "change_key": "SOURCE-CK",
                    "subject": "Test message",
                    "from": {"email": sender},
                }
            ],
        }

    def get_email(self, *, item_id: str, **kwargs):
        if item_id.startswith("SOURCE-"):
            return {
                "item_id": item_id,
                "change_key": "SOURCE-CK",
                "subject": "Test message",
                "from": {"email": item_id.removeprefix("SOURCE-")},
                "body_html": "<p>source</p>",
                "body_type": "HTML",
                "body_truncated": False,
                "is_draft": False,
                "attachments": [],
            }
        draft = self.drafts[item_id].copy()
        draft["attachments"] = self.attachments.get(item_id, [])
        return draft

    def reply_as_draft(self, *, change_key, **kwargs):
        self.reply_change_keys.append(change_key)
        item_id = "REPLY-DRAFT"
        self.drafts[item_id] = self._draft(item_id, "REPLY-CK", "RE: Test")
        return DraftResult(item_id=item_id, change_key="REPLY-CK", draft_type="reply")

    def forward_as_draft(self, *, change_key, to, **kwargs):
        self.forward_change_keys.append(change_key)
        item_id = "FORWARD-DRAFT"
        self.drafts[item_id] = self._draft(item_id, "FORWARD-CK", "FW: Test")
        return DraftResult(item_id=item_id, change_key="FORWARD-CK", to=to, draft_type="forward")

    def create_draft(self, *, to, subject, **kwargs):
        item_id = "NEW-DRAFT"
        self.drafts[item_id] = self._draft(item_id, "NEW-CK", subject)
        return DraftResult(item_id=item_id, change_key="NEW-CK", to=to, subject=subject)

    def attachment_roots(self):
        return [self.root]

    def add_attachment_to_draft(self, *, item_id, file_path, **kwargs):
        path = Path(file_path)
        self.attachments[item_id] = [{"name": path.name}]
        return AttachmentResult(
            attachment_id="ATTACHMENT",
            root_item_id=item_id,
            root_item_change_key="NEW-CK-2",
            filename=path.name,
            size=path.stat().st_size,
            content_type="text/plain",
        )

    @staticmethod
    def _draft(item_id: str, change_key: str, subject: str):
        return {
            "item_id": item_id,
            "change_key": change_key,
            "subject": subject,
            "body_html": "<p>draft</p>",
            "body_type": "HTML",
            "body_truncated": False,
            "is_draft": True,
            "attachments": [],
        }


def test_full_phase2_dt_runs_all_write_tests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    client = FakeClient(tmp_path / "attachments")
    report = run_phase2_integration_tests(
        client,  # type: ignore[arg-type]
        Phase2TestConfig(
            senders=["alice@example.com", "bob@example.com"],
            draft_recipient="self@example.com",
        ),
    )
    assert report["summary"]["status"] == "PASS"
    assert report["version"] == __version__
    names = [step["name"] for step in report["steps"]]
    assert "search_sender_1" in names
    assert "get_sender_message_2" in names
    assert "reply_as_draft_auto_changekey" in names
    assert "forward_as_draft_auto_changekey" in names
    assert "add_attachment_and_verify" in names
    assert client.reply_change_keys == [None]
    assert client.forward_change_keys == [None]
    assert len(report["created_drafts"]) == 3
    assert Path(report["report_path"]).exists()
    assert all("v0.6.2" not in str(step) for step in report["steps"])
    assert all("v0.6.3" not in str(step) for step in report["steps"])


def test_read_only_dt_creates_no_drafts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    client = FakeClient(tmp_path / "attachments")
    report = run_phase2_integration_tests(
        client,  # type: ignore[arg-type]
        Phase2TestConfig(
            senders=["alice@example.com"],
            draft_recipient="self@example.com",
        ),
        read_only=True,
    )
    assert report["summary"]["failed"] == 0
    assert report["created_drafts"] == []
    assert any(step["status"] == "SKIP" for step in report["steps"])

from exchange_ews_mcp.dt_runner import run_phase023_integration_tests
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.workflow_test_config import Phase023TestConfig


class FakePhase023Client:
    def __init__(self) -> None:
        self.drafts: dict[str, dict] = {}

    def test_connection(self) -> str:
        return "Inbox"

    def get_current_user(self):
        return {
            "status": "resolved",
            "source": "configured",
            "display_name": "Self User",
            "primary_email": "self@example.com",
        }

    def resolve_names(self, *, query: str, **kwargs):
        return {
            "returned": 1,
            "candidates": [
                {
                    "display_name": query,
                    "email": f"{query}@example.com",
                    "mailbox_type": "Mailbox",
                }
            ],
        }

    def search_emails(self, **kwargs):
        return {
            "returned": 1,
            "items": [
                {
                    "item_id": "SOURCE",
                    "change_key": "SOURCE-CK",
                    "subject": "Workflow Test",
                    "from": {"email": "alice@example.com"},
                    "display_to": "Self User",
                    "display_cc": "",
                    "conversation_id": "CONV",
                    "parent_folder_id": "INBOX",
                }
            ],
        }

    def search_emails_multi_folder(self, **kwargs):
        return {
            "folders": ["inbox", "sentitems"],
            "returned": 2,
            "items": [{"item_id": "A"}, {"item_id": "B"}],
            "per_folder": [
                {"folder": "inbox", "returned": 1},
                {"folder": "sentitems", "returned": 1},
            ],
        }

    def get_email(self, *, item_id: str, **kwargs):
        if item_id == "SOURCE":
            return {
                "item_id": "SOURCE",
                "change_key": "SOURCE-CK",
                "subject": "Workflow Test",
                "body_html": "<p>source</p>",
                "body_type": "HTML",
                "body_truncated": False,
                "is_draft": False,
                "attachments": [],
            }
        return self.drafts[item_id].copy()

    def create_draft(self, *, to, subject, body_html, **kwargs):
        self.drafts["DRAFT"] = {
            "item_id": "DRAFT",
            "change_key": "DRAFT-1",
            "subject": subject,
            "body_html": body_html,
            "body_type": "HTML",
            "body_truncated": False,
            "is_draft": True,
            "importance": "Normal",
            "attachments": [],
        }
        return DraftResult(item_id="DRAFT", change_key="DRAFT-1", subject=subject, to=to)

    def update_draft(self, *, item_id, subject, body_html, importance, **kwargs):
        self.drafts[item_id].update(
            {
                "change_key": "DRAFT-2",
                "subject": subject,
                "body_html": body_html,
                "importance": importance,
            }
        )
        return DraftResult(
            item_id=item_id,
            change_key="DRAFT-2",
            subject=subject,
            draft_type="updated",
        )


def test_v03_dt_covers_workflow_primitives(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    store = ReferenceStore(tmp_path / "state.db")
    report = run_phase023_integration_tests(
        FakePhase023Client(),  # type: ignore[arg-type]
        Phase023TestConfig(
            person_queries=["xiaoming", "xiaohong"],
            sender="alice@example.com",
            draft_recipient="self@example.com",
        ),
        store=store,
    )
    assert report["summary"]["status"] == "PASS"
    names = [step["name"] for step in report["steps"]]
    assert "get_current_user" in names
    assert "resolve_names_2" in names
    assert "enhanced_search_participant" in names
    assert "multi_folder_search" in names
    assert "message_ref_get_email" in names
    assert "action_session_store_roundtrip" in names
    assert "update_draft_by_ref" in names
    assert len(report["created_drafts"]) == 1
    assert Path(report["report_path"]).exists()

from exchange_ews_mcp.dt_config import DtTestConfig
from exchange_ews_mcp.dt_runner import run_dt_suite
import exchange_ews_mcp.dt_runner as dt_runner_module


def _fake_group_report(name: str, *, failed: int = 0):
    status = "FAIL" if failed else "PASS"
    return {
        "summary": {"status": status, "passed": 1 if not failed else 0, "failed": failed, "skipped": 0},
        "steps": [{"name": name, "status": status, "details": {}}] if not failed else [{"name": name, "status": "FAIL", "error": "boom"}],
        "created_drafts": [],
    }


def test_unified_dt_suite_groups_atomic_and_workflow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        dt_runner_module,
        "run_phase2_integration_tests",
        lambda *args, **kwargs: _fake_group_report("atomic_step"),
    )
    monkeypatch.setattr(
        dt_runner_module,
        "run_phase023_integration_tests",
        lambda *args, **kwargs: _fake_group_report("workflow_step"),
    )
    monkeypatch.setattr(
        dt_runner_module,
        "run_semantic_v04_integration_tests",
        lambda *args, **kwargs: _fake_group_report("semantic_step"),
    )
    monkeypatch.setattr(
        dt_runner_module,
        "run_calendar_v05_integration_tests",
        lambda *args, **kwargs: {**_fake_group_report("calendar_step"), "created_calendar_items": []},
    )
    monkeypatch.setattr(
        dt_runner_module,
        "run_weekly_report_v06_integration_tests",
        lambda *args, **kwargs: _fake_group_report("weekly_step"),
    )
    report = run_dt_suite(
        object(),  # type: ignore[arg-type]
        DtTestConfig(
            person_queries=["xiaoming"],
            senders=["alice@example.com"],
            draft_recipient="self@example.com",
        ),
        store=ReferenceStore(tmp_path / "unified-state.db"),
    )
    assert report["summary"]["status"] == "PASS"
    assert report["version"] == __version__
    assert [group["id"] for group in report["groups"]] == [
        "atomic", "workflow-v03", "semantic-mail-v04", "calendar-v05", "weekly-report-v06"
    ]
    assert Path(report["report_path"]).exists()


def test_unified_dt_suite_can_run_one_group(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        dt_runner_module,
        "run_phase2_integration_tests",
        lambda *args, **kwargs: _fake_group_report("atomic_step"),
    )
    workflow_called = False

    def workflow(*args, **kwargs):
        nonlocal workflow_called
        workflow_called = True
        return _fake_group_report("workflow_step")

    monkeypatch.setattr(dt_runner_module, "run_phase023_integration_tests", workflow)
    report = run_dt_suite(
        object(),  # type: ignore[arg-type]
        DtTestConfig(
            person_queries=["xiaoming"],
            senders=["alice@example.com"],
            draft_recipient="self@example.com",
        ),
        groups=["atomic"],
        store=ReferenceStore(tmp_path / "unified-state.db"),
    )
    assert [group["id"] for group in report["groups"]] == ["atomic"]
    assert workflow_called is False

from exchange_ews_mcp.dt_runner import run_calendar_v05_integration_tests
from exchange_ews_mcp.ews import CalendarItemResult
from exchange_ews_mcp.config import AppConfig


class FakeCalendarDtClient:
    def __init__(self) -> None:
        self.items = {}
        self.deleted = []

    def resolve_names(self, *, query: str, **kwargs):
        return {
            "status": "resolved", "returned": 1,
            "candidates": [{"display_name": "Self", "email": query, "mailbox_type": "Mailbox"}],
        }

    def get_user_availability(self, *, attendees, start, end, interval_minutes):
        return {
            "status": "success", "start": start, "end": end,
            "attendees": [
                {
                    **item,
                    "status": "success",
                    "response_code": "NoError",
                    "events": [],
                    "working_hours": {
                        "time_zone": {
                            "bias_minutes": 0,
                            "utc_offset": "+00:00",
                            "observes_daylight_saving": False,
                            "standard_transition": None,
                            "daylight_transition": None,
                            "standard_utc_offset": "+00:00",
                            "daylight_utc_offset": None,
                        },
                        "working_periods": [{
                            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                            "start_minutes": 540,
                            "end_minutes": 1080,
                            "start": "09:00",
                            "end": "18:00",
                        }],
                    },
                }
                for item in attendees
            ],
        }

    def list_calendar_events(self, **kwargs):
        return {"status": "success", "start": kwargs["start"], "end": kwargs["end"], "returned": 0, "items": []}

    def create_meeting(self, **kwargs):
        result = CalendarItemResult(
            item_id="DT-CAL", change_key="DT-CK", subject=kwargs["subject"],
            start=kwargs["start"], end=kwargs["end"],
            required_attendees=kwargs["required_attendees"],
            optional_attendees=kwargs["optional_attendees"],
            location=kwargs["location"], sent=kwargs["send_invitations"],
        )
        self.items[result.item_id] = result.as_dict()
        return result

    def get_calendar_item(self, *, item_id, **kwargs):
        return self.items[item_id]

    def delete_calendar_item(self, *, item_id, **kwargs):
        self.deleted.append(item_id)
        self.items.pop(item_id, None)
        return {"status": "deleted", "item_id": item_id}


def test_calendar_v05_dt_never_sends_and_cleans_up(tmp_path) -> None:
    client = FakeCalendarDtClient()
    report = run_calendar_v05_integration_tests(
        client,  # type: ignore[arg-type]
        DtTestConfig(person_queries=["xiaoming"], senders=["a@example.com"], draft_recipient="self@example.com").normalized(),
        app_config=AppConfig(
            ews_url="https://example.invalid/EWS/Exchange.asmx", username="dt",
            primary_email="self@example.com", company_email_domains=["example.com"],
            calendar_time_zone="UTC", calendar_workday_start="09:00", calendar_workday_end="18:00",
        ),
        read_only=False, store=ReferenceStore(tmp_path / "calendar-dt.db"), stamp="20260730T000000Z",
    )
    assert report["summary"]["status"] == "PASS"
    assert report["created_calendar_items"][0]["sent"] is False
    assert client.deleted == ["DT-CAL"]


from exchange_ews_mcp.dt_runner import run_weekly_report_v06_integration_tests


WEEKLY_DT_SEPARATOR = (
    "<p class=MsoNormal><span lang=EN-US style='font-family:等线'>"
    "<o:p>&nbsp;</o:p></span></p>"
)
WEEKLY_DT_BODY = (
    "<html><body><div class=WordSection1>"
    "<table><tr><td>日期：2026-07-27 至 2026-08-02</td></tr>"
    "<tr><td>完成旧任务</td></tr></table>"
    + WEEKLY_DT_SEPARATOR
    + "<div>WK2</div>"
    + WEEKLY_DT_SEPARATOR
    + "<div>WK1</div>"
    + "</div></body></html>"
)


class FakeWeeklyDtClient:
    def __init__(self) -> None:
        self.reply_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.draft_body: str | None = None
        self.draft_change_key = "DRAFT-CK1"

    def search_emails_multi_folder(self, **kwargs):
        return {
            "returned": 1,
            "items": [
                {
                    "item_id": "WEEKLY-SOURCE",
                    "change_key": "WEEKLY-CK",
                    "subject": "项目周报",
                    "folder": "sentitems",
                    "conversation_id": "WEEKLY-CONV",
                    "sent_at": "2026-08-03T01:00:00Z",
                }
            ],
            "per_folder": [],
        }

    def get_email(self, *, item_id, **kwargs):
        if item_id == "WEEKLY-SOURCE":
            return {
                "item_id": item_id,
                "change_key": "WEEKLY-CK",
                "subject": "项目周报",
                "folder": "sentitems",
                "conversation_id": "WEEKLY-CONV",
                "sent_at": "2026-08-03T01:00:00Z",
                "is_draft": False,
                "body_html": WEEKLY_DT_BODY,
                "body_type": "HTML",
                "body_truncated": False,
                "attachments": [],
            }
        assert item_id == "WEEKLY-DRAFT"
        return {
            "item_id": item_id,
            "change_key": self.draft_change_key,
            "subject": "RE: 项目周报",
            "folder": "drafts",
            "is_draft": True,
            "body_html": self.draft_body,
            "body_type": "HTML",
            "body_truncated": False,
            "attachments": [],
        }

    def reply_as_draft(self, **kwargs):
        self.reply_calls.append(kwargs)
        self.draft_body = kwargs["body_html"] + "<div>native history</div>"
        return DraftResult(
            item_id="WEEKLY-DRAFT",
            change_key=self.draft_change_key,
            subject="RE: 项目周报",
            draft_type="reply_all",
        )

    def update_draft(self, **kwargs):
        self.update_calls.append(kwargs)
        self.draft_change_key = "DRAFT-CK2"
        return DraftResult(
            item_id="WEEKLY-DRAFT",
            change_key=self.draft_change_key,
            subject=kwargs.get("subject") or "RE: 项目周报",
            draft_type="updated",
        )


def _weekly_dt_app_config() -> AppConfig:
    return AppConfig(
        ews_url="https://example.invalid/EWS/Exchange.asmx",
        username="dt",
        primary_email="self@example.com",
        company_email_domains=["example.com"],
    )


def test_weekly_report_v06_dt_read_only_validates_context(tmp_path) -> None:
    client = FakeWeeklyDtClient()
    report = run_weekly_report_v06_integration_tests(
        client,  # type: ignore[arg-type]
        DtTestConfig(
            person_queries=["xiaoming"],
            senders=["a@example.com"],
            draft_recipient="self@example.com",
            subject_contains="周报",
        ).normalized(),
        app_config=_weekly_dt_app_config(),
        read_only=True,
        store=ReferenceStore(tmp_path / "weekly-dt-read.db"),
        stamp="20260806T000000Z",
    )
    assert report["summary"]["status"] == "PASS"
    assert [step["name"] for step in report["steps"]] == [
        "weekly_report_context", "weekly_report_reply_all_draft"
    ]
    assert report["steps"][1]["status"] == "SKIP"
    assert client.reply_calls == []


def test_weekly_report_v06_dt_creates_unsent_reply_all_draft(tmp_path) -> None:
    client = FakeWeeklyDtClient()
    report = run_weekly_report_v06_integration_tests(
        client,  # type: ignore[arg-type]
        DtTestConfig(
            person_queries=["xiaoming"],
            senders=["a@example.com"],
            draft_recipient="self@example.com",
            subject_contains="周报",
        ).normalized(),
        app_config=_weekly_dt_app_config(),
        read_only=False,
        store=ReferenceStore(tmp_path / "weekly-dt-write.db"),
        stamp="20260806T000000Z",
    )
    assert report["summary"]["status"] == "PASS"
    assert len(report["created_drafts"]) == 1
    assert report["created_drafts"][0]["draft_type"] == "weekly_report_reply_all"
    assert client.reply_calls[0]["reply_all"] is True
    assert client.update_calls[0]["subject"] == "项目周报"


def test_weekly_report_v06_dt_skips_without_subject_filter(tmp_path) -> None:
    report = run_weekly_report_v06_integration_tests(
        FakeWeeklyDtClient(),  # type: ignore[arg-type]
        DtTestConfig(
            person_queries=["xiaoming"],
            senders=["a@example.com"],
            draft_recipient="self@example.com",
        ).normalized(),
        app_config=_weekly_dt_app_config(),
        read_only=False,
        store=ReferenceStore(tmp_path / "weekly-dt-skip.db"),
        stamp="20260806T000000Z",
    )
    assert report["summary"]["status"] == "PASS"
    assert report["summary"]["skipped"] == 1
    assert report["created_drafts"] == []
