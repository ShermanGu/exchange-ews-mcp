# Fresh start — v0.7.2

Only use this flow for a genuinely new user or a deliberate clean-room retest.

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe reset-local
.\.venv\Scripts\exchange-ews-mcp.exe configure
.\.venv\Scripts\exchange-ews-mcp.exe set-current-user `
  --email "yourself@company.com" `
  --display-name "Your Name"
.\.venv\Scripts\exchange-ews-mcp.exe configure-dt `
  --person-query "xiaoming" `
  --sender "sender@company.com" `
  --draft-recipient "yourself@company.com"
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --read-only
.\.venv\Scripts\exchange-ews-mcp.exe dt-test
```

For normal upgrades from v0.6.x, do not run `reset-local`.
