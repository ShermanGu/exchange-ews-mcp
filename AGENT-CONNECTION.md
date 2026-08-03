# Connect an Agent to Exchange EWS MCP v0.7.2

## Install

```powershell
cd D:\tools\exchange-ews-mcp
.\install.cmd
.\.venv\Scripts\exchange-ews-mcp.exe version
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
```

Expected:

```text
version = 0.7.2
visible_tool_count = 18
```

Existing credentials and configuration remain in the Windows user profile. Do not run `reset-local` during an upgrade.

## stdio MCP settings

```text
Name: exchange-ews
Transport: stdio
Command: D:\tools\exchange-ews-mcp\.venv\Scripts\exchange-ews-mcp-server.exe
Arguments: empty
Environment variables: empty
```

Completely exit and restart the Agent after changing the command.

Do not use `exchange-ews-mcp-debug-server.exe` for normal Agent operation.

## JSON form

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe mcp-config
```

Equivalent configuration:

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

## Template scenario

User request:

> 按照我上周发的周报格式创建本周周报，收件人和 CC 不变。

Recommended Agent sequence:

```text
find_email / extract_email_template
→ if ambiguous: continue_action
→ use suggested_compose_inputs; do not reconstruct a large template preview
→ generate only the new content fragment in body_html
→ compose_email(to/cc/subject/body_html/template_ref)
```

Reply request:

```text
extract_email_template(template source)
→ generate only the new reply fragment in body_html
→ reply_to_email(reply target message_ref, body_html, reply_all, template_ref)
```

When `template_ref` is absent, `body_html` is complete HTML. When it is present, `body_html` is new content rendered into the complete locally stored template.
