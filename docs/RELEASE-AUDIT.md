# Exchange EWS MCP v0.8.3 — Release Audit

Audit date: **2026-08-11**

This document records the repository-level release validation for Exchange EWS MCP v0.8.3. The primary target is Windows with Python 3.10–3.13 and an on-premises Exchange EWS endpoint using NTLM. Live Exchange DT remains an environment-owned release gate and must be run from the maintainer's configured Windows environment against the exact release candidate before tagging.

## Release identity

- Package: `exchange-ews-mcp`
- Module / distribution version: `0.8.3`
- Production tool profile: **11** tools
- Debug tool profile: **17** tools
- Unified DT groups: **5**
  - `atomic`
  - `workflow-v03`
  - `semantic-mail-v04`
  - `calendar-v05`
  - `weekly-report-v06`

The historical `weekly-report-v06` DT group name is intentionally retained for compatibility; it exercises the current v0.8.3 `weekly_report → continue_action` route.

## v0.8.3 weekly-report contract

The Agent-facing weekly-report workflow is intentionally compact:

1. `weekly_report` is the only weekly-report entry tool.
2. The result contains only the context needed by the Agent: `resume_token`, server-selected `mode`, default draft `subject`, current user `request`, short local `slots`, and at most the previous two reports in `history`.
3. Public slot IDs are local ordinals (`s1`, `s2`, ...). Long internal IDs, HTML offsets, hashes and Exchange identifiers remain server-side.
4. The Agent submits only changed `id/text` pairs through generic `continue_action`.
5. Local payload validation errors do not consume the token. The one-shot claim begins only after deterministic preflight succeeds and the workflow reaches the write boundary.
6. The Server automatically advances supported Subject date/week markers by one week. `selections.subject` is only an explicit user override.
7. Weekly history is three weeks total: the newest report is represented by editable `slots`; `history` contains at most the previous two reports.
8. Reply-history detection uses the first visible `发件人` or standalone `From` marker and HTML nesting depth rather than an Outlook separator whitelist.
9. If the newest report contains quoted history, the draft is created as native Reply All. Otherwise the workflow creates a fresh Compose draft and copies the source To/CC server-side.
10. The Agent never owns HTML. Text replacement remains deterministic and HTML structure is revalidated before the Exchange write.

## Compact tool surface

Production exposes exactly:

```text
search_mail
read_mail
resolve_people
save_mail_draft
edit_mail_draft
continue_action
weekly_report
read_calendar
find_meeting_times
save_meeting
send_meeting_invitation
```

Debug adds six low-level primitives:

```text
resolve_names
create_draft
reply_as_draft
forward_as_draft
update_draft
create_meeting
```

Room/Resource attendee semantics are not advertised or supported by the compact calendar workflow. `resolve_people` remains independently visible because identity disambiguation is shared across workflows.

## Deterministic validation

The final release candidate must pass:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python -m pytest -W error::ResourceWarning
```

Expected result for this source tree:

```text
228 passed
```

Additional release checks:

```text
python -m compileall -q src tests scripts
scripts/release_check.py static repository contract
package build without PEP 517 isolation
clean extraction / re-test of the final source archive
```

The release checker also writes `dist/SHA256SUMS.txt` for the generated distributions.

## CI contract

GitHub Actions:

- runs strict unit tests on Windows for Python 3.10, 3.11, 3.12 and 3.13;
- prints package version and Production/Debug tool profiles;
- builds distributions;
- installs the wheel in a clean virtual environment and checks its CLI;
- uploads the artifacts as `exchange-ews-mcp-v0.8.3-distributions`.

## Final artifact validation

The final release distributions were rebuilt from the exact v0.8.3 package source on Windows with Python 3.12 and a no-build-isolation PEP 517 build. They were then validated for metadata/source parity.

```text
exchange_ews_mcp-0.8.3-py3-none-any.whl
SHA-256: 7473e4822aa2dc43651e871a582f857287da4237560a05aca03fbbf92e26bd32

exchange_ews_mcp-0.8.3.tar.gz
SHA-256: 50baf2ad08ae23875509f65617f8c067c4f3064fe924b4cc3183befdf1851aac
```

Wheel metadata validation:

- `Name: exchange-ews-mcp`
- `Version: 0.8.3`
- `Requires-Python: >=3.10`
- `License-Expression: MIT`
- all declared runtime dependencies are present in wheel metadata;
- all **22** packaged `exchange_ews_mcp/*.py` files are byte-for-byte identical to the final source tree;
- direct import from the wheel reports v0.8.3, 11 Production tools and 17 Debug tools.

Local validation imported the built wheel directly in the existing Windows release environment and confirmed v0.8.3 plus both tool profiles. The GitHub Actions package job remains the required dependency-resolving clean-install gate: it installs the wheel with dependencies in a new Windows virtual environment and validates the version and both tool profiles.

## Live Exchange DT gate

Live EWS tests are intentionally not run by the repository build container because they require Windows Credential Manager state, a reachable company EWS endpoint and approved test recipients.

Before creating tag `v0.8.3`, run against the exact release candidate:

```powershell
.\run-dt-tests.cmd
.\run-dt-tests.cmd --full
```

Review generated mail/weekly-report drafts and calendar cleanup, then confirm there are no unexpected sends or mutations. See [DT.md](DT.md) for the per-group contract and cleanup guidance.

## Distribution integrity

`run-release-check.cmd` builds both wheel and sdist when the `build` frontend is available; its supported offline fallback builds the wheel only. In either case it writes SHA-256 hashes for the generated artifacts to:

```text
dist/SHA256SUMS.txt
```

The checksum file is generated from the exact built artifacts, so hashes should be taken from the final candidate rather than copied between builds.

## Release decision

Repository-level release readiness is **PASS** only when the deterministic gate is green and the generated archive is re-tested from a clean extraction. Final tagging remains contingent on the maintainer completing the live Exchange DT gate against the same v0.8.3 source candidate.
