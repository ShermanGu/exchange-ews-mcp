# Agent tool surface — v0.6.14

Normal Agents must use the **production** stdio server. The debug server is reserved for deterministic EWS troubleshooting and development tests.

## Production profile: 19 tools

### Identity and mail reads

| Tool | Purpose |
| --- | --- |
| `get_current_user` | Return the configured mailbox identity and `person_ref`. |
| `list_emails` | List message summaries from supported standard folders. |
| `search_emails` | Search messages by folders, people, subject, time, attachment state, and related filters. |
| `get_email` | Read a message body, recipients, and attachment metadata. |
| `find_email` | High-level message resolution that returns a `message_ref` or confirmation candidates. |
| `resolve_people` | Resolve full emails or supported name queries with mail-history disambiguation. |

### Draft workflows

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `compose_email` | Resolve recipients and create a new HTML draft. | Draft only |
| `reply_to_email` | Resolve a source message and create a reply or Reply All draft. | Draft only |
| `forward_email` | Resolve source and recipients and create a forward draft. | Draft only |
| `update_email_draft` | Modify an existing draft through `draft_ref`. | Draft only |
| `add_attachment_to_draft` | Add an allow-listed local file to a draft. | Draft only |
| `continue_action` | Resume a candidate choice, time choice, or send confirmation. | Depends on pending action |

### Weekly reports

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `get_weekly_report_context` | Mandatory first step for each new weekly-report request. Returns up to five historical sections, compact text slots, a full Agent prompt, and one single-use token. | Read-only Exchange access |
| `update_weekly_report` | Mandatory second step. Consumes the token, fills selected text slots into server-owned HTML, validates structure, and creates a native Reply All draft. | Draft only |

### Calendar

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `get_user_availability` | Query attendee/resource availability and EWS WorkingHours. | Read-only |
| `list_calendar_events` | List current-user calendar items in a time window. | Read-only |
| `get_calendar_item` | Read an item through `calendar_ref`. | Read-only |
| `find_meeting_times` | Resolve attendees and find common slots within working hours. | Read-only |
| `schedule_meeting` | Create an unsent meeting or send only after explicit confirmation. | Default `SendToNone` |

## Debug-only tools

The debug server adds six low-level primitives:

```text
resolve_names
create_draft
reply_as_draft
forward_as_draft
update_draft
create_meeting
```

They duplicate safer high-level workflows and should not be visible to normal Agents.

## Recommended routing

```text
Read recent mail                    → list_emails / search_emails / get_email
Resolve a person                    → resolve_people
Create a new message                → compose_email
Reply to a known message            → reply_to_email(message_ref=...)
Find then reply                     → find_email → reply_to_email
Forward a message                   → forward_email
Resolve an ambiguity                → continue_action
Modify an existing draft            → update_email_draft
Add an attachment                   → add_attachment_to_draft
Update a weekly report              → get_weekly_report_context → update_weekly_report
Find common meeting time            → find_meeting_times
Create meeting without sending      → schedule_meeting(send_invitations=false)
Send invitations                    → schedule_meeting → continue_action(confirm=send)
```

## Weekly-report routing contract

For every new user update, the Agent must call `get_weekly_report_context`, even when an older token is visible in the current conversation.

The Agent receives compact slots:

```json
{
  "slot_id": "slot_0019_xxx",
  "text": "完成接口开发",
  "location": "行表头：项目A；列表头：工作内容 / 本周进展"
}
```

It must compare all supplied history, rewrite the user's conversational facts into formal report language, check all inherited reporting-period dates, and call update with only:

```json
{
  "slot_id": "slot_0019_xxx",
  "new_text": "完成接口联调。"
}
```

Do not return HTML. Do not copy an old token. Do not write “no change” into a slot. Do not modify a header unless the user explicitly requests a header/date change.

## Folder precedence

For tools that accept both `folder` and `folders`, a non-empty `folders` list overrides `folder`. Use supported canonical folder values documented by the tool schema.

## Inspect the active surface

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
.\.venv\Scripts\exchange-ews-mcp.exe tool-list --profile debug
```
