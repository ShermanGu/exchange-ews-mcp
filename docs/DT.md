# Live Exchange development tests — v0.9.0

DT runs against a user-owned Exchange mailbox and is intentionally separate from CI/unit tests.

## Safety

- Mail write DT creates or edits drafts only; it never sends email.
- Calendar write DT creates `SendToNone` items and cleans them up.
- Weekly-report full DT creates one unsent draft and leaves it for manual inspection/deletion.

## Configure

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe configure-dt `
  --person-query "xiaoming" `
  --sender "alice@company.example" `
  --draft-recipient "you@company.example" `
  --subject-contains "周报" `
  --search-limit 20
```

Review:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe show-dt-config
```

Groups remain backward-compatible:

```text
atomic
workflow-v03
semantic-mail-v04
calendar-v05
weekly-report-v06
```

The historical `weekly-report-v06` group name is retained even though it now tests the v0.8 Agent route.

## Read-only first

```powershell
.\run-dt-tests.cmd
```

Equivalent:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --read-only
```

All write steps are expected to show `SKIP` in this mode.

## Full DT

```powershell
.\run-dt-tests.cmd --full
```

Equivalent:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test
```

Mail drafts remain in Drafts for manual review. Calendar DT items are cleaned automatically.

## Weekly-report DT

A distinctive `subject_contains` is required. The latest matching Sent Items message must have an HTML body with at least one editable visible text slot. `WordSection1` is no longer mandatory.

Read-only weekly DT validates:

- recent weekly-report search;
- first `发件人`/standalone `From` top-body boundary logic or fresh-message fallback;
- compact short-ID `id/text/loc` output;
- at most three weeks total (current slots + previous two `history` entries);
- the `weekly_report → continue_action` contract;
- creation of a one-shot `weeklyflow_...` `resume_token`.

Full weekly DT consumes that token through `continue_action`. The server automatically chooses:

- `reply_all` if the latest source contains a quoted-history sender marker;
- `compose` if no marker is found.

The DT asserts that the result is unsent, that the returned mode and `reply_all` flag agree, that no post-create Body overwrite occurs, and that an HTML structure signature is present.

Run only this group:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --read-only --group weekly-report-v06
.\.venv\Scripts\exchange-ews-mcp.exe dt-test --group weekly-report-v06
```

## Manual weekly draft review

After full DT, inspect the created weekly draft in Outlook:

- top weekly-report table/layout is intact;
- expected text/date changes are correct;
- inline images still render;
- Reply All mode retains native history exactly once;
- Compose mode copied To/CC correctly;
- Subject reporting period is current;
- nothing was sent.

## Common failures

**Group skipped:** configure `--subject-contains`.

**History marker unexpectedly missing/found:** run `scripts/dump_weekly_report_html.py` against sanitized HTML and inspect the reported first visible `发件人`/`From` boundary. There is no separator whitelist in v0.8.

**Context stale:** a newer matching weekly report arrived or the latest source Body changed after `weekly_report`; rerun `weekly_report`.

**Subject override rejected:** `weekly_report` automatically rolls supported date/week markers forward. If `continue_action` explicitly supplies the old Subject again, remove the override or provide the intended custom Subject.
