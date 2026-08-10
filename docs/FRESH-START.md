# Fresh install and upgrade — v0.7.0

## Clean installation

```powershell
git clone https://github.com/ShermanGu/exchange-ews-mcp.git
cd exchange-ews-mcp
.\install.cmd
```

Verify:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe version
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
```

Expected:

```text
version = 0.7.0
visible_tool_count = 11
```

Configure the mailbox:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe configure
.\.venv\Scripts\exchange-ews-mcp.exe set-current-user `
  --email "you@company.example" `
  --display-name "Your Name"
.\.venv\Scripts\exchange-ews-mcp.exe status
.\.venv\Scripts\exchange-ews-mcp.exe test
```

Configure allowed attachment roots and calendar preferences as needed.

## Upgrade from an older checkout

1. Keep the existing Windows user profile and Credential Manager entries.
2. Replace or update the repository files.
3. Run `install.cmd` from the new repository.
4. Confirm both module and metadata versions are `0.7.0`.
5. Regenerate the MCP configuration.
6. Completely restart the MCP client.
7. Verify that 11 production tools are visible.

Do not run `reset-local` during a normal upgrade. It removes configuration, DT configuration, the local reference database, and the stored password.

## Recommended upgrade verification

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe version
.\.venv\Scripts\exchange-ews-mcp.exe status
.\.venv\Scripts\exchange-ews-mcp.exe test
.\.venv\Scripts\exchange-ews-mcp.exe tool-list
.\.venv\Scripts\exchange-ews-mcp.exe mcp-config
```

Then run:

```powershell
.\run-unit-tests.cmd
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --read-only
```

Run full DT after read-only DT passes.

## Production MCP entry

```text
Command: D:\tools\exchange-ews-mcp\.venv\Scripts\exchange-ews-mcp-server.exe
Arguments: empty
Transport: stdio
```

The debug entry is only for troubleshooting:

```text
D:\tools\exchange-ews-mcp\.venv\Scripts\exchange-ews-mcp-debug-server.exe
```

## Weekly-report acceptance check

Use a short human-style request such as:

```text
项目A完成联调，项目B没变化，下周项目A做性能测试。
```

Confirm:

- the Agent calls `get_weekly_report_context` first;
- the returned slots are compact;
- the Agent uses the complete prompt and location strings;
- project B is not changed;
- reporting-period dates are updated;
- the second call uses the new one-time token;
- the result is one unsent Reply All draft;
- table structure, styles, images, and native history remain intact.

## Recovery

### Wrong package path

Delete `.venv` and rerun `install.cmd` from the intended repository.

### Stale Agent tools

Remove old MCP configuration entries, regenerate `mcp-config`, and completely restart the client.

### Credential problems

Run `status`, then `configure` if the saved credential is missing or invalid. Use `logout` only when you intentionally want to remove the saved password.
