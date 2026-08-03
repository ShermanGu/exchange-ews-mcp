# Release audit — v0.7.2

## Scope

This release completes the decoupled template extraction architecture with a single public `body_html` contract across MCP, service, workflow, and CLI.

## Static surface

- Production MCP tools: 18
- Debug MCP tools: 24
- CLI commands: 40
- Removed MCP/CLI tools: `compose_from_email`, `reply_from_email`
- Added MCP/CLI tool: `extract_email_template`
- Existing writers enhanced: `compose_email`, `reply_to_email`
- Public template-specific body parameters: none
- `body_html` without `template_ref`: complete HTML
- `body_html` with `template_ref`: new content fragment rendered into the stored complete template

## Unit and regression coverage

The suite covers:

- top-message extraction from Outlook, Gmail, cite-block, and Original Message histories;
- complete preservation when the selected email contains only one message;
- truncated long-message handling;
- exact source candidate confirmation and outsider rejection;
- opaque `template_ref` type validation;
- template source and reply target independence;
- absence of `template_content_html` from public writer signatures;
- CLI `body_html + template_ref` routing into server-side rendering;
- To/CC suggestions with BCC omission;
- rendered-final-HTML-based `cid:` attachment selection;
- explicit normal-attachment opt-in;
- attachment preflight before draft creation;
- compose/reply compatibility without any template;
- local attachment ChangeKey chains;
- SQLite connection closure;
- calendar send-confirmation protections;
- production/debug tool profiles and CLI registration.

## Final validation

- Unit and regression tests: 185 passed
- ResourceWarning strict run: 185 passed
- Local wheel build: passed
- Clean isolated-target wheel installation: passed
- Installed package version: 0.7.2
- Console scripts: 3
- Production/debug surfaces: 18 / 24
- CLI commands: 40

## v0.7.2 focused audit

- EWS `UniqueBody` request and parser coverage: PASS
- Separate Body/UniqueBody server and local truncation flags: PASS
- 5000-character-prefix regression: PASS
- Classic Outlook header-block fallback: PASS
- Large MCP result compaction: PASS
- Single public `body_html` writer contract: PASS
- `template_content_html` removed from MCP/service/workflow writer signatures: PASS
- CLI and MCP template insertion semantics aligned: PASS
- Server-side template insertion in compose/reply: PASS
- Reply `message_ref` and format `template_ref` independence: PASS
- Strict ResourceWarning test run: 185 PASS
