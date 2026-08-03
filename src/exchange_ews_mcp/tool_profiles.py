from __future__ import annotations

# Production profile: the tools an Agent should normally see. It keeps the
# semantic workflow tools plus read-only inspection primitives, while hiding
# duplicate low-level write operations that are easy for an Agent to misuse.
PRODUCTION_TOOL_NAMES: tuple[str, ...] = (
    "get_current_user",
    "list_emails",
    "search_emails",
    "get_email",
    "add_attachment_to_draft",
    "resolve_people",
    "compose_email",
    "extract_email_template",
    "find_email",
    "reply_to_email",
    "forward_email",
    "continue_action",
    "update_email_draft",
    "get_user_availability",
    "list_calendar_events",
    "get_calendar_item",
    "find_meeting_times",
    "schedule_meeting",
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
