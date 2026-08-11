from exchange_ews_mcp.cli import build_parser
from exchange_ews_mcp.dt_config import VALID_GROUPS
from exchange_ews_mcp.tool_profiles import DEBUG_ONLY_TOOL_NAMES, PRODUCTION_TOOL_NAMES, tool_names


def test_cli_registers_calendar_and_tool_profile_commands() -> None:
    parser = build_parser()
    sub = next(action for action in parser._actions if getattr(action, "choices", None))
    assert len(sub.choices) == 41
    for command in (
        "set-calendar-preferences", "availability", "calendar-list", "calendar-get",
        "update-meeting", "send-meeting-invitation", "create-meeting",
        "find-meeting-times", "schedule-meeting", "tool-list",
    ):
        assert command in sub.choices


def test_production_server_exposes_compact_11_tools() -> None:
    names = tool_names()
    assert len(names) == 11
    assert names == PRODUCTION_TOOL_NAMES
    assert {
        "search_mail", "read_mail", "resolve_people", "save_mail_draft", "edit_mail_draft",
        "continue_action", "weekly_report",
        "read_calendar", "find_meeting_times", "save_meeting",
        "send_meeting_invitation",
    } <= set(names)
    assert {
        "resolve_names", "create_draft", "reply_as_draft", "forward_as_draft",
        "update_draft", "create_meeting", "get_current_user",
        "compose_email", "find_email", "update_meeting", "schedule_meeting",
        "get_weekly_report_context", "update_weekly_report",
    }.isdisjoint(names)


def test_debug_server_adds_six_atomic_tools() -> None:
    names = tool_names(include_debug_tools=True)
    assert len(PRODUCTION_TOOL_NAMES) == 11
    assert len(DEBUG_ONLY_TOOL_NAMES) == 6
    assert len(names) == 17
    assert names == PRODUCTION_TOOL_NAMES + DEBUG_ONLY_TOOL_NAMES


def test_dt_has_calendar_and_weekly_groups() -> None:
    assert "calendar-v05" in VALID_GROUPS
    assert VALID_GROUPS[-1] == "weekly-report-v06"
