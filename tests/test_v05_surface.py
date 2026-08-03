from exchange_ews_mcp.cli import build_parser
from exchange_ews_mcp.dt_config import VALID_GROUPS
from exchange_ews_mcp.tool_profiles import DEBUG_ONLY_TOOL_NAMES, PRODUCTION_TOOL_NAMES, tool_names


def test_cli_registers_calendar_and_tool_profile_commands() -> None:
    parser = build_parser()
    sub = next(action for action in parser._actions if getattr(action, "choices", None))
    assert len(sub.choices) == 40
    for command in (
        "set-calendar-preferences", "availability", "calendar-list", "calendar-get",
        "create-meeting", "find-meeting-times", "schedule-meeting",
        "extract-email-template", "compose-email", "reply-email", "tool-list",
    ):
        assert command in sub.choices
    assert "compose-from-email" not in sub.choices
    assert "reply-from-email" not in sub.choices


def test_production_server_exposes_curated_18_tools() -> None:
    names = tool_names()
    assert len(names) == 18
    assert names == PRODUCTION_TOOL_NAMES
    assert {
        "get_current_user", "resolve_people", "compose_email", "extract_email_template",
        "find_email", "reply_to_email", "forward_email", "continue_action",
        "update_email_draft", "get_user_availability", "list_calendar_events",
        "get_calendar_item", "find_meeting_times", "schedule_meeting",
    } <= set(names)
    assert {"compose_from_email", "reply_from_email"}.isdisjoint(names)
    assert {
        "resolve_names", "create_draft", "reply_as_draft", "forward_as_draft",
        "update_draft", "create_meeting",
    }.isdisjoint(names)


def test_debug_server_retains_all_24_tools() -> None:
    names = tool_names(include_debug_tools=True)
    assert len(PRODUCTION_TOOL_NAMES) == 18
    assert len(DEBUG_ONLY_TOOL_NAMES) == 6
    assert len(names) == 24
    assert names == PRODUCTION_TOOL_NAMES + DEBUG_ONLY_TOOL_NAMES


def test_dt_keeps_template_group_compatibility() -> None:
    assert VALID_GROUPS[-1] == "template-mail-v06"


def test_extract_template_and_existing_writers_cli_parse() -> None:
    parser = build_parser()
    extracted = parser.parse_args([
        "extract-email-template", "--message-ref", "msg_x", "--folders", "Sent Items",
    ])
    assert extracted.message_ref == "msg_x"
    compose = parser.parse_args([
        "compose-email", "--to", "a@example.com", "--subject", "s",
        "--html", "<p>x</p>", "--template-ref", "tmpl_x",
    ])
    assert compose.template_ref == "tmpl_x"
    reply = parser.parse_args([
        "reply-email", "--message-ref", "msg_x", "--html", "<p>new</p>",
        "--template-ref", "tmpl_x",
    ])
    assert reply.template_ref == "tmpl_x"
    assert reply.reply_all is False
