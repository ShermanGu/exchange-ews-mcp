# Agent input compatibility

Mail folder inputs accept canonical EWS IDs and common display aliases.

| Friendly input | Canonical value |
|---|---|
| Inbox / 收件箱 | `inbox` |
| Drafts / 草稿箱 | `drafts` |
| Sent Items / Sent Mail / 已发送邮件 | `sentitems` |
| Deleted Items / Trash | `deleteditems` |
| Junk Email / Spam | `junkemail` |
| Outbox / 发件箱 | `outbox` |

This normalization is shared by `list_emails`, `search_emails`, `find_email`, `extract_email_template`, `reply_to_email`, and `forward_email`.

Other normalized enums include mail importance and availability attendee roles. The former template-mode enum is no longer exposed because template extraction and mail writing are decoupled.
