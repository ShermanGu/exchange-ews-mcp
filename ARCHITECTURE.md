# Mail-template architecture

## Principle

Template extraction is a read concern. Draft creation and reply are write concerns. They must not be combined into overlapping Agent tools.

## Components

```text
MessageResolver
  └─ message_ref / structured search / resume_token

EmailTemplateExtractor
  ├─ GetItem Body + UniqueBody + recipients + attachment metadata
  ├─ UniqueBody-first / structural fallback history exclusion
  ├─ complete top-message HTML stored locally
  ├─ marker-based style shell stored locally
  └─ local opaque template_ref

MailComposer
  └─ compose_email(body_html, optional template_ref)

MailReplier
  └─ reply_to_email(reply target, body_html, optional template_ref)
```

## Data flow

```text
Selected source message
  ↓
extract_email_template
  ├─ compact previews + character counts
  ├─ To/CC suggestions
  └─ template_ref → complete template + shell

Agent generates only new content
  ↓
body_html fragment + template_ref
  ↓
compose_email or reply_to_email renders the complete local template
  ↓
copy only referenced cid resources from template_ref
  ↓
unsent draft
```

## Safety properties

- Template extraction performs no write.
- The template source cannot change the reply target.
- BCC is not suggested or copied.
- Normal attachments require explicit opt-in.
- Source attachment content is fetched and validated before draft creation.
- Ambiguous source selection is resumable and candidate-bound.
- Exchange ItemId and ChangeKey stay behind local opaque refs.
- Without `template_ref`, `body_html` is complete HTML; with it, `body_html` is the new content fragment.
- A single public body parameter keeps MCP, service, workflow, and CLI semantics aligned.
