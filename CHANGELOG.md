# Changelog

## 0.7.2

- Removed the public `template_content_html` parameter from MCP, service, and workflow writer surfaces.
- Unified template composition around one `body_html` parameter: complete HTML without `template_ref`, new content fragment with `template_ref`.
- Fixed CLI template composition so `body_html + template_ref` performs server-side insertion into the complete stored template.
- Kept reply target selection (`message_ref`) independent from template format selection (`template_ref`).
- Updated DT coverage, Agent instructions, architecture documents, and regression tests for the single-body contract.

## 0.7.1

- `extract_email_template` now requests and prefers EWS `UniqueBody`, the Exchange-native current-message body without quoted conversation history.
- Distinguishes Exchange server truncation from local output limiting for both `Body` and `UniqueBody`.
- Large templates remain complete in `template_ref`; MCP responses return compact previews and exact character counts instead of flooding/truncating Agent tool results.
- `compose_email` and `reply_to_email` accept `template_content_html` for server-side insertion into the stored full template.
- Added classic Outlook header-block fallback detection and regression coverage for 5000-character-prefix failures.

## 0.7.0

- Replaced `compose_from_email` and `reply_from_email` with one read-only `extract_email_template` tool.
- Extended existing `compose_email` and `reply_to_email` with optional `template_ref` resource reuse.
- Made reply target and template source fully independent.
- Template extraction now uses only the selected message: quoted chains keep the top/first visible message; single-message HTML is preserved completely.
- Added exact `template_html`, marker-based `template_shell_html`, To/CC suggestions, history-boundary diagnostics, and seven-day opaque template references.
- Copies only final-HTML-referenced inline `cid:` resources by default; normal template attachments require explicit opt-in.
- Removed legacy template writer commands from MCP and CLI to reduce Agent tool-choice ambiguity.
- Retained DT group ID `template-mail-v06` for existing configuration compatibility, but rewired it to validate the decoupled architecture.

## 0.6.3

- Previous coupled template writer implementation and long-reply hotfix.
