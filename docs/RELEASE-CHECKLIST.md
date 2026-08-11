# v0.8.3 release checklist

This checklist defines the final repository and package gate for Exchange EWS MCP v0.8.3.

## Repository contract

- [ ] `src/exchange_ews_mcp/__init__.py` reports `0.8.3`.
- [ ] Production and debug profiles expose 11 and 17 tools respectively.
- [ ] English and Simplified Chinese README files describe the same supported release.
- [ ] `AGENT-CONNECTION.md` and `AGENT-TOOLS.md` document `weekly_report` followed by `continue_action`.
- [ ] CI covers Python 3.10–3.13 on Windows and builds distributions.
- [ ] No credentials, mailbox data, local state databases, or generated virtual environments are committed.

## Deterministic validation

Run:

```powershell
.\run-release-check.cmd
```

This performs repository checks, strict UT, `compileall`, distribution build, and writes `dist/SHA256SUMS.txt`. The wrapper first uses the current shell `python` when `python -m pytest` is available, then falls back to a pytest-enabled project virtual environment or `py -3`. The checker uses the same interpreter for package building and explicitly disables build isolation, which is safer for corporate and offline indexes. Before release validation, install the full local toolchain with `python -m pip install -e ".[dev]"`. The strict UT environment disables unrelated globally installed pytest plugins and treats warnings as errors.

## Live Exchange DT

Configure a dedicated test mailbox and run read-only DT first:

```powershell
.\.venv\Scripts\exchange-ews-mcp.exe configure-dt `
  --person-query "test.person@company.example" `
  --sender "known.sender@company.example" `
  --draft-recipient "test.person@company.example" `
  --subject-contains "EWS-MCP-DT-WEEKLY"

.\run-dt-tests.cmd
```

Then run the full DT only after reviewing the configured recipients and subject marker:

```powershell
.\run-dt-tests.cmd --full
```

Full DT can create unsent mail, reply, forward, weekly-report Reply All/Compose, and meeting drafts. It does not send mail; meeting invitation sending remains separately confirmed by the workflow under test. Review and remove generated drafts after the run.

## Artifact review

- [ ] Install the built wheel into a clean Windows virtual environment.
- [ ] Run `exchange-ews-mcp version` and both production/debug `tool-list` commands.
- [ ] Inspect `RELEASE-AUDIT.md` and generated `dist/SHA256SUMS.txt`.
- [ ] Tag the exact commit as `v0.8.3` only after all environment-owned live DT checks pass.
