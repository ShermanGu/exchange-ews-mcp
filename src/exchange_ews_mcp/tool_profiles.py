from __future__ import annotations

# Production profile: a compact Agent-facing facade.  Low-level and overlapping
# mail/calendar primitives remain available to the CLI and internal workflows,
# but are deliberately not registered as MCP tools.
PRODUCTION_TOOL_NAMES: tuple[str, ...] = (
    "search_mail",
    "read_mail",
    "save_mail_draft",
    "edit_mail_draft",
    "continue_action",
    "get_weekly_report_context",
    "update_weekly_report",
    "read_calendar",
    "find_meeting_times",
    "save_meeting",
    "send_meeting_invitation",
)

# Debug profile only: deterministic EWS write primitives retained for CLI, DT,
# development, and protocol troubleshooting. They duplicate safer workflow
# tools and therefore are not exposed by the default Agent server.
DEBUG_ONLY_TOOL_NAMES: tuple[str, ...] = (
    "resolve_names",
    "create_draft",
    "reply_as_draft",
    "forward_as_draft",
    "update_draft",
    "create_meeting",
)


def tool_names(*, include_debug_tools: bool = False) -> tuple[str, ...]:
    """Return the stable ordered tool surface for the selected server profile."""
    if include_debug_tools:
        return PRODUCTION_TOOL_NAMES + DEBUG_ONLY_TOOL_NAMES
    return PRODUCTION_TOOL_NAMES
