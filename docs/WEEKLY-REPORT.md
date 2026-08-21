# Weekly-report workflow — v0.9.0

The weekly-report design separates **semantic understanding** from **deterministic HTML editing**. The Agent decides wording. The server owns HTML, slot mapping, validation, Exchange state, and draft creation.

## Agent-visible flow

Only one weekly-report tool is exposed:

```text
weekly_report
    ↓
compact JSON: token + current slots + previous two weeks
    ↓
Agent decides changed slots / optional new Subject
    ↓
continue_action
    ↓
server-only weekly_report_update primitive
    ↓
unsent Reply All or Compose draft
```

`update_weekly_report` remains an internal workflow primitive and is not registered as an MCP tool.

## Request preparation

`weekly_report` has one required Agent-facing semantic input: `request`. It is the **final set of facts/instructions that should be absorbed into the new weekly report**, not necessarily the user's original sentence.

Two supported paths converge on the same tool call:

```text
Direct update:
user facts → weekly_report(request=<user facts>)

Aggregate other reports:
search_mail → read_mail → LLM summary → weekly_report(request=<summary>)
```

For a prompt such as “summarize A and B's weekly reports and generate mine”, the Agent must finish the mail search/read and LLM summarization first. It then passes only the useful synthesized weekly facts as `request`. The meta-instruction to “search/summarize A and B” must not be copied into `request`, and `weekly_report` itself does not search other users' messages.

The summary should preserve project/topic, current progress, next-step plans, risks/issues, and other facts needed by slot routing; duplicate or conversational mail text should be condensed before the weekly-report stage.

## `weekly_report` result

The Agent-facing tool always reads at most **three weekly mails total**. The newest report is represented by editable `slots`; only the previous two reports are returned as `history`, so the newest report is not duplicated.

Typical result:

```json
{
  "resume_token": "weeklyflow_...",
  "mode": "reply_all",
  "subject": "项目周报 2026-08-03 至 2026-08-09",
  "request": "项目A完成联调，下周做性能测试",
  "instructions": "先拆分独立事实，再按 loc 的显式表头层级路由；例如周报（纵向表头） = nl2sql项目（横向表头） > 项目进展（二级纵向表头）...",
  "slots": [
    {"id":"s1","text":"日期：2026-08-03 至 2026-08-09"},
    {"id":"s2","text":"完成接口联调","loc":"周报（纵向表头） = 项目A（横向表头） > 本周进展（二级纵向表头）"},
    {"id":"s3","text":"开展性能测试","loc":"周报（纵向表头） = 项目A（横向表头） > 下周计划（二级纵向表头）"}
  ],
  "history": [
    {"subject":"项目周报 2026-07-27 至 2026-08-02","text":"..."},
    {"subject":"项目周报 2026-07-20 至 2026-07-26","text":"..."}
  ]
}
```

If a historical item cannot be extracted, a compact `warnings` field may also be returned. Dynamic `agent_prompt`, HTML, Exchange item IDs, offsets, hashes, To/CC, TTL metadata, and long internal slot IDs are not returned.

`loc` is optional and advisory only; it never participates in deterministic write positioning. Every text node in the path carries an explicit structural label in parentheses. Example: `周报（纵向表头） = nl2sql项目（横向表头） > 项目进展（二级纵向表头）`. `=` marks the primary vertical/column-header and horizontal/row-header intersection, while `>` continues into a more specific header level. The editable content itself stays in `slot.text` and is not duplicated at the end of `loc`. The server does not emit a semantic `role` or duplicate `context` object.

## Short slot IDs

Public slot IDs are local to one `resume_token`:

```text
s1, s2, s3, ...
```

They are not hashes and do not need to be globally unique. Internally, the server re-extracts the stored template and deterministically maps each ordinal ID back to the corresponding long internal slot ID and original offset range.

Therefore the real identity is effectively:

```text
(resume_token, sN)
```

The Agent must copy an `id` exactly from the current `weekly_report` result and must never invent one.

## Fixed Agent rules

The behavioral rules live in the `weekly_report` MCP tool description and the compact `instructions` string returned with the current context. `request` may be direct user input or an Agent-prepared mail summary; after it reaches this stage both are handled identically. For slot selection the Agent must first split the request into independent facts, then match project/topic against the middle `loc` path and the intended time/status column against the final path segment. `slot.text` is only a concrete-content/tie-breaker signal; it must not cause the entire request to be copied into one slot.

