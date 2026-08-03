# Agent tool surface — v0.7.2

## Production profile: 18 tools

### Identity and mail reads

- `get_current_user`
- `list_emails`
- `search_emails`
- `get_email`
- `find_email`
- `resolve_people`

### Draft workflows

- `extract_email_template`: read and extract one reusable template; never writes.
- `compose_email`: create a new HTML draft; optional `template_ref` supplies the stored format and referenced resources.
- `reply_to_email`: create a reply/reply-all draft; `message_ref` selects the target while optional `template_ref` independently supplies format and resources.
- `forward_email`
- `update_email_draft`
- `add_attachment_to_draft`
- `continue_action`

### Calendar

- `get_user_availability`
- `list_calendar_events`
- `get_calendar_item`
- `find_meeting_times`
- `schedule_meeting`

## Removed overlapping tools

The following are intentionally absent:

```text
compose_from_email
reply_from_email
```

The Agent should not choose between multiple tools that both create the same type of draft. It should extract first only when formatting reuse is requested, then call the normal writer.

## Recommended routing

```text
New mail, no template      → compose_email
Reply, no template         → reply_to_email
Need previous formatting   → extract_email_template
                              then compose_email or reply_to_email
Ambiguous person/message   → continue_action
```

## Debug-only tools

The debug server additionally exposes:

```text
resolve_names
create_draft
reply_as_draft
forward_as_draft
update_draft
create_meeting
```

## v0.7.2 template routing

For a large template, never rebuild the message from `template_preview_html`. Use the returned `template_ref` and pass only the new content through `body_html` to `compose_email` or `reply_to_email`. The server renders against the complete stored template.

Without `template_ref`, `body_html` is complete HTML. With `template_ref`, it is only the new content fragment. There is no second template-specific body parameter.
