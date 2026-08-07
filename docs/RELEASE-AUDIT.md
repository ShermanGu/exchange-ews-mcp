# Exchange EWS MCP v0.6.16 — Final Release Audit

Audit date: **2026-08-07**

This document records deterministic validation completed for the repository-ready v0.6.16 release. The target environment is Windows with Python 3.10–3.13 and an on-premises Exchange EWS endpoint using NTLM.

## Release identity

- Package: `exchange-ews-mcp`
- Module and wheel metadata version: `0.6.16`
- Production tool profile: **21** tools
- Debug tool profile: **27** tools
- Unified DT groups: **5**
  - `atomic`
  - `workflow-v03`
  - `semantic-mail-v04`
  - `calendar-v05`
  - `weekly-report-v06`

## v0.6.16 fixes

This release addresses two failures observed against the real Agent workflow:

1. An Agent could pass a `calendar_ref` to `update_email_draft`, which correctly failed the reference-kind guard but surfaced as a tool execution error.
2. `update_meeting` treated Exchange `IsMeeting=false` as conclusive and rejected an unsent CalendarItem even when attendee data showed that it was a meeting.

The release changes are:

- accidental `update_email_draft(draft_ref=cal_...)` calls now return a non-mutating structured response with `recommended_tool=update_meeting` instead of attempting a write or raising the generic kind error;
- meeting results now expose `reference_kind=calendar`, `update_tool=update_meeting`, and `send_tool=send_meeting_invitation` to make subsequent Agent routing explicit;
- calendar references now retain meeting provenance, attendee addresses, send state, and the current ChangeKey;
- `update_meeting` and `send_meeting_invitation` classify an item using attendee collections and MCP reference provenance in addition to Exchange `IsMeeting`;
- a true appointment with no attendees is still rejected;
- existing v0.6.15 `cal_` references remain usable when Exchange returns the attendee collections;
- no OWA URL or additional user configuration was added.

Microsoft defines a CalendarItem with attendees as a meeting, while `IsMeeting` indicates meeting versus appointment. The implementation therefore treats attendee collections as corroborating evidence when an on-premises server returns an inconsistent flag.

## Deterministic validation

Strict unit suite:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
PYTHONWARNINGS=error
python -m pytest -q -W error::ResourceWarning
210 passed
```

Coverage added for v0.6.16 includes:

- `IsMeeting=false` plus retained attendees is accepted as an unsent meeting;
- `IsMeeting=false` with no attendees remains rejected;
- a calendar reference passed to `update_email_draft` produces a safe route hint and performs no EWS call;
- saved meeting results declare the exact update/send tools;
- enriched reference metadata is persisted for later update/send operations;
- CLI tool-profile JSON remains printable when the inherited Windows stream encoding is CP1252.

Compilation check:

```text
python -m compileall -q src tests scripts
passed
```

Repository release contract:

```text
v0.6.16
21 Production tools
27 Debug tools
5 DT groups
passed
```

## Distribution validation

Wheel built from the final source tree:

```text
exchange_ews_mcp-0.6.16-py3-none-any.whl
SHA-256: 218c3c93640c8bfcf4baaf41bbbe287d13e5cb7480b4f3edb9f1edca4226666c
```

Validation performed:

- clean GitHub-ready source tree: **210 passed**;
- wheel module version: `0.6.16`;
- wheel metadata version: `0.6.16`;
- `Requires-Python: >=3.10` present;
- all **23** packaged Python files are byte-for-byte identical to the final source tree;
- the full strict **210-test** suite passes while importing `exchange_ews_mcp` from the extracted wheel rather than repository `src`;
- Production and Debug tool counts from wheel code are 21 and 27.

The build used the selected interpreter and disabled PEP 517 isolation. In this container the `build` frontend was unavailable, so the supported `pip wheel --no-build-isolation` fallback was used.

## Live Exchange DT status

**Not executed in this build container.** Live validation requires the maintainer's Windows Credential Manager entry, reachable company EWS endpoint, test mailbox, and approved recipients.

The deterministic suite validates routing, parsing, reference handling, XML generation, ChangeKey refresh, update behavior, and send confirmation. It does not claim that this patch has already been exercised against the user's real Exchange server.

Recommended target-environment verification:

1. upgrade to v0.6.16 without deleting the existing state database;
2. call `get_calendar_item` with the existing `cal_...` reference and verify attendees are returned;
3. call `update_meeting(calendar_ref=..., body_html=...)` and confirm `meeting_updated_not_sent`;
4. inspect the item in Outlook;
5. call `send_meeting_invitation(..., confirm_send=true)` only after manual confirmation;
6. verify attendees receive exactly one invitation.