- user-provided current facts override history;
- only slots that truly changed are submitted;
- conversational input is rewritten into concise, formal, factual weekly-report language;
- historical project names and technical terminology should be preserved where applicable;
- facts, dates, metrics, owners, or completion states must never be invented;
- all inherited report-period dates/week numbers must be reviewed and rolled forward;
- supported Subject date/week markers are rolled forward deterministically by the Server; the Agent only overrides `subject` when the user explicitly requests a different title;
- HTML is never generated by the Agent;
- local validation failures reuse the same token; only stale/expired/used/superseded contexts require another `weekly_report` call.

`reference_materials` are Agent-supplied tool inputs and are intentionally not echoed back in the result, avoiding duplicate context.

## Quoted-history boundary

For each HTML message the server scans **visible text only** for the first `发件人` or standalone English `From`. Attributes, comments, CSS, script/style content do not trigger matching.

Nesting depth is tracked relative to this scan root:

1. inner content of `div.WordSection1`, when present;
2. otherwise inner `<body>`;
3. otherwise the document fragment.

When the first sender marker is found, the scanner rewinds to the opening offset of the depth-0 block containing that marker and cuts immediately before it. If no sender marker is found, the entire current message body is treated as that week's report.

This supports both Reply All threads and users who create a fresh weekly-report email every week, without Outlook-version-specific separator whitelists.

## Historical aggregation

The workflow searches the latest three matching mail items and extracts one top report body from each:

```text
latest mail   → top body → editable template / slots
previous mail → top body → history[0]
previous mail → top body → history[1]
```

Failure to extract the newest item is fatal. Older malformed items may be skipped and reported in `warnings`.

## Reply All vs fresh Compose

The extraction result determines draft mode server-side:

```text
latest source contains 发件人/From history marker
        → reply_all

no history marker
        → compose
```

### Reply All

The server creates one native EWS Reply All draft and supplies the updated top report as `NewBodyContent`. It does not perform a second Body update afterward. Native reply history is left to Exchange.

### Compose

The server creates a new draft and copies the original To and CC lists. The source Subject is used as the baseline. The original sent timestamp is not copied into the new draft.

## `continue_action`

Weekly continuation accepts:

```json
{
  "changes": [
    {"id":"s2","text":"完成接口联调及验证"},
    {"id":"s3","text":"开展性能测试"}
  ]
}
```

Each change may contain only `id` and `text`. Unknown fields or IDs are rejected before any Exchange write. `subject` remains an optional override; normally it is omitted because `weekly_report` already returned and stored the Server-selected default draft Subject.

The server translates each short ID to the hidden internal slot ID and then applies the existing text-only replacement logic. `text` is HTML-escaped; the Agent never controls markup, offsets, attributes, or tags.

## HTML ownership and integrity

For the newest template the server stores:

- original raw HTML;
- deterministic internal slot IDs and offsets;
- slot-manifest signature;
- HTML-structure signature;
- complete internal layout analysis;
- source-body hash and Exchange source identity.

Only selected text spans are replaced. Replacements are applied from the end of the template toward the beginning, then the server re-lexes the result and requires the structure signature to remain identical.

This keeps semantic location and deterministic write location deliberately separate.

## Token/staleness state machine

```text
context_ready
  ├─ bad id/text/subject → context_ready (retry same token)
  └─ deterministic preflight passes
             ↓ atomic claim
          applying
            ├─ success → completed
            ├─ newer/source-changed → context_stale
            └─ write-phase/remote failure → failed
```

A newer context for the same source supersedes an older unused token. Tokens expire after the configured TTL. The token becomes one-shot only at the write boundary; local Agent payload mistakes do not consume it.

Before writing, the server re-checks the latest matching item, complete source-body SHA-256, slot manifest, and HTML structure signature.

## Safety invariants

Future refactors should preserve these properties:

- only `weekly_report` is Agent-visible for starting a weekly-report workflow;
- the second step always uses generic `continue_action`;
- the Agent never receives or returns HTML;
- public slot IDs are short and context-local;
- `loc` is advisory only;
- writes are based on the stored template and deterministic server-side manifest;
- text is escaped and HTML structure must remain unchanged;
- Reply All performs one native reply-body write and never overwrites Body afterward;
- Compose copies source To/CC server-side;
- the workflow never sends mail;
- stale source/context and duplicate writes fail closed.
