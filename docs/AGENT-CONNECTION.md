# Connect an Agent to Exchange EWS MCP v0.9.0

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
version = 0.9.0
visible_tool_count = 11
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

The Agent should see exactly these 11 tools:

```text
search_mail
read_mail
resolve_people
save_mail_draft
edit_mail_draft
continue_action
weekly_report
read_calendar
find_meeting_times
save_meeting
send_meeting_invitation
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
7. `把刚才会议的正文改成“请提前准备当前进展和风险”，仍然不要发送。`
8. `发送刚才的会议邀请。`

Expected safety behavior:

- mail operations create drafts and report `sent=false`;
- the weekly-report request calls `weekly_report` and submits changes only through `continue_action`;
- “项目B没变化” does not create a “no change” slot edit;
- reporting-period dates are reviewed and updated;
- choosing save/no at the meeting send decision creates an unsent calendar item instead of returning another pending confirmation;
- `save_meeting` creates or changes a meeting without notifying attendees;
- `send_meeting_invitation` rejects calls without explicit confirmation and rejects already-sent meetings.

## 6. Weekly-report Agent requirements

- For direct weekly updates, pass the user facts as `weekly_report(request=...)`. For aggregation requests, first `search_mail`/`read_mail`, summarize with the LLM, then pass that summary as `request`.
- Use only the token and short `sN` IDs returned by that call; never invent IDs.
- Compare the current `slots` with the previous two weeks in `history`.
- Rewrite conversational input into concise formal report language.
- Use optional `loc` only to understand project row/current-week/next-week/risk/header semantics.
- Check every date-like slot before update.
- Submit only compact `id` and `text` fields inside weekly `changes[]`.
- Never generate or return weekly-report HTML.

## 7. General Agent behavior

- Person queries should use a full email or the supported transliterated/name form; ask the user when the query is insufficient.
- When two or more plausible candidates have meaningful mail history, ask the user to choose.
- Mail writes must remain draft-only.
- Meeting invitations require explicit confirmation.
- Display local time fields to the user where available; preserve UTC fields for machine semantics.
- Do not expose credentials, raw Exchange IDs, internal EWS URLs, or mailbox content in logs.
