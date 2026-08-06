from exchange_ews_mcp.cli import build_parser
from exchange_ews_mcp.dt_config import VALID_GROUPS
from exchange_ews_mcp.tool_profiles import DEBUG_ONLY_TOOL_NAMES, PRODUCTION_TOOL_NAMES, tool_names


def test_cli_registers_calendar_and_tool_profile_commands() -> None:
    parser = build_parser()
    sub = next(action for action in parser._actions if getattr(action, "choices", None))
    assert len(sub.choices) == 39
    for command in (
        "set-calendar-preferences", "availability", "calendar-list", "calendar-get",
        "create-meeting", "find-meeting-times", "schedule-meeting", "tool-list",
    ):
        assert command in sub.choices


def test_production_server_exposes_curated_19_tools() -> None:
    names = tool_names()
    assert len(names) == 19
    assert names == PRODUCTION_TOOL_NAMES
    assert {
        "get_current_user", "resolve_people", "compose_email", "find_email",
        "reply_to_email", "get_weekly_report_context", "update_weekly_report",
        "forward_email", "continue_action", "update_email_draft",
        "get_user_availability", "list_calendar_events", "get_calendar_item",
        "find_meeting_times", "schedule_meeting",
    } <= set(names)
    assert {
        "resolve_names", "create_draft", "reply_as_draft", "forward_as_draft",
        "update_draft", "create_meeting",
    }.isdisjoint(names)


def test_debug_server_retains_all_25_tools() -> None:
    names = tool_names(include_debug_tools=True)
    assert len(PRODUCTION_TOOL_NAMES) == 19
    assert len(DEBUG_ONLY_TOOL_NAMES) == 6
    assert len(names) == 25
    assert names == PRODUCTION_TOOL_NAMES + DEBUG_ONLY_TOOL_NAMES


def test_dt_has_calendar_and_weekly_groups() -> None:
    assert "calendar-v05" in VALID_GROUPS
    assert VALID_GROUPS[-1] == "weekly-report-v06"
