# Exchange EWS MCP v0.6.14 — Final Release Audit

Audit date: **2026-08-06**

This document records the deterministic validation completed for the repository-ready v0.6.14 release. The release target is Windows with Python 3.10–3.13 and an on-premises Exchange EWS endpoint using NTLM.

## Release identity

- Package: `exchange-ews-mcp`
- Module version: `0.6.14`
- Wheel metadata version: `0.6.14`
- Production tool profile: **19** tools
- Debug tool profile: **25** tools
- Unified DT groups: **5**
  - `atomic`
  - `workflow-v03`
  - `semantic-mail-v04`
  - `calendar-v05`
  - `weekly-report-v06`

## Repository completeness

The release tree includes:

- English and Simplified Chinese README files at repository root;
- all other public Markdown documentation organized under `docs/`;
- Windows GitHub Actions CI for Python 3.10–3.13;
- Issue and pull-request templates;
- Agent connection and tool-routing documentation;
- architecture and weekly-report design documents;
- live Exchange DT instructions and reusable release checklist;
- clean-install/upgrade guidance;
- contributing, security, code-of-conduct, changelog, and license files;
- strict unit-test, live-DT, and release-check command entry points;
- all scripted pytest execution uses Python module mode rather than a standalone pytest executable.

The public docs describe the same v0.6.14 tool surface and weekly-report contract. Historical version numbers remain only in `CHANGELOG.md` and the legacy DT redirect document where they are intentionally historical.

## Release-check interpreter hotfix

The final repository wrapper no longer unconditionally starts `.venv\Scripts\python.exe`. It now:

1. prefers the current shell `python` when that interpreter can import pytest;
2. falls back to the project virtual environment only when that interpreter can import pytest;
3. finally tries the Windows `py -3` launcher;
4. prints an actionable `python -m pip install -e ".[test]"` command when no usable interpreter is found.

The Python selected by the wrapper runs `scripts/release_check.py`, and the checker uses that same `sys.executable` for `python -m pytest`. Batch control flow uses labels rather than parenthesized exit-code expansion so the checker return code is preserved.

The distribution build also uses the same selected interpreter. It preflights `setuptools.build_meta` and `wheel`, then runs `python -m build --no-isolation` when the `build` frontend is installed, or `python -m pip wheel --no-build-isolation` as a wheel-only fallback. This avoids `BackendUnavailable` failures caused by corporate or offline indexes being unable to seed a temporary PEP 517 environment. A dedicated regression test covers the installed-`build` branch and requires `--no-isolation`.

## Deterministic UT and simulated DT

Commands were executed with external pytest plugin autoload disabled.

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
194 passed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONWARNINGS=error \
  python -m pytest -q -W error::ResourceWarning
194 passed
```

A focused release/DT/weekly-report regression set also passed:

```text
83 passed
```

That focused set covers:

- unified DT configuration and group routing;
- read-only and full-mode fake EWS DT execution;
- the `weekly-report-v06` context and unsent Reply All draft path;
- one-time weekly-flow token behavior;
- compact slot schema and formal Agent prompt contract;
- complex table layout, merged cells, nested tables, multi-level headers, paragraphs, headings, and lists;
- weekly-report SOAP XML and structure-preserving slot replacement;
- 19/25 tool profiles and repository release files.

Compilation check:

```text
python -m compileall -q src tests scripts
passed
```

## Live Exchange DT status

**Not executed in this build container.** Live DT requires the maintainer's Windows Credential Manager entry, reachable EWS endpoint, dedicated test mailbox, recipients, and weekly-report subject marker.

The release includes and unit-tests all five live DT groups. On the target Windows machine, run read-only DT first and then the full draft-creating suite as documented in `DT.md` and `RELEASE-CHECKLIST.md`.

Full DT is designed to create unsent mail/reply/forward/weekly-report/meeting drafts where applicable. It must never be run against unintended recipients, and generated drafts should be reviewed and removed after testing.

## Distribution validation

Wheel built from the final source tree:

```text
exchange_ews_mcp-0.6.14-py3-none-any.whl
SHA-256: 66cd75aa19cfd824d359620eb13bc8c256230a902de25db8bed74a7c0b6e1281
```

Validation performed:

- wheel module version: `0.6.14`;
- wheel distribution metadata version: `0.6.14`;
- `Requires-Python: >=3.10` present;
- all **23** packaged Python files are byte-for-byte identical to the final source tree;
- the complete strict `194`-test suite passes when importing code extracted from the wheel;
- a no-dependency clean virtual environment successfully installs the wheel and imports the package version/metadata.
- the reorganized GitHub-ready source ZIP keeps only the two README files at repository root, places all other Markdown documentation under `docs/`, and contains no build caches or local environments; its strict `194`-test suite and repository release checker both passed after clean extraction.

The container cannot download the full runtime dependency set, so it does not claim a clean-venv MCP CLI startup. Windows CI installs all declared dependencies, runs the strict suite, builds distributions, installs the wheel into a clean environment, and verifies version/tool-list commands.

## Safety properties checked

- mail-writing workflows remain draft-first;
- meeting sends remain explicitly confirmed;
- weekly reports require `get_weekly_report_context` before `update_weekly_report`;
- weekly tokens are random, short-lived, atomically consumed, and single-use;
- Agents edit only text slots and never receive the HTML template;
- new text is HTML-escaped and the final HTML structure is revalidated;
- the weekly-report update uses one native Reply All draft and does not send;
- source ItemId, ChangeKey, credentials, and full templates remain server-side;
- plain-text weekly-report bodies are rejected rather than edited unsafely.

## Environment-owned final gate

Before tagging `v0.6.14`, the maintainer should complete on Windows:

1. install from the final wheel or repository;
2. verify `exchange-ews-mcp version`;
3. verify production and debug tool lists show 19 and 25 tools;
4. run read-only live DT;
5. review configuration and run full live DT;
6. inspect and clean generated drafts;
7. confirm the real Outlook weekly-report format is recognized in that Exchange environment.

Artifact hashes for the final source ZIP, wheel, and release bundle are written to the external SHA-256 manifest generated after packaging.
