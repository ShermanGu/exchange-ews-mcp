# Architecture — Exchange EWS MCP v0.6.16

## Design goals

Exchange EWS MCP is designed around four constraints:

1. the Exchange environment is on-premises and authenticates through NTLM;
2. credentials and mailbox identifiers must remain local;
3. Agent-facing writes should be high-level, draft-first, and hard to misuse;
4. uncertain selections and send actions must be resumable and explicitly confirmed.

## Layers

```text
MCP / CLI surface
        ↓
Service facade
        ↓
Semantic workflows
  ├─ mail workflow
  ├─ weekly-report workflow
  └─ calendar workflow
        ↓
Workflow primitives and EWS client
        ↓
Exchange Web Services + NTLM
```

### Configuration and credentials

- Non-secret configuration is stored under the Windows user profile through `platformdirs`.
- Passwords are stored through `keyring`, normally backed by Windows Credential Manager.
- The repository must not contain credentials, mailbox data, internal URLs, or local state databases.

### EWS client

`exchange_ews_mcp.ews.EwsClient` owns SOAP construction and parsing for:

- identity and name resolution;
- mail listing, searching, reading, and attachment metadata;
- draft creation, reply, reply-all, forward, and update;
- availability and working-hours queries;
- calendar item listing, reading, creation, and deletion used by DT cleanup.

The EWS client operates with raw ItemId and ChangeKey values, but those values are not the normal Agent contract.

### Local reference store

`ReferenceStore` stores opaque, expiring references and resumable actions in SQLite:

- `message_ref`
- `draft_ref`
- `person_ref`
- `calendar_ref`
- confirmation/resume tokens
- weekly-report contexts and one-time flow tokens

References keep Exchange IDs and internal workflow state out of ordinary Agent prompts.

### Semantic mail workflow

`SemanticMailWorkflow` combines recipient resolution, mail search, ambiguity handling, and draft operations.

Key properties:

- people are resolved by full email or supported name query rules;
- ambiguous people or messages produce candidates and a resumable token;
- compose, reply, forward, and update operations never send mail;
- attachment paths are validated before remote state is created;
- multi-attachment updates carry the latest ChangeKey forward.

### Weekly-report workflow

The weekly-report workflow intentionally separates understanding from deterministic editing:

```text
get_weekly_report_context
        ↓
compact text slots + full Agent instructions + single-use token
        ↓
Agent selects slot_id and produces polished new_text
        ↓
update_weekly_report
        ↓
server inserts escaped text into stored HTML and validates structure
        ↓
native Reply All draft
```

The Agent never receives the source HTML. Layout analysis is advisory only and is compressed into one nullable `location` string per slot. Actual writes use the server-side slot manifest and stored template.

See [WEEKLY-REPORT.md](WEEKLY-REPORT.md).

### Calendar workflow

`CalendarWorkflow` resolves attendees, queries EWS availability, applies configured local working hours, finds common slots, and creates meetings. Saved meetings are addressed through `calendar_ref`; update/send operations re-read the CalendarItem by ItemId so they use the server's current ChangeKey after any Outlook edit.

Safety rules:

- meeting creation defaults to `SendToNone`;
- a request to send invitations produces a three-way send/save/cancel confirmation action;
- choosing save creates the CalendarItem without notifying attendees;
- `update_meeting` is restricted to unsent, non-cancelled meetings and uses `SendToNone`;
- `send_meeting_invitation` requires `confirm_send=true`, rejects already-sent meetings, and uses `SendToAllAndSaveCopy`;
- calendar updates use `NeverOverwrite`, so a concurrent Exchange edit fails instead of being silently replaced;
- a time-window request with multiple valid slots produces a time-selection action first;
- Agent-facing results include local-display fields while preserving UTC semantics.

## MCP profiles

### Production profile

The production profile exposes 21 curated tools. It includes high-level workflows and read-only primitives but hides overlapping low-level writes.

### Debug profile

The debug profile adds six EWS primitives:

```text
resolve_names
create_draft
reply_as_draft
forward_as_draft
update_draft
create_meeting
```

They exist for deterministic DT and protocol troubleshooting. Normal Agents should not use them because they bypass high-level routing and confirmation behavior.

## Error and ambiguity model

The server prefers explicit states over guesses:

- `resolved` — one safe selection exists;
- `needs_confirmation` — the caller must select a candidate or confirm an action;
- `context_stale` — source state changed and the workflow must restart;
- `failed` — a one-time workflow was consumed but validation or remote execution failed;
- clear configuration/input errors — returned before remote writes whenever possible.

## Security properties

- Secrets remain in the local credential store.
- Normal mail writes are draft-only.
- Meeting sends require confirmation.
- External paths are allow-listed and validated.
- Weekly-report HTML never enters the Agent context.
- Weekly-report text is HTML-escaped before insertion.
- Weekly-report structure tokens must remain identical after editing.
- One-time weekly tokens prevent out-of-order, repeated, concurrent, or stale updates.
- SQLite connections are explicitly closed.

## Testing strategy

### Unit tests

Unit tests use fake clients and deterministic XML fixtures. They cover:

- SOAP builders and parsers;
- ChangeKey propagation;
- semantic resolution and confirmation;
- calendar time normalization;
- weekly-report separators, layout analysis, slot application, prompt rules, and token states;
- tool profiles, CLI schema, package version, and release-document consistency.

### Development tests

DT uses a real user-owned Exchange mailbox and is intentionally separate from CI. Mail operations create drafts only. Calendar write tests use `SendToNone` and delete created test items. See [DT.md](DT.md).
