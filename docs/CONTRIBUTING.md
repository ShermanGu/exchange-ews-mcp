# Contributing

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

Thank you for improving Exchange EWS MCP. Contributions must preserve its local-first, draft-first safety model and remain practical for on-premises Exchange environments.

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- Use public issues for bugs, documentation, and feature proposals.
- Use private vulnerability reporting for security-sensitive findings; see [SECURITY.md](SECURITY.md).
- Never include real credentials, mailbox content, internal EWS URLs, Exchange IDs, or personal data in tests, issues, commits, or logs.

## Development setup

The primary target is Windows with Python 3.10–3.13.

```powershell
git clone https://github.com/ShermanGu/exchange-ews-mcp.git
cd exchange-ews-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Run the strict suite:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
$env:PYTHONWARNINGS = "default"
.\.venv\Scripts\python.exe -m pytest -W error::ResourceWarning
```

Build distributions:

```powershell
.\.venv\Scripts\python.exe -m build
```

## Change requirements

1. Create a focused branch from `main`.
2. Keep unrelated refactors out of the same pull request.
3. Add or update deterministic unit tests for behavior changes.
4. Update English and Simplified Chinese entry documentation for user-visible changes.
5. Update `AGENT-TOOLS.md` when the MCP surface or routing changes.
6. Update `DT.md` when a real-Exchange scenario changes.
7. Preserve draft-first behavior and explicit meeting-send confirmation.
8. Run the full strict suite and package build before pushing.

## Weekly-report changes

Changes to the weekly-report workflow require extra care:

- do not expose complete HTML to the Agent;
- do not allow the Agent to supply tags, offsets, or attributes;
- keep separator matching fail-closed and covered by exact fixtures;
- preserve one-time token ordering and atomic claim behavior;
- keep layout information advisory only;
- add tests for table, non-table, malformed, stale, repeated, and concurrent cases;
- add or update the `weekly-report-v06` DT group when live behavior changes.

## Coding expectations

- Support Python 3.10 and newer.
- Use UTF-8 for source and documentation.
- Prefer clear workflow boundaries over overlapping Agent tools.
- Keep Exchange ItemId, ChangeKey, credentials, and server-owned HTML behind local abstractions.
- Validate all local paths and external inputs before remote writes.
- Return actionable errors without revealing secrets.
- Close resources deterministically.

## Live Exchange validation

Unit tests must not require a live mailbox. Use fixtures and fake clients for protocol and workflow behavior.

When live DT is required:

- use a dedicated test mailbox;
- run read-only DT before full DT;
- create drafts rather than sending mail;
- use `SendToNone` for calendar writes;
- delete or review generated test items;
- sanitize all reports before sharing.

## Pull-request checklist

A good pull request includes:

- problem and solution summary;
- user, compatibility, and safety impact;
- tests added or updated;
- exact validation commands and results;
- documentation changes;
- no credentials, mailbox content, generated environments, caches, state databases, or private logs.
