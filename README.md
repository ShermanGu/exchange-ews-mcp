# Exchange EWS MCP v0.7.2

Local Windows stdio MCP for on-premises Microsoft Exchange through EWS + NTLM.
Credentials stay in Windows Credential Manager. Mail writes create drafts by default; meeting invitations require explicit confirmation.

## v0.7 architecture change

Email formatting is now fully decoupled from mail creation and reply actions:

```text
extract_email_template
        ↓ compact preview + complete local template_ref
Agent generates only the new content in body_html
        ↓
compose_email OR reply_to_email renders the stored template
```

The former `compose_from_email` and `reply_from_email` tools were removed from the MCP surface. This avoids overlapping choices for the Agent.

`extract_email_template` does not create a draft. It:

- locates one source message by `message_ref` or structured search;
- if the selected HTML contains multiple quoted replies, keeps only the top/first visible message;
- if the selected HTML contains only one message, keeps that complete message;
- stores the complete template and marker-based shell behind `template_ref`, returning compact previews and exact character counts when HTML is large;
- records source attachment metadata so existing write tools can copy only the rendered HTML's referenced `cid:` images.

## Production MCP tools

The production server exposes 18 tools:

```text
get_current_user
list_emails
search_emails
get_email
add_attachment_to_draft
resolve_people
compose_email
extract_email_template
find_email
reply_to_email
forward_email
continue_action
update_email_draft
get_user_availability
list_calendar_events
get_calendar_item
find_meeting_times
schedule_meeting
```

The debug server adds six lower-level EWS write primitives.

## Typical template workflow

1. Find or directly select the source email.
2. Call `extract_email_template`.
3. Generate only the new content fragment; do not reconstruct the complete stored template.
4. For a new email, call `compose_email` with that fragment in `body_html` plus `template_ref`.
5. For a reply, call `reply_to_email` with the reply target's `message_ref`, the new reply fragment in `body_html`, and `template_ref`.

Without `template_ref`, `body_html` remains the complete HTML body. With `template_ref`, `body_html` is the new content fragment inserted into the stored template.

The reply target and the template source are independent.

## CLI examples

Extract a template:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe extract-email-template `
  --folders "Sent Items" `
  --subject-contains "周报" `
  --after "2026-07-20T00:00:00+08:00" `
  --before "2026-07-27T00:00:00+08:00"
```

Create a new draft from a new-content fragment and the stored template:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe compose-email `
  --to "a@company.com" `
  --cc "b@company.com" `
  --subject "本周项目周报" `
  --html-file ".\weekly-report-content.html" `
  --template-ref "tmpl_xxx"
```

Create a reply draft using the same template:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe reply-email `
  --message-ref "msg_reply_target" `
  --reply-all `
  --html-file ".\reply-content.html" `
  --template-ref "tmpl_xxx"
```

Normal source attachments are not copied unless `--copy-template-attachments` is explicitly supplied. BCC is never suggested by template extraction.

## Install

```powershell
cd D:\tools\exchange-ews-mcp
.\install.cmd
.\.venv\Scripts\exchange-ews-mcp.exe version
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
```

Existing local configuration can be reused. Do not run `reset-local` when upgrading.

See:

- `TEMPLATE-ARCHITECTURE.zh-CN.md`
- `TEMPLATE-EXTRACTION.md`
- `AGENT-TOOLS.md`
- `AGENT-CONNECTION.md`
- `DT.md`
- `RELEASE-AUDIT.md`

## v0.7.2 single-body template contract

`extract_email_template` now prefers EWS `UniqueBody`, which represents the body unique to the selected conversation item. The complete template is stored in `template_ref`; large HTML is not copied into the MCP tool result.

Recommended calls:

```text
extract_email_template(...) -> template_ref
compose_email(..., template_ref=template_ref, body_html="<p>new content</p>")
reply_to_email(..., template_ref=template_ref, body_html="<p>new reply</p>")
```

`template_content_html` is no longer public. `body_html` is the only body parameter, eliminating mutually exclusive Agent inputs and making CLI and MCP behavior identical.
