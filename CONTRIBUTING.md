# Contributing

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

Thank you for improving Exchange EWS MCP. Contributions should preserve its local-first, draft-first safety model and remain practical for on-premises Exchange environments.

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- Use a public issue for bugs, documentation, and feature proposals.
- Use GitHub private vulnerability reporting for anything security-sensitive; see [SECURITY.md](SECURITY.md).
- Do not include real mailbox content, credentials, internal server names, Exchange identifiers, or personal data in issues, tests, or commits.

## Development setup

The primary development target is Windows with Python 3.10 or newer.

```powershell
git clone https://github.com/ShermanGu/exchange-ews-mcp.git
cd exchange-ews-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Run the strict test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -W error::ResourceWarning
```

## Making a change

1. Create a focused branch from `main`.
2. Keep unrelated refactors out of the same pull request.
3. Add or update tests for behavior changes.
4. Update both English and Simplified Chinese entry documentation when user-facing behavior changes.
5. Preserve backward-compatible tool names unless the change is explicitly a breaking change.
6. Run the full strict test suite before pushing.

## Coding expectations

- Support Python 3.10 and newer.
- Prefer clear workflow boundaries over overlapping Agent tools.
- Keep writes draft-first and meeting sends explicitly confirmed.
- Keep Exchange ItemId, ChangeKey, credentials, and full stored templates behind local abstractions.
- Validate attachments and other external inputs before creating remote state.
- Return actionable errors without exposing secrets.
- Use UTF-8 for source and documentation.

## Testing EWS changes

Unit tests must not require a live mailbox. Use deterministic fixtures and fake clients for protocol and workflow behavior.

If a change also needs live Exchange validation:

- use a dedicated test mailbox;
- create drafts instead of sending messages;
- do not send meeting invitations without explicit confirmation;
- remove or redact mailbox data from logs before sharing results.

## Pull requests

A good pull request includes:

- a concise explanation of the problem and solution;
- user and compatibility impact;
- tests added or updated;
- the exact validation command and result;
- documentation changes where applicable;
- no credentials, mailbox content, generated environments, caches, or local state files.

Maintainers may ask for a smaller scope, additional regression coverage, or updated translations before merging.
