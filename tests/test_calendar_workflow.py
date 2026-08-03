from pathlib import Path

from exchange_ews_mcp.calendar_workflow import CalendarWorkflow
from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import CalendarItemResult
from exchange_ews_mcp.state_store import ReferenceStore


class FakeMailWorkflow:
    def resolve_people(self, *, query: str, **kwargs):
        if query == "ambiguous":
            return {
                "selection_status": "needs_confirmation", "selected": None,
                "candidates": [
                    {"email": "one@example.com", "person_ref": "person_one"},
                    {"email": "two@example.com", "person_ref": "person_two"},
                ],
            }
        email = query if "@" in query else f"{query}@example.com"
        return {
            "selection_status": "resolved", "selected": {"email": email, "person_ref": f"p_{query}"},
            "candidates": [{"email": email, "person_ref": f"p_{query}"}],
            "default_rule_applied": "exact_email" if "@" in query else None,
            "user_notice": None,
        }


class FakeCalendarClient:
    def __init__(self) -> None:
        self.created = []

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
            item_id="CAL1", change_key="CK1", subject=kwargs["subject"],
            start=kwargs["start"], end=kwargs["end"],
            required_attendees=kwargs["required_attendees"],
            optional_attendees=kwargs["optional_attendees"],
            location=kwargs.get("location"), sent=kwargs["send_invitations"],
        )


def config(time_zone: str = "UTC") -> AppConfig:
    return AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx", username="D\\u",
        primary_email="self@example.com", company_email_domains=["example.com"],
        calendar_time_zone=time_zone, calendar_workday_start="09:00", calendar_workday_end="18:00",
    )


def workflow(tmp_path: Path, time_zone: str = "UTC"):
    client = FakeCalendarClient()
    wf = CalendarWorkflow(
        client,  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"), config(time_zone), FakeMailWorkflow(),  # type: ignore[arg-type]
    )
    return wf, client


def test_find_common_times_uses_person_resolution(tmp_path: Path) -> None:
    wf, _ = workflow(tmp_path)
    result = wf.find_meeting_times(
        attendee_queries=["alice"], window_start="2026-08-03T09:00:00Z",
        window_end="2026-08-03T12:00:00Z", duration_minutes=60,
    )
    assert result["status"] == "resolved"
    assert result["returned"] == 5
    assert result["attendees"][1]["email"] == "alice@example.com"


def test_ambiguous_attendee_can_be_resumed(tmp_path: Path) -> None:
    wf, _ = workflow(tmp_path)
    pending = wf.find_meeting_times(
        attendee_queries=["ambiguous"], window_start="2026-08-03T09:00:00Z",
        window_end="2026-08-03T12:00:00Z",
    )
    assert pending["status"] == "needs_confirmation"
    result = wf.continue_action(
        resume_token=pending["resume_token"], selections={"ambiguous": "person_two"},
    )
    assert result["status"] == "resolved"
    assert result["attendees"][1]["email"] == "two@example.com"


def test_send_invitations_requires_second_confirmation(tmp_path: Path) -> None:
    wf, client = workflow(tmp_path)
    pending = wf.schedule_meeting(
        attendee_queries=["alice"], subject="Planning", body_html="<p>x</p>",
        start="2026-08-03T09:15:00Z", end="2026-08-03T10:15:00Z",
        send_invitations=True,
    )
    assert pending["confirmation_type"] == "send_invitations"
    assert client.created == []
    sent = wf.continue_action(
        resume_token=pending["resume_token"], selections={"confirm": "send"},
    )
    assert sent["status"] == "meeting_sent"
    assert client.created[0]["send_invitations"] is True


def test_schedule_defaults_to_unsent_meeting(tmp_path: Path) -> None:
    wf, client = workflow(tmp_path)
    result = wf.schedule_meeting(
        attendee_queries=["alice"], subject="Planning", body_html="<p>x</p>",
        start="2026-08-03T09:15:00Z", end="2026-08-03T10:15:00Z",
    )
    assert result["status"] == "meeting_saved_not_sent"
    assert result["calendar_item"]["calendar_ref"].startswith("cal_")
    assert client.created[0]["send_invitations"] is False


def test_optional_attendees_must_be_full_emails(tmp_path: Path) -> None:
    wf, _ = workflow(tmp_path)
    try:
        wf.schedule_meeting(
            attendee_queries=["alice"], optional_attendees=["bob"],
            subject="Planning", body_html="<p>x</p>",
            start="2026-08-03T09:00:00Z", end="2026-08-03T10:00:00Z",
        )
    except ValueError as exc:
        assert "完整邮箱" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_schedule_offset_free_time_uses_calendar_time_zone(tmp_path: Path) -> None:
    wf, client = workflow(tmp_path, "Asia/Shanghai")
    result = wf.schedule_meeting(
        attendee_queries=["alice"], subject="Planning", body_html="<p>x</p>",
        start="2026-08-03T09:15:00", end="2026-08-03T10:15:00",
    )
    assert result["status"] == "meeting_saved_not_sent"
    assert client.created[0]["start"] == "2026-08-03T01:15:00Z"
    assert client.created[0]["end"] == "2026-08-03T02:15:00Z"
    assert result["calendar_item"]["local_start"] == "2026-08-03T09:15:00+08:00"


def test_find_times_offset_free_window_uses_calendar_time_zone(tmp_path: Path) -> None:
    wf, client = workflow(tmp_path, "Asia/Shanghai")
    result = wf.find_meeting_times(
        attendee_queries=["alice"],
        window_start="2026-08-03T09:00:00",
        window_end="2026-08-03T12:00:00",
        duration_minutes=60,
    )
    assert result["status"] == "resolved"
    assert result["window_start"] == "2026-08-03T01:00:00Z"
    assert result["local_window_start"] == "2026-08-03T09:00:00+08:00"
