# Live Exchange development tests — v0.6.14

DT runs against a user-owned Exchange mailbox. It is intentionally separate from CI and unit tests because it requires real credentials, real mailbox data, and an accessible EWS endpoint.

## Safety rules

- Use a dedicated test mailbox where possible.
- Mail write tests create or modify drafts only; they do not send messages.
- Calendar write tests create `SendToNone` items and delete them automatically.
- Weekly-report write DT creates one unsent Reply All draft and records its reference for manual review/deletion.
- Do not share generated reports without removing email addresses, subjects, internal URLs, ItemId/ChangeKey values, and mailbox data.
- Never run DT against a mailbox you do not own or administer.

## 1. Install and verify

```powershell
.\install.cmd
.\.venv\Scripts\exchange-ews-mcp.exe version
.\.venv\Scripts\exchange-ews-mcp.exe status
.\.venv\Scripts\exchange-ews-mcp.exe test
```

Expected version: `0.6.14`.

## 2. Configure DT objects

Choose:

- one or more person queries supported by `resolve_people`;
- one or more real sender email addresses with searchable mail;
- a mailbox address that can receive draft test messages;
- a distinctive weekly-report subject keyword for `weekly-report-v06`.

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe configure-dt `
  --person-query "xiaoming" `
  --sender "alice@company.example" `
  --draft-recipient "you@company.example" `
  --subject-contains "周报" `
  --search-limit 20
```

Multiple people or senders may be supplied by repeating the option.

Review the saved configuration:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe show-dt-config
```

Current groups:

```text
atomic
workflow-v03
semantic-mail-v04
calendar-v05
weekly-report-v06
```

## 3. Read-only DT first

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --read-only
```

Read-only mode validates:

- EWS connectivity and mailbox reads;
- sender searches and GetItem parsing;
- name resolution and semantic message lookup;
- availability, working-hours parsing, and common-slot calculation;
- weekly-report search, history splitting, compact slots, complete Agent prompt, date rule, and one-time token creation.

It skips mail draft writes, calendar item creation, and weekly Reply All draft creation.

## 4. Full DT

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test
```

Full mode validates all read-only behavior plus safe writes:

- raw mail drafts, replies, forwards, attachments, and updates;
- semantic compose workflow;
- `SendToNone` meeting creation/read/delete;
- weekly-report context followed by a native unsent Reply All draft.

The weekly DT deliberately reuses an existing slot's text and subject. Its goal is to verify the deterministic server chain without inventing a project update.

## 5. Run one group

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --read-only --group weekly-report-v06
```

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --group calendar-v05
```

`--group` may be repeated.

## 6. Weekly-report DT prerequisites

`weekly-report-v06` requires `subject_contains`. If it is absent, the group is skipped to avoid selecting an unrelated message.

The latest matching Sent Items message must:

- have an HTML body;
- contain a recognized `WordSection1`;
- contain the approved top-level separator block between report sections;
- contain at least one non-empty editable text slot.

The write step verifies:

- `get_weekly_report_context` returns `context_ready`;
- the token starts with `weeklyflow_`;
- production slots contain only `slot_id`, `text`, and `location`;
- the full prompt contains date hard-check and formal rewriting rules;
- `update_weekly_report` consumes the token;
- the result is a Reply All draft;
- `sent` is `false`;
- no second Body overwrite occurs after the native reply operation;
- an HTML structure signature is returned.

## 7. Reports and cleanup

Reports are written under the user's application configuration directory in `test-reports`, with a filename similar to:

```text
dt-20260806T091500Z.json
```

The report lists:

- each step and duration;
- PASS/FAIL/SKIP state;
- redacted diagnostic details;
- created draft references;
- created calendar items and cleanup result.

Calendar test items are automatically deleted. Mail and weekly-report drafts are intentionally retained for review; delete them manually from Drafts after inspection.

## 8. Interpreting results

A release candidate is ready for the user's Exchange environment only when:

- the strict unit suite passes;
- read-only DT passes;
- full DT passes or every skip is understood and accepted;
- no mail was sent unexpectedly;
- no meeting invitation was sent unexpectedly;
- the weekly draft preserves formatting, images, and native reply history;
- the Agent-facing tool list is exactly 19 tools.

## 9. Common failures

### `weekly-report-v06` skipped

Configure a `--subject-contains` value that identifies the user's weekly reports.

### Weekly separator not found

Run the sanitized diagnostic script and compare the exact Outlook separator block with `weekly_separator_whitelist.py`. Do not broaden matching based on appearance alone.

### Context stale

A newer report arrived or the source message changed after context creation. Run `get_weekly_report_context` again.

### WorkingHours unavailable

EWS may omit or return unusable WorkingHours. Configure local calendar preferences and review the returned source/override fields.

### Name ambiguity

Use a full email or an unambiguous supported name query and rerun DT.

## 10. Clear only DT configuration

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe clear-dt-config
```

Do not use `reset-local` unless you also intend to remove normal configuration, local references, and the saved credential.
