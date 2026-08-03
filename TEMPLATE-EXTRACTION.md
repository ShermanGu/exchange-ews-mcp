# Decoupled email template extraction

## Goal

Template analysis must not create or reply to mail. `extract_email_template` only reads and extracts; `compose_email` and `reply_to_email` remain the only high-level new-mail/reply writers.

## Source selection

The tool accepts either:

- `message_ref`; or
- `folders`, `sender_query`, `participant_query`, `subject_contains`, `after`, `before`, and `limit`.

Default folders are `inbox` and `sentitems`. Outlook display aliases such as `Sent Items` are normalized automatically.

If multiple messages match, it returns `needs_confirmation`, candidate `message_ref` values, and a `resume_token`. `continue_action` only accepts a candidate from that exact pending set.

## Reply-chain extraction rule

The selected EWS item is the only template source. The extractor does not search for another conversation item. It first requests EWS `UniqueBody`, the Exchange-native current-message segment.

When `UniqueBody` is unavailable, it scans the selected full HTML for the earliest known history boundary, including:

- Outlook `divRplyFwdMsg`;
- Outlook `stopSpelling` / message-header blocks;
- `<blockquote type="cite">`;
- Gmail `gmail_quote`;
- `-----Original Message-----` and Chinese equivalents.

If a boundary exists:

```text
[top/current message] [history boundary] [older messages]
        ↓
[top/current message only]
```

If no boundary exists, the complete selected message is used as the example.

## Returned data

Important fields:

```json
{
  "status": "template_extracted",
  "template_ref": "tmpl_xxx",
  "template_html": "<html>...</html>",
  "template_shell_html": "<html>...EWS-MCP-CONTENT-START...</html>",
  "suggested_compose_inputs": {
    "to_queries": ["a@company.com"],
    "cc_queries": ["b@company.com"],
    "subject": "上周项目周报"
  },
  "quoted_history_excluded": true,
  "history_boundary_strategy": "outlook_reply_forward_div",
  "inline_content_ids": ["logo-1"]
}
```

The complete top-message template and marker shell stay behind `template_ref`. `template_html` and `template_shell_html` are returned directly only when small enough; otherwise the result contains compact previews, truncation flags, and exact character counts. The Agent must not reconstruct a complete template from a preview.

## `template_ref`

`template_ref` is a local opaque reference with a seven-day TTL. It contains source identity, extracted template metadata, and attachment metadata. It does not expose Exchange ItemId to the Agent.

When passed to `compose_email` or `reply_to_email`:

- `body_html` is interpreted as the new content fragment and rendered into the complete stored template;
- only inline attachments whose ContentId is still referenced by the rendered final HTML are copied;
- normal attachments are skipped by default;
- normal attachments are copied only when explicitly enabled;
- all source attachments are fetched and validated before the draft is created, preventing half-created drafts on preflight errors.

## Independence of reply target and template source

A reply may use:

```text
reply target: msg_customer_question
template source: tmpl_weekly_report
```

`reply_to_email` replies to `msg_customer_question`. `tmpl_weekly_report` only supplies reusable resources. It cannot change the reply thread or recipient semantics.

## Single body parameter

Without `template_ref`, `body_html` is complete HTML. With `template_ref`, `body_html` is only the new content fragment. `template_content_html` is intentionally absent from the public MCP, service, workflow, and CLI surfaces.
