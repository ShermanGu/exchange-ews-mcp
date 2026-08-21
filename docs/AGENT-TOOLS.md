# Agent tool surface — v0.9.0

Normal Agents use the compact production stdio server. The debug server is reserved for deterministic EWS troubleshooting and DT.

## Production profile: 11 tools

| Tool | Purpose | Write behavior |
| --- | --- | --- |
| `search_mail` | List/search mail and return typed opaque refs. | Read-only |
| `read_mail` | Read a message or draft by opaque ref. | Read-only |
| `resolve_people` | Resolve a name/email independently with ambiguity handling. | Read-only |
| `save_mail_draft` | Compose, reply, Reply All, or forward by `mode`. | Draft only |
| `edit_mail_draft` | Edit a draft and append validated attachments. | Draft only |
| `continue_action` | Resume workflow-owned ambiguity/confirmation and submit weekly-report slot changes. | Stored-action dependent; never sends mail |
| `weekly_report` | The only weekly-report entry. Returns compact short-ID slots + previous-two-week history + a context-bound `resume_token`; creates no draft. | Read-only |
| `read_calendar` | List a time window or read one calendar item. | Read-only |
| `find_meeting_times` | Resolve attendees and find common free time. | Read-only |
| `save_meeting` | Create/update an unsent meeting. | `SendToNone` |
| `send_meeting_invitation` | Send a saved meeting only with explicit confirmation. | Sends invitation |

The debug profile adds six low-level primitives: `resolve_names`, `create_draft`, `reply_as_draft`, `forward_as_draft`, `update_draft`, and `create_meeting`, for 17 visible tools total.

## Recommended routing

```text
Mail search/read                  → search_mail / read_mail
Independent identity resolution  → resolve_people
Mail draft create/reply/forward  → save_mail_draft
Draft edit/attachments           → edit_mail_draft
Workflow continuation            → continue_action
Weekly report (direct)           → weekly_report(request=...) → continue_action
Weekly report (from other mail)  → search_mail/read_mail → LLM summary → weekly_report(request=...) → continue_action
Calendar read                    → read_calendar
Find meeting time                → find_meeting_times
Create/update unsent meeting     → save_meeting
Send saved invitation            → send_meeting_invitation
```

## Weekly-report contract

For a direct weekly-report update, call `weekly_report(request=...)`. If the user first wants other people's reports summarized, use `search_mail`/`read_mail`, summarize those messages with the LLM, then call `weekly_report` with that summary as `request`. Do not pass the original meta-instruction to search/summarize as the weekly `request`. The tool reads three weeks total and returns compact Agent-safe JSON only: `resume_token`, server-selected `mode`, the server-selected default draft `subject`, user `request`, fixed routing `instructions`, short-ID `slots`, and the previous two weeks as `history`. HTML and internal slot identity never leave the server.

Slot shape:

```json
{"id":"s7","text":"完成接口开发","loc":"周报（纵向表头） = nl2sql项目（横向表头） > 项目进展（二级纵向表头）"}
```

`loc` is optional and advisory only. Every text node inside `loc` carries its explicit table position/level in parentheses. Example: `周报（纵向表头） = nl2sql项目（横向表头） > 项目进展（二级纵向表头）`. `=` joins the primary vertical/column header and primary horizontal/row header at the content intersection; `>` continues into a more specific header level. The Agent should understand the real header text before the parentheses and use the parenthesized axis/level to understand hierarchy and disambiguate. `slot.text` is the actual editable content at the end of that structural path. The server does not emit a semantic `role` or duplicate `context` object. The actual write mapping remains server-side. IDs are local to the current `resume_token`; copy them exactly and never invent them.

After deciding changes, call:

```text
continue_action(
  resume_token=<this weekly_report token>,
  selections={
    "changes": [{"id": "s7", "text": "完成接口联调"}]
  }
)
```

Only changed slots should be submitted. Rewrite current facts into concise formal report language, preserve established project/technical terminology, never invent facts, and review all inherited report-period dates/week markers in the body. The server automatically rolls supported Subject date/week markers forward by one week and returns that default draft Subject from `weekly_report`; pass `selections.subject` only when the user explicitly wants a different Subject. Local `id/text/subject` validation errors reuse the same token; only stale/expired/used/superseded contexts require another `weekly_report`.

The server automatically chooses `reply_all` when the latest source contains a visible quoted-history sender header (`发件人` or standalone `From`), otherwise `compose`. Compose copies the source To/CC.
