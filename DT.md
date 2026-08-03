# Template DT

For backward compatibility, the unified DT group ID remains:

```text
template-mail-v06
```

Its implementation now validates the v0.7 decoupled architecture.

## Read-only

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test `
  --group template-mail-v06 `
  --read-only
```

Checks:

1. locate a real Sent Items source email;
2. call `extract_email_template`;
3. verify `template_ref`, HTML, To/CC suggestions, history-boundary metadata, and available inline resources;
4. create no drafts.

## Full

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test `
  --group template-mail-v06
```

Additionally creates two unsent drafts:

1. a new draft through `compose_email(..., template_ref=...)`;
2. a reply draft through `reply_to_email(..., template_ref=...)`.

Both must return `sent=false`. Ordinary template attachments remain disabled during DT. Inspect and delete the test drafts in Outlook afterward.
