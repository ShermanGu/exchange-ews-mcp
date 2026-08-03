# v0.7.2 Template extraction and single-body contract

## Root cause

v0.7.0 read the complete EWS `Body`, then inferred the current visible message by scanning Outlook/Gmail HTML markers. This was brittle because Outlook-generated HTML varies by client and the MCP client can truncate a large tool result near 5000 characters.

## New pipeline

```text
GetItem Body + UniqueBody
        ↓
UniqueBody available → authoritative current-message segment
        ↓
UniqueBody unavailable → Outlook/Gmail structural fallback
        ↓
full template + shell stored in template_ref
        ↓
Agent receives compact preview + exact character counts
        ↓
compose_email/reply_to_email(template_ref, body_html=new content fragment)
        ↓
server renders into the complete stored template
```

`Body.IsTruncated` and `UniqueBody.IsTruncated` are reported separately from local character limits.

## Agent contract

For a templated new draft:

```text
extract_email_template → template_ref
compose_email(template_ref=..., body_html="<p>new content</p>")
```

For a templated reply:

```text
extract_email_template → template_ref
reply_to_email(message_ref=target, template_ref=..., body_html="<p>new reply</p>")
```

Do not rebuild a large template from `template_preview_html`.
