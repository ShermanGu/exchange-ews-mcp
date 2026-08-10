# Agent tool surface — v0.7.0

Normal Agents use the compact **production** stdio server. The debug server is reserved for deterministic EWS troubleshooting and development tests.

## Production profile: 11 tools

### Mail

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `search_mail` | List or search inbox/sent mail, resolve people, and return stable `message_ref` values. | Read-only |
| `read_mail` | Read a message or draft through `message_ref`/`draft_ref`. | Read-only |
| `save_mail_draft` | Create, reply, Reply All, or forward according to `mode`. | Draft only |
| `edit_mail_draft` | Modify a draft and optionally append prevalidated local attachments. | Draft only |
| `continue_action` | Resume a pending person, message, time, or confirmation selection. | Depends on the stored action; never sends email |

`search_mail` replaces the old list/search/find split. With no filters it returns recent mail; people are resolved inside the workflow. `save_mail_draft` accepts `compose`, `reply`, `reply_all`, or `forward`. Reply and forward require a `source_message_ref`, so search and selection happen before any draft write.

### Weekly reports

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `get_weekly_report_context` | Mandatory first step for each new request. Returns compact text slots, full Agent instructions, history, and a single-use token. | Read-only Exchange access |
| `update_weekly_report` | Mandatory second step. Consumes the token, updates selected server-owned HTML text slots, validates structure, and creates a native Reply All draft. | Draft only |

The weekly-report two-step contract is unchanged from v0.6.16.

### Calendar

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `read_calendar` | List a time window or read one item through `calendar_ref`. | Read-only |
| `find_meeting_times` | Resolve attendees and return common free slots plus availability details. | Read-only |
| `save_meeting` | Create or update a meeting at an exact time. It always keeps invitations unsent. | `SendToNone` |
| `send_meeting_invitation` | Send an existing unsent meeting only after `confirm_send=true`. | `SendToAllAndSaveCopy` |

Use `find_meeting_times` before `save_meeting` when the user has not selected an exact start/end. `save_meeting` never sends. Sending remains a separate, explicit tool boundary.

## Debug-only tools

The debug server adds six low-level write primitives:

```text
resolve_names
create_draft
reply_as_draft
forward_as_draft
update_draft
create_meeting
```

They bypass parts of the compact semantic facade and should not be visible to normal Agents.

## Recommended routing

```text
List or find mail                   → search_mail
Read a selected message            → read_mail(message_ref=...)
Create a new draft                 → save_mail_draft(mode=compose)
Reply or Reply All                 → search_mail → save_mail_draft(mode=reply/reply_all)
Forward a message                  → search_mail → save_mail_draft(mode=forward)
Modify draft / add attachments     → edit_mail_draft
Resolve an ambiguity               → continue_action
Update a weekly report             → get_weekly_report_context → update_weekly_report
List or read calendar items        → read_calendar
Find common meeting time           → find_meeting_times
Create a meeting without sending   → save_meeting
Update an unsent meeting           → save_meeting(calendar_ref=...)
Send a saved meeting invitation    → send_meeting_invitation(calendar_ref=..., confirm_send=true)
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

It must compare all supplied history, rewrite conversational facts into formal report language, check inherited reporting-period dates, and call update with only:

```json
{
  "slot_id": "slot_0019_xxx",
  "new_text": "完成接口联调。"
}
```

Do not return HTML. Do not copy an old token. Do not write “no change” into a slot. Do not modify a header unless the user explicitly requests a header/date change.

## Inspect the active surface

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
.\.venv\Scripts\exchange-ews-mcp.exe tool-list --profile debug
```
