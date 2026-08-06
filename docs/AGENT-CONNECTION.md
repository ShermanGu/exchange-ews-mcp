# Connect an Agent to Exchange EWS MCP v0.6.14

## 1. Install and verify

```powershell
cd D:\tools\exchange-ews-mcp
.\install.cmd
.\.venv\Scripts\exchange-ews-mcp.exe version
.\.venv\Scripts\exchange-ews-mcp.exe status
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
```

Expected:

```text
version = 0.6.14
visible_tool_count = 19
```

The reported package and Python paths must be inside the current repository's `.venv`.

Existing configuration and credentials are stored in the Windows user profile. Do not run `reset-local` during an upgrade unless you intentionally want to delete configuration, DT settings, local state, and the saved password.

## 2. Production stdio settings

```text
Name: exchange-ews
Transport: stdio
Command: D:\tools\exchange-ews-mcp\.venv\Scripts\exchange-ews-mcp-server.exe
Arguments: empty
Environment variables: empty
```

Completely exit and restart the MCP client after changing the command.

Do not point a normal Agent at:

```text
exchange-ews-mcp-debug-server.exe
```

The debug server exposes low-level write primitives that intentionally bypass some semantic routing.

## 3. JSON configuration

Generate the exact configuration for the active virtual environment:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe mcp-config
```

Equivalent form:

```json
{
  "mcpServers": {
    "exchange-ews": {
      "command": "D:\\tools\\exchange-ews-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "exchange_ews_mcp.server"]
    }
  }
}
```

## 4. Expected production tools

The Agent should see exactly these 19 tools:

```text
get_current_user
list_emails
search_emails
get_email
add_attachment_to_draft
resolve_people
compose_email
find_email
reply_to_email
get_weekly_report_context
update_weekly_report
forward_email
continue_action
update_email_draft
get_user_availability
list_calendar_events
get_calendar_item
find_meeting_times
schedule_meeting
```

If the list is stale or incomplete:

1. verify that the command points to this repository's `.venv`;
2. run `version` and `tool-list` from the same directory;
3. completely terminate the MCP client, not only the current conversation;
4. confirm that the production server is selected;
5. remove obsolete MCP entries that point to older folders or a global Python installation.

## 5. Recommended acceptance prompts

Run each scenario separately and review the resulting draft or confirmation state.

1. `列出我最近 5 封未读邮件。`
2. `给 xiaoming 写一封测试邮件，只创建草稿。`
3. `找我上周发送的周报。`
4. `项目A完成联调，项目B没变化，下周项目A做性能测试。`
5. `查我和 xiaoming 下周工作时间内共同空闲的一小时。`
6. `按第一个时间创建会议，但不要发送邀请。`
7. `发送刚才的会议邀请。`

Expected safety behavior:

- mail operations create drafts and report `sent=false`;
- the weekly-report request calls `get_weekly_report_context` before `update_weekly_report`;
- “项目B没变化” does not create a “no change” slot edit;
- reporting-period dates are reviewed and updated;
- the meeting send request pauses for explicit confirmation;
- only the confirmed meeting action sends invitations.

## 6. Weekly-report Agent requirements

- Call `get_weekly_report_context` for every new user request.
- Use only the token and slot IDs returned by that call.
- Compare every historical section included in the prompt.
- Rewrite conversational input into concise formal report language.
- Use `location` to distinguish project row, current-week progress, next-week plan, risk, and header/date slots.
- Check every date-like slot before update.
- Submit only `slot_id` and `new_text`.
- Never generate or return weekly-report HTML.

## 7. General Agent behavior

- Person queries should use a full email or the supported transliterated/name form; ask the user when the query is insufficient.
- When two or more plausible candidates have meaningful mail history, ask the user to choose.
- Mail writes must remain draft-only.
- Meeting invitations require explicit confirmation.
- Display local time fields to the user where available; preserve UTC fields for machine semantics.
- Do not expose credentials, raw Exchange IDs, internal EWS URLs, or mailbox content in logs.
