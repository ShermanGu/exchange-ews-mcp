from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
import re
from typing import Any

from .config import AppConfig, effective_company_domains
from .ews import EwsClient
from .state_store import ReferenceStore
from .input_normalization import normalize_mail_folders, normalize_template_mode


DEFAULT_HISTORY_DAYS = 365
MAX_HISTORY_MESSAGES = 100
AGENT_TEMPLATE_PREVIEW_CHARS = 2800


def _valid_email(value: str) -> str | None:
    _, parsed = parseaddr(value.strip())
    return parsed if parsed and "@" in parsed else None


def _email_parts(email: str | None) -> tuple[str | None, str | None]:
    if not email or "@" not in email:
        return None, None
    local, domain = email.rsplit("@", 1)
    return local.casefold(), domain.casefold()


def _stamp(item: dict[str, Any]) -> str:
    return str(
        item.get("received_at")
        or item.get("sent_at")
        or item.get("last_modified_at")
        or item.get("created_at")
        or ""
    )


def _after_days(days: int) -> str:
    value = datetime.now(timezone.utc) - timedelta(days=days)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


TEMPLATE_CONTENT_START = "<!-- EWS-MCP-CONTENT-START -->"
TEMPLATE_CONTENT_END = "<!-- EWS-MCP-CONTENT-END -->"


def _render_template_body(
    source_html: str,
    *,
    mode: str,
    new_content_html: str | None,
) -> tuple[str, str]:
    normalized_mode = normalize_template_mode(mode)
    if normalized_mode == "clone":
        if new_content_html is not None and new_content_html.strip():
            raise ValueError("mode=clone 时不要提供 new_content_html。")
        if not source_html.strip():
            raise ValueError("来源邮件没有可复制的 HTML 正文。")
        return source_html, "clone_exact_html"
    replacement = (new_content_html or "").strip()
    if normalized_mode == "rendered_html":
        if not replacement:
            raise ValueError("mode=rendered_html 时 new_content_html 不能为空。")
        return replacement, "agent_rendered_complete_html"
    if normalized_mode != "replace_content":
        raise ValueError("mode 只支持 clone、replace_content 或 rendered_html。")
    if not replacement:
        raise ValueError("mode=replace_content 时 new_content_html 不能为空。")

    start = source_html.find(TEMPLATE_CONTENT_START)
    end = source_html.find(TEMPLATE_CONTENT_END)
    if 0 <= start < end:
        content_start = start + len(TEMPLATE_CONTENT_START)
        return (
            source_html[:content_start]
            + "\n"
            + replacement
            + "\n"
            + source_html[end:],
            "explicit_content_markers",
        )

    body_pattern = re.compile(r"(<body\b[^>]*>)(.*?)(</body\s*>)", re.IGNORECASE | re.DOTALL)
    match = body_pattern.search(source_html)
    if match:
        rendered = (
            source_html[: match.start()]
            + match.group(1)
            + replacement
            + match.group(3)
            + source_html[match.end() :]
        )
        return rendered, "replace_body_preserve_head_and_body_attributes"
    return replacement, "replace_entire_body_no_html_wrapper"


_REPLY_HISTORY_BOUNDARY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("outlook_reply_forward_div", re.compile(r"<(?:div|span)\b[^>]*\bid\s*=\s*([\"'])divRplyFwdMsg\1[^>]*>", re.IGNORECASE)),
    ("outlook_stop_spelling_hr", re.compile(r"<hr\b[^>]*\bid\s*=\s*([\"'])stopSpelling\1[^>]*>", re.IGNORECASE)),
    ("outlook_message_header", re.compile(r"<(?:div|table)\b[^>]*\bclass\s*=\s*([\"'])[^\"']*OutlookMessageHeader[^\"']*\1[^>]*>", re.IGNORECASE)),
    ("html_cite_blockquote", re.compile(r"<blockquote\b[^>]*\btype\s*=\s*([\"'])cite\1[^>]*>", re.IGNORECASE)),
    ("gmail_quote", re.compile(r"<div\b[^>]*\bclass\s*=\s*([\"'])[^\"']*gmail_quote[^\"']*\1[^>]*>", re.IGNORECASE)),
    ("outlook_web_reference_container", re.compile(r"<(?:div|section)\b[^>]*\bid\s*=\s*([\"'])mail-editor-reference-message-container\1[^>]*>", re.IGNORECASE)),
    ("outlook_mail_original", re.compile(r"<(?:div|section)\b[^>]*\bname\s*=\s*([\"'])_MailOriginal\1[^>]*>", re.IGNORECASE)),
    ("outlook_classic_header_block", re.compile(
        r"<(?:div|table)\b[^>]*\bstyle\s*=\s*([\"'])[^\"']*border-top[^\"']*\1[^>]*>"
        r"(?:(?!</(?:div|table)>).){0,5000}?"
        r"(?:From|发件人|寄件者|差出人)\s*:"
        r"(?:(?!</(?:div|table)>).){0,3000}?"
        r"(?:Sent|发送时间|寄件日時|送信日時)\s*:"
        r"(?:(?!</(?:div|table)>).){0,3000}?"
        r"(?:To|收件人|宛先)\s*:"
        r"(?:(?!</(?:div|table)>).){0,3000}?"
        r"(?:Subject|主题|件名)\s*:",
        re.IGNORECASE | re.DOTALL,
    )),
    ("original_message_separator", re.compile(r"(?:-{5,}|_{5,})\s*(?:Original Message|原始邮件|原邮件|邮件原文)\s*(?:-{5,}|_{5,})", re.IGNORECASE)),
)

_VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def _balance_html_fragment(fragment: str) -> str:
    """Close open tags in a truncated HTML body fragment without changing its content."""
    stack: list[str] = []
    tag_re = re.compile(r"<!--.*?-->|<![^>]*>|<\?[^>]*\?>|</?([A-Za-z][A-Za-z0-9:-]*)\b[^>]*>", re.DOTALL)
    for match in tag_re.finditer(fragment):
        token = match.group(0)
        name = (match.group(1) or "").casefold()
        if not name or token.startswith(("<!--", "<!", "<?")):
            continue
        if token.startswith("</"):
            if name in stack:
                while stack:
                    opened = stack.pop()
                    if opened == name:
                        break
            continue
        if name in _VOID_HTML_TAGS or token.rstrip().endswith("/>"):
            continue
        stack.append(name)
    return fragment + "".join(f"</{name}>" for name in reversed(stack))


def _html_body_inner(source_html: str) -> str:
    match = re.search(r"<body\b[^>]*>(.*)</body\s*>", source_html, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else source_html


def _visible_html_text(value: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    text = re.sub(r"<(?:style|script)\b.*?</(?:style|script)\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _combine_unique_body_with_document_shell(source_html: str, unique_html: str) -> str:
    """Place Exchange UniqueBody into the original document shell.

    UniqueBody is commonly an HTML fragment.  The full Body still carries useful
    <head>/<style> content and body attributes, so retain that shell while using
    Exchange's conversation-aware unique fragment as the template content.
    """
    if re.search(r"<(?:html|body)\b", unique_html, re.IGNORECASE):
        return unique_html
    match = re.search(r"(<body\b[^>]*>)(.*?)(</body\s*>)", source_html, re.IGNORECASE | re.DOTALL)
    if not match:
        return unique_html
    return (
        source_html[: match.start(2)]
        + unique_html
        + source_html[match.end(2) :]
    )


def _unique_body_removed_history(source_html: str, unique_html: str) -> bool:
    full_text = _visible_html_text(_html_body_inner(source_html))
    unique_text = _visible_html_text(unique_html)
    if not full_text or not unique_text or full_text == unique_text:
        return False
    # Exchange may normalize whitespace/markup.  Treat a materially shorter
    # unique body as proof that quoted conversation history was excluded.
    if unique_text in full_text:
        return len(full_text) > len(unique_text) + 32
    return len(unique_text) < int(len(full_text) * 0.92)


def _agent_safe_html(value: str) -> tuple[str | None, str, bool]:
    """Return full HTML only when it is small enough for an MCP tool result."""
    if len(value) <= AGENT_TEMPLATE_PREVIEW_CHARS:
        return value, value, False
    return None, value[:AGENT_TEMPLATE_PREVIEW_CHARS], True


def _latest_message_template_html(
    source_html: str,
    *,
    source_truncated: bool = False,
    unique_body_html: str | None = None,
    unique_body_truncated: bool = False,
) -> tuple[str, str, bool]:
    """Return only the newest visible message segment from a quoted mail body.

    The selected EWS item can contain an entire quoted conversation.  This helper
    removes the first detected reply/forward boundary and everything below it,
    while retaining the document head, body attributes, and balanced HTML above
    the boundary.
    """
    unique = (unique_body_html or "").strip()
    if unique:
        rendered = _combine_unique_body_with_document_shell(source_html, unique)
        removed = _unique_body_removed_history(source_html, unique)
        strategy = "ews_unique_body_truncated" if unique_body_truncated else "ews_unique_body"
        return _balance_html_fragment(rendered) if unique_body_truncated else rendered, strategy, removed

    if not source_html.strip():
        raise ValueError("格式来源邮件没有 HTML 正文。")
    body_pattern = re.compile(r"(<body\b[^>]*>)(.*?)(</body\s*>)", re.IGNORECASE | re.DOTALL)
    body_match = body_pattern.search(source_html)
    if body_match:
        search_text = body_match.group(2)
        prefix = source_html[: body_match.start(2)]
        suffix = source_html[body_match.end(2) :]
    else:
        search_text = source_html
        prefix = ""
        suffix = ""

    candidates: list[tuple[int, str]] = []
    for strategy, pattern in _REPLY_HISTORY_BOUNDARY_PATTERNS:
        match = pattern.search(search_text)
        if match:
            candidates.append((match.start(), strategy))
    if not candidates:
        if source_truncated:
            return (
                _balance_html_fragment(source_html),
                "latest_segment_truncated_no_history_boundary",
                False,
            )
        return source_html, "latest_segment_no_history_boundary", False

    boundary, strategy = min(candidates, key=lambda item: item[0])
    newest = search_text[:boundary].rstrip()
    if not re.sub(r"<[^>]+>", "", newest).strip():
        return source_html, f"{strategy}_boundary_ignored_empty_prefix", False
    newest = _balance_html_fragment(newest)
    return prefix + newest + suffix, strategy, True


def _render_reply_template_body(
    latest_message_html: str,
    *,
    mode: str,
    new_content_html: str | None,
) -> tuple[str, str]:
    """Render a reply body from one newest-message segment, never a full chain."""
    normalized_mode = normalize_template_mode(mode)
    if normalized_mode != "replace_content":
        return _render_template_body(
            latest_message_html, mode=normalized_mode, new_content_html=new_content_html
        )

    replacement = (new_content_html or "").strip()
    if not replacement:
        raise ValueError("mode=replace_content 时 new_content_html 不能为空。")
    start = latest_message_html.find(TEMPLATE_CONTENT_START)
    end = latest_message_html.find(TEMPLATE_CONTENT_END)
    if 0 <= start < end:
        return _render_template_body(
            latest_message_html, mode="replace_content", new_content_html=replacement
        )

    body_pattern = re.compile(r"(<body\b[^>]*>)(.*?)(</body\s*>)", re.IGNORECASE | re.DOTALL)
    match = body_pattern.search(latest_message_html)
    if not match:
        return replacement, "reply_replace_entire_body_no_html_wrapper"

    inner = match.group(2)
    leading = re.match(r"\s*(?:<!--.*?-->\s*)*", inner, flags=re.DOTALL)
    content_start = leading.end() if leading else 0
    first = re.match(
        r"<(?P<tag>div|section|article|main|span|font|p)\b(?P<attrs>[^>]*)>",
        inner[content_start:],
        flags=re.IGNORECASE | re.DOTALL,
    )
    rendered_inner = replacement
    strategy = "reply_replace_body_preserve_head_and_body_attributes"
    if first:
        tag = first.group("tag")
        attrs = first.group("attrs")
        if re.search(r"\b(?:style|class|dir|lang)\s*=", attrs, flags=re.IGNORECASE):
            rendered_inner = f"<{tag}{attrs}>{replacement}</{tag}>"
            strategy = f"reply_replace_body_preserve_{tag.casefold()}_style_shell"

    rendered = (
        latest_message_html[: match.start()]
        + match.group(1)
        + rendered_inner
        + match.group(3)
        + latest_message_html[match.end() :]
    )
    return rendered, strategy


def _referenced_content_ids(body_html: str) -> set[str]:
    values = re.findall(r"cid:\s*<?([^\"'<>\s)]+)>?", body_html, flags=re.IGNORECASE)
    return {value.strip("<>").casefold() for value in values if value.strip("<>")}


def _mailbox_emails(values: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    emails: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for mailbox in values:
        raw = str(mailbox.get("email") or "").strip()
        parsed = _valid_email(raw)
        if not parsed:
            label = mailbox.get("name") or raw or "unknown"
            warnings.append(f"无法复制非 SMTP 收件人：{label}")
            continue
        key = parsed.casefold()
        if key not in seen:
            emails.append(parsed)
            seen.add(key)
    return emails, warnings


class SemanticMailWorkflow:
    """Deterministic semantic mail workflow built on top of EWS core primitives."""

    def __init__(self, client: EwsClient, store: ReferenceStore, config: AppConfig) -> None:
        self.client = client
        self.store = store
        self.config = config
        self.company_domains = set(effective_company_domains(config))

    def _person_ref(self, candidate: dict[str, Any]) -> str | None:
        email = str(candidate.get("email") or "").strip()
        if not email:
            return None
        return self.store.upsert_reference(
            kind="person",
            external_key=email.casefold(),
            payload={
                "display_name": candidate.get("display_name"),
                "email": email,
                "routing_type": candidate.get("routing_type"),
                "mailbox_type": candidate.get("mailbox_type"),
            },
            ttl_days=30,
        )

    def _message_ref(self, item: dict[str, Any]) -> str | None:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            return None
        return self.store.upsert_reference(
            kind="message",
            external_key=item_id,
            payload={
                "item_id": item_id,
                "change_key": item.get("change_key"),
                "subject": item.get("subject"),
                "folder": item.get("folder"),
                "conversation_id": item.get("conversation_id"),
                "internet_message_id": item.get("internet_message_id"),
            },
            ttl_days=7,
        )

    def _draft_dict(self, result: Any) -> dict[str, Any]:
        data = result.as_dict()
        data["draft_ref"] = self.store.upsert_reference(
            kind="draft",
            external_key=result.item_id,
            payload={
                "item_id": result.item_id,
                "change_key": result.change_key,
                "subject": result.subject,
                "folder": "drafts",
                "draft_type": result.draft_type,
            },
            ttl_days=30,
        )
        return data

    def _communication_stats(
        self,
        email: str,
        display_name: str | None,
        *,
        lookback_days: int,
    ) -> dict[str, Any]:
        after = _after_days(lookback_days)
        collected: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        def collect(page: dict[str, Any], direction: str | None = None) -> None:
            for index, item in enumerate(page.get("items") or []):
                item_direction = direction
                if item_direction is None:
                    item_direction = (
                        "sent_to_candidate"
                        if item.get("folder") == "sentitems"
                        else "received_from_candidate"
                    )
                key = str(item.get("item_id") or f"{item_direction}:{index}:{_stamp(item)}")
                existing = collected.setdefault(key, {**item, "directions": []})
                if item_direction not in existing["directions"]:
                    existing["directions"].append(item_direction)

        try:
            collect(
                self.client.search_emails_multi_folder(
                    folders=["inbox", "sentitems"],
                    participant_contains=email,
                    after=after,
                    limit=MAX_HISTORY_MESSAGES,
                    offset=0,
                )
            )
        except Exception as exc:
            warnings.append(f"cross-folder email lookup: {exc}")

        has_sent_match = any(
            "sent_to_candidate" in item["directions"] for item in collected.values()
        )
        if not has_sent_match and display_name and display_name.strip():
            try:
                collect(
                    self.client.search_emails(
                        folder="sentitems",
                        participant_contains=display_name.strip(),
                        after=after,
                        limit=MAX_HISTORY_MESSAGES,
                        offset=0,
                    ),
                    "sent_to_candidate",
                )
            except Exception as exc:
                warnings.append(f"sent display-name lookup {display_name!r}: {exc}")

        items = list(collected.values())
        received_count = sum("received_from_candidate" in item["directions"] for item in items)
        sent_count = sum("sent_to_candidate" in item["directions"] for item in items)
        return {
            "communication_count": len(items),
            "received_from_count": received_count,
            "sent_to_count": sent_count,
            "last_communication_at": max((_stamp(item) for item in items), default=None),
            "communication_lookup_warning": "; ".join(warnings) if warnings else None,
        }

    def resolve_people(
        self,
        *,
        query: str,
        limit: int = 100,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
        auto_select: bool = True,
    ) -> dict[str, Any]:
        """Resolve a romanized name stem or one complete SMTP address.

        A query without ``@`` is always treated as a romanized *person name*, not as
        an exact mailbox local-part. All returned company addresses whose local-part
        starts with that name are peers and must be compared by communication history.
        A query containing ``@`` is treated as one exact SMTP address.
        """
        normalized = query.strip()
        if not normalized:
            raise ValueError("query 不能为空。")
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")
        if not 1 <= lookback_days <= 3650:
            raise ValueError("lookback_days 必须在 1 到 3650 之间。")

        raw = self.client.resolve_names(query=normalized, limit=limit)
        if raw.get("status") == "romanized_query_required":
            return {
                **raw,
                "query_mode": "romanized_name",
                "selection_status": "needs_romanized_query",
                "selected": None,
                "default_rule_applied": None,
                "ambiguity_reason": None,
                "user_notice": raw.get("message"),
            }

        query_email = _valid_email(normalized) if "@" in normalized else None
        query_mode = "exact_email" if query_email else "romanized_name"
        query_name = normalized.casefold()
        unfiltered = list(raw.get("candidates") or [])
        matched: list[dict[str, Any]] = []

        for raw_candidate in unfiltered:
            candidate = dict(raw_candidate)
            email = str(candidate.get("email") or "").strip()
            local, domain = _email_parts(email)

            if query_mode == "exact_email":
                if not query_email or not email or email.casefold() != query_email.casefold():
                    continue
                match_reason = "exact_email"
            else:
                # A bare romanized name is a name stem, never an exact local-part.
                # Keep every matching employee across every configured company domain.
                if not local or not local.startswith(query_name):
                    continue
                if self.company_domains and (not domain or domain not in self.company_domains):
                    continue
                match_reason = "romanized_name_stem"

            candidate["person_ref"] = self._person_ref(candidate)
            candidate["email_local_part"] = local
            candidate["email_domain"] = domain
            candidate["is_company_address"] = bool(domain and domain in self.company_domains)
            candidate["match_reason"] = match_reason
            candidate["exact_email_match"] = bool(
                query_email and email.casefold() == query_email.casefold()
            )
            candidate["romanized_name_stem_match"] = bool(
                query_mode == "romanized_name" and local and local.startswith(query_name)
            )

            if email:
                candidate.update(
                    self._communication_stats(
                        email,
                        str(candidate.get("display_name") or "") or None,
                        lookback_days=lookback_days,
                    )
                )
            else:
                candidate.update(
                    {
                        "communication_count": 0,
                        "received_from_count": 0,
                        "sent_to_count": 0,
                        "last_communication_at": None,
                        "communication_lookup_warning": None,
                    }
                )
            matched.append(candidate)

        matched.sort(
            key=lambda item: (
                int(item.get("communication_count") or 0),
                str(item.get("last_communication_at") or ""),
                str(item.get("email_local_part") or ""),
                str(item.get("email_domain") or ""),
            ),
            reverse=True,
        )

        selected: dict[str, Any] | None = None
        rule: str | None = None
        notice: str | None = None
        ambiguity_reason: str | None = None
        correspondents = [
            item for item in matched if int(item.get("communication_count") or 0) > 0
        ]

        if auto_select and matched:
            if query_mode == "exact_email" and len(matched) == 1:
                selected, rule = matched[0], "exact_email"
            elif query_mode == "romanized_name" and len(matched) == 1:
                selected, rule = matched[0], "single_candidate"
            elif query_mode == "romanized_name" and len(correspondents) == 1:
                selected, rule = correspondents[0], "unique_prior_correspondent"
                notice = (
                    f"查询 {normalized!r} 返回 {len(matched)} 个同级候选；只有 "
                    f"{selected.get('display_name') or selected.get('email')} 与当前用户存在邮件来往，"
                    "已按默认规则自动选择。"
                )

        if selected is not None:
            selection_status = (
                "auto_selected" if rule == "unique_prior_correspondent" else "resolved"
            )
        elif matched:
            selection_status = "needs_confirmation"
            if query_mode == "romanized_name":
                if len(correspondents) >= 2:
                    ambiguity_reason = "multiple_prior_correspondents"
                    notice = (
                        f"查询 {normalized!r} 返回 {len(matched)} 个同级候选，其中 "
                        f"{len(correspondents)} 个与你有邮件来往，需要你确认具体收件人。"
                    )
                elif len(correspondents) == 0 and len(matched) > 1:
                    ambiguity_reason = "no_prior_correspondent"
                    notice = (
                        f"查询 {normalized!r} 返回 {len(matched)} 个同级候选，"
                        "但都没有查到历史邮件来往，需要你确认具体收件人。"
                    )
            elif query_mode == "exact_email":
                ambiguity_reason = "duplicate_exact_email"
        else:
            selection_status = "not_found"

        return {
            **raw,
            "status": "resolved" if matched else "not_found",
            "query_mode": query_mode,
            "company_email_domains": sorted(self.company_domains),
            "lookback_days": lookback_days,
            "unfiltered_candidate_count": len(unfiltered),
            "returned": len(matched),
            "candidates": matched,
            "selected": selected,
            "selection_status": selection_status,
            "default_rule_applied": rule,
            "ambiguity_reason": ambiguity_reason,
            "prior_correspondent_count": len(correspondents),
            "user_notice": notice,
        }

    def _resolve_recipient_slot(
        self,
        query: str,
        *,
        lookback_days: int,
    ) -> dict[str, Any]:
        direct = _valid_email(query)
        result = self.resolve_people(query=query, lookback_days=lookback_days)
        selected = result.get("selected")
        if selected:
            return {
                "query": query,
                "status": "resolved",
                "email": selected.get("email"),
                "person_ref": selected.get("person_ref"),
                "selected": selected,
                "default_rule_applied": result.get("default_rule_applied"),
                "user_notice": result.get("user_notice"),
                "candidates": result.get("candidates") or [],
            }
        if direct and result.get("selection_status") == "not_found":
            _, domain = _email_parts(direct)
            return {
                "query": query,
                "status": "resolved",
                "email": direct,
                "person_ref": None,
                "selected": {
                    "display_name": None,
                    "email": direct,
                    "is_company_address": bool(domain and domain in self.company_domains),
                    "resolution_source": "direct_email",
                },
                "default_rule_applied": "direct_email",
                "user_notice": None,
                "candidates": [],
            }
        return {
            "query": query,
            "status": result.get("selection_status"),
            "email": None,
            "person_ref": None,
            "selected": None,
            "default_rule_applied": None,
            "user_notice": result.get("user_notice"),
            "candidates": result.get("candidates") or [],
            "message": result.get("message"),
        }

    def _resolve_slots(
        self,
        *,
        to_queries: list[str],
        cc_queries: list[str],
        bcc_queries: list[str],
        lookback_days: int,
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {"to": [], "cc": [], "bcc": []}
        for field, queries in (("to", to_queries), ("cc", cc_queries), ("bcc", bcc_queries)):
            for query in queries:
                result[field].append(
                    self._resolve_recipient_slot(query.strip(), lookback_days=lookback_days)
                )
        return result

    @staticmethod
    def _slot_addresses(slots: dict[str, list[dict[str, Any]]], field: str) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for slot in slots[field]:
            email = str(slot.get("email") or "").strip()
            key = email.casefold()
            if email and key not in seen:
                values.append(email)
                seen.add(key)
        return values

    @staticmethod
    def _pending_slots(slots: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for field, values in slots.items():
            for index, slot in enumerate(values):
                if slot.get("status") != "resolved":
                    pending.append({"field": field, "index": index, **slot})
        return pending

    def _execute_compose(self, payload: dict[str, Any], slots: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        to = self._slot_addresses(slots, "to")
        cc = self._slot_addresses(slots, "cc")
        bcc = self._slot_addresses(slots, "bcc")
        if not to and not cc and not bcc:
            raise ValueError("至少需要一个已解析的 To、CC 或 BCC 收件人。")

        attachment_paths: list[str] = []
        validate_attachment = getattr(self.client, "validate_attachment_path", None)
        for path_value in payload.get("attachments") or []:
            raw_path = str(path_value)
            attachment_paths.append(
                str(validate_attachment(raw_path)) if callable(validate_attachment) else raw_path
            )

        prepared, template_warnings, template_payload = self._prepare_template_attachments(
            template_ref=payload.get("template_ref"),
            rendered_body_html=str(payload["body_html"]),
            copy_inline_images=bool(payload.get("copy_template_inline_images", True)),
            copy_attachments=bool(payload.get("copy_template_attachments", False)),
        )
        if prepared and not callable(getattr(self.client, "add_attachment_content_to_draft", None)):
            raise ValueError("当前 EWS Client 不支持复制模板附件。")

        draft = self.client.create_draft(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=str(payload["subject"]),
            body_html=str(payload["body_html"]),
        )
        data = self._draft_dict(draft)
        local_attachments: list[dict[str, Any]] = []
        current_item_id = draft.item_id
        current_change_key = draft.change_key
        for path_value in attachment_paths:
            attached = self.client.add_attachment_to_draft(
                item_id=current_item_id,
                change_key=current_change_key,
                file_path=path_value,
            )
            current_item_id = attached.root_item_id
            current_change_key = attached.root_item_change_key
            local_attachments.append(attached.as_dict())

        current_item_id, current_change_key, copied = self._copy_prepared_attachments_to_draft(
            item_id=current_item_id,
            change_key=current_change_key,
            prepared=prepared,
            context="模板",
        )
        if local_attachments or copied:
            data["item_id"] = current_item_id
            data["change_key"] = current_change_key
            data["draft_ref"] = self.store.upsert_reference(
                kind="draft",
                external_key=current_item_id,
                payload={
                    "item_id": current_item_id,
                    "change_key": current_change_key,
                    "subject": payload["subject"],
                    "folder": "drafts",
                    "draft_type": "new",
                },
                ttl_days=30,
            )
        notices = [
            str(slot["user_notice"])
            for values in slots.values()
            for slot in values
            if slot.get("user_notice")
        ]
        inline_count = sum(bool(item.get("is_inline")) for item in copied)
        return {
            "status": "draft_created",
            "draft": data,
            "recipient_resolution": slots,
            "default_rule_notices": notices,
            "attachments": local_attachments,
            "template_ref": payload.get("template_ref"),
            "template_render_strategy": payload.get("template_render_strategy"),
            "template_source": (
                {
                    "message_ref": template_payload.get("source_message_ref"),
                    "subject": template_payload.get("source_subject"),
                }
                if template_payload else None
            ),
            "template_inline_images_copied": inline_count,
            "template_attachments_copied": len(copied) - inline_count,
            "copied_template_attachments": copied,
            "template_attachment_warnings": template_warnings,
            "sent": False,
        }

    def compose_email(
        self,
        *,
        to_queries: list[str],
        subject: str,
        body_html: str | None = None,
        cc_queries: list[str] | None = None,
        bcc_queries: list[str] | None = None,
        attachments: list[str] | None = None,
        template_ref: str | None = None,
        copy_template_inline_images: bool = True,
        copy_template_attachments: bool = False,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        if not subject.strip():
            raise ValueError("subject 不能为空。")
        rendered_body_html, template_render_strategy = self._resolve_template_body_input(
            body_html=body_html,
            template_ref=template_ref,
        )
        if not to_queries and not cc_queries and not bcc_queries:
            raise ValueError("至少提供一个收件人查询。")
        payload = {
            "subject": subject.strip(),
            "body_html": rendered_body_html,
            "template_render_strategy": template_render_strategy,
            "attachments": [str(Path(item)) for item in (attachments or [])],
            "template_ref": template_ref,
            "copy_template_inline_images": copy_template_inline_images,
            "copy_template_attachments": copy_template_attachments,
            "lookback_days": lookback_days,
        }
        slots = self._resolve_slots(
            to_queries=to_queries,
            cc_queries=cc_queries or [],
            bcc_queries=bcc_queries or [],
            lookback_days=lookback_days,
        )
        pending = self._pending_slots(slots)
        if pending:
            token = self.store.create_action_session(
                {
                    "action": "compose_email",
                    "payload": payload,
                    "recipient_slots": slots,
                },
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "pending": pending,
                "resolved_recipients": slots,
                "sent": False,
            }
        return self._execute_compose(payload, slots)

    def _prepare_source_attachments(
        self,
        *,
        source: dict[str, Any],
        rendered_body_html: str,
        keep_inline_images: bool,
        keep_attachments: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        selected_metadata: list[dict[str, Any]] = []
        warnings: list[str] = []
        referenced_cids = _referenced_content_ids(rendered_body_html)
        for metadata in source.get("attachments") or []:
            kind = str(metadata.get("type") or "")
            attachment_id = str(metadata.get("attachment_id") or "").strip()
            is_inline = bool(metadata.get("is_inline"))
            content_id = str(metadata.get("content_id") or "").strip()
            if kind != "FileAttachment":
                if keep_attachments or is_inline:
                    warnings.append(
                        f"未复制不支持的 {kind or 'unknown'}：{metadata.get('name') or attachment_id}"
                    )
                continue
            if not attachment_id:
                warnings.append(f"附件缺少 attachment_id：{metadata.get('name')}")
                continue
            if is_inline:
                if not keep_inline_images:
                    continue
                if not content_id:
                    warnings.append(f"内联附件缺少 ContentId，未复制：{metadata.get('name')}")
                    continue
                if content_id.strip("<>").casefold() not in referenced_cids:
                    continue
                selected_metadata.append(dict(metadata))
            elif keep_attachments:
                selected_metadata.append(dict(metadata))

        if not selected_metadata:
            return [], warnings
        fetch = getattr(self.client, "get_attachments", None)
        if not callable(fetch):
            raise ValueError("当前 EWS Client 不支持读取来源附件内容。")
        contents = fetch(
            attachment_ids=[str(item["attachment_id"]) for item in selected_metadata]
        )
        by_id = {str(item.attachment_id): item for item in contents}
        prepared: list[dict[str, Any]] = []
        for metadata in selected_metadata:
            attachment_id = str(metadata["attachment_id"])
            content_item = by_id.get(attachment_id)
            if content_item is None:
                raise ValueError(f"来源附件内容未返回：{attachment_id}")
            if content_item.attachment_type != "FileAttachment" or content_item.content is None:
                warnings.append(
                    f"未复制无文件内容的附件：{content_item.filename or attachment_id}"
                )
                continue
            filename = content_item.filename or str(metadata.get("name") or "attachment.bin")
            candidate = {
                "source_attachment_id": attachment_id,
                "filename": filename,
                "content_type": content_item.content_type or metadata.get("content_type"),
                "content": content_item.content,
                "is_inline": bool(content_item.is_inline or metadata.get("is_inline")),
                "content_id": str(content_item.content_id or metadata.get("content_id") or "").strip("<>") or None,
            }
            validate_content = getattr(self.client, "validate_attachment_content", None)
            if callable(validate_content):
                validated = validate_content(
                    filename=str(candidate["filename"]),
                    content=bytes(candidate["content"]),
                    content_type=candidate.get("content_type"),
                    is_inline=bool(candidate.get("is_inline")),
                    content_id=candidate.get("content_id"),
                )
                candidate.update(validated)
            prepared.append(candidate)
        return prepared, warnings

    def _load_template_reference(self, template_ref: str) -> dict[str, Any]:
        stored = self.store.get_reference(template_ref, expected_kind="template")
        payload = dict(stored.payload)
        if not payload.get("source_item_id"):
            raise ValueError("template_ref 缺少来源邮件信息，请重新提取模板。")
        return payload

    def _resolve_template_body_input(
        self,
        *,
        body_html: str | None,
        template_ref: str | None,
    ) -> tuple[str, str | None]:
        content = (body_html or "").strip()
        if not content:
            raise ValueError("body_html 不能为空。")
        if not template_ref:
            return content, None
        payload = self._load_template_reference(template_ref)
        shell = str(payload.get("template_shell_html") or payload.get("template_html") or "")
        if not shell.strip():
            raise ValueError("template_ref 中没有可渲染的 HTML 模板，请重新提取。")
        rendered, strategy = _render_template_body(
            shell,
            mode="replace_content",
            new_content_html=content,
        )
        return rendered, strategy

    def _prepare_template_attachments(
        self,
        *,
        template_ref: str | None,
        rendered_body_html: str,
        copy_inline_images: bool,
        copy_attachments: bool,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        if not template_ref:
            return [], [], None
        payload = self._load_template_reference(template_ref)
        source = {
            "attachments": list(payload.get("attachments") or []),
        }
        prepared, warnings = self._prepare_source_attachments(
            source=source,
            rendered_body_html=rendered_body_html,
            keep_inline_images=copy_inline_images,
            keep_attachments=copy_attachments,
        )
        return prepared, warnings, payload

    def _copy_prepared_attachments_to_draft(
        self,
        *,
        item_id: str,
        change_key: str | None,
        prepared: list[dict[str, Any]],
        context: str,
    ) -> tuple[str, str | None, list[dict[str, Any]]]:
        if not prepared:
            return item_id, change_key, []
        add_content = getattr(self.client, "add_attachment_content_to_draft", None)
        if not callable(add_content):
            raise ValueError(f"当前 EWS Client 不支持复制{context}附件。")
        current_item_id = item_id
        current_change_key = change_key
        copied: list[dict[str, Any]] = []
        for index, item in enumerate(prepared):
            if not current_change_key:
                refresh = getattr(self.client, "get_item_identity", None)
                if not callable(refresh):
                    raise ValueError(f"复制{context}附件时缺少 ChangeKey，且无法刷新草稿身份。")
                identity = refresh(item_id=current_item_id)
                current_item_id = str(identity.get("item_id") or current_item_id)
                current_change_key = identity.get("change_key")
                if not current_change_key:
                    raise ValueError(f"复制{context}附件时无法获取草稿最新 ChangeKey。")
            attached = add_content(
                item_id=current_item_id,
                change_key=current_change_key,
                filename=item["filename"],
                content=item["content"],
                content_type=item.get("content_type"),
                is_inline=bool(item.get("is_inline")),
                content_id=item.get("content_id"),
                verify_draft=False,
            )
            current_item_id = attached.root_item_id
            current_change_key = attached.root_item_change_key
            detail = attached.as_dict()
            detail.update(
                {
                    "source_attachment_id": item["source_attachment_id"],
                    "is_inline": bool(item.get("is_inline")),
                    "content_id": item.get("content_id"),
                }
            )
            copied.append(detail)
        if copied and not current_change_key:
            refresh = getattr(self.client, "get_item_identity", None)
            if callable(refresh):
                identity = refresh(item_id=current_item_id)
                current_item_id = str(identity.get("item_id") or current_item_id)
                current_change_key = identity.get("change_key")
        return current_item_id, current_change_key, copied

    def extract_email_template(
        self,
        *,
        message_ref: str | None = None,
        folders: list[str] | None = None,
        sender_query: str | None = None,
        participant_query: str | None = None,
        subject_contains: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        """Extract one reusable HTML template without creating or replying to mail.

        If the selected message contains a quoted reply chain, only the top/newest
        visible message segment is retained.  If no reply-history boundary exists,
        the complete selected message body is treated as the template example.
        """
        normalized_folders = (
            normalize_mail_folders(folders) if folders else ["inbox", "sentitems"]
        )
        payload = {
            "message_ref": message_ref,
            "folders": normalized_folders,
            "sender_query": sender_query,
            "participant_query": participant_query,
            "subject_contains": subject_contains,
            "after": after,
            "before": before,
            "limit": limit,
            "lookback_days": lookback_days,
        }
        resolved = self._resolve_message_for_action(payload)
        if resolved.get("status") != "resolved":
            if resolved.get("status") == "not_found":
                return {"status": "not_found", "message_resolution": resolved}
            token = self.store.create_action_session(
                {
                    "action": "extract_email_template",
                    "payload": payload,
                    "message_resolution": resolved,
                },
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "message_resolution": resolved,
            }

        source = self.client.get_email(
            item_id=str(resolved["item_id"]),
            change_key=resolved.get("change_key"),
            max_body_chars=500000,
        )
        source_html = str(source.get("body_html") or "")
        source_truncated = bool(source.get("body_truncated"))
        unique_body_html = str(source.get("unique_body_html") or "")
        unique_body_truncated = bool(source.get("unique_body_truncated"))
        latest_html, boundary_strategy, history_removed = _latest_message_template_html(
            source_html,
            source_truncated=source_truncated,
            unique_body_html=unique_body_html,
            unique_body_truncated=unique_body_truncated,
        )
        marker = (
            f"{TEMPLATE_CONTENT_START}\n"
            "<!-- Agent: replace this marker block with the new email content. -->\n"
            f"{TEMPLATE_CONTENT_END}"
        )
        shell_html, shell_strategy = _render_reply_template_body(
            latest_html,
            mode="replace_content",
            new_content_html=marker,
        )
        to, to_warnings = _mailbox_emails(list(source.get("to") or []))
        cc, cc_warnings = _mailbox_emails(list(source.get("cc") or []))
        attachment_metadata = [dict(item) for item in (source.get("attachments") or [])]
        inline_ids = sorted(_referenced_content_ids(latest_html))
        warnings = to_warnings + cc_warnings
        if unique_body_html:
            if unique_body_truncated:
                warnings.append(
                    "Exchange 返回的 UniqueBody 标记为截断；模板仍优先使用该会话唯一正文，"
                    "并已平衡未闭合标签。"
                )
        elif source_truncated:
            if history_removed:
                warnings.append(
                    "Exchange 未返回 UniqueBody；已在完整 Body 的读取范围内识别历史分隔线，"
                    "模板仅保留顶部当前邮件。"
                )
            else:
                warnings.append(
                    "Exchange 未返回 UniqueBody，且完整 Body 在读取范围内未发现历史分隔线；"
                    "当前模板使用读取到的单封邮件片段，并已平衡未闭合标签。"
                )

        template_payload = {
            "source_message_ref": resolved.get("message_ref"),
            "source_item_id": source.get("item_id") or resolved.get("item_id"),
            "source_change_key": source.get("change_key") or resolved.get("change_key"),
            "source_subject": source.get("subject"),
            "source_sent_at": source.get("sent_at"),
            "source_received_at": source.get("received_at"),
            "source_folder": source.get("folder"),
            "template_html": latest_html,
            "template_shell_html": shell_html,
            "attachments": attachment_metadata,
            "to": to,
            "cc": cc,
            "history_boundary_strategy": boundary_strategy,
            "quoted_history_excluded": history_removed,
            "source_body_truncated": source_truncated,
            "source_body_server_truncated": bool(source.get("body_server_truncated")),
            "source_body_local_truncated": bool(source.get("body_local_truncated")),
            "unique_body_available": bool(unique_body_html),
            "unique_body_truncated": unique_body_truncated,
            "unique_body_type": source.get("unique_body_type"),
        }
        source_key = str(template_payload["source_item_id"])
        template_ref = self.store.upsert_reference(
            kind="template",
            external_key=source_key,
            payload=template_payload,
            ttl_days=7,
        )
        response_template_html, template_preview, template_preview_truncated = _agent_safe_html(latest_html)
        response_shell_html, shell_preview, shell_preview_truncated = _agent_safe_html(shell_html)
        return {
            "status": "template_extracted",
            "template_ref": template_ref,
            "source_message": {
                "message_ref": resolved.get("message_ref"),
                "item_id": template_payload["source_item_id"],
                "subject": source.get("subject"),
                "sent_at": source.get("sent_at"),
                "received_at": source.get("received_at"),
                "folder": source.get("folder"),
            },
            "template_html": response_template_html,
            "template_preview_html": template_preview,
            "template_html_chars": len(latest_html),
            "template_html_preview_truncated": template_preview_truncated,
            "template_shell_html": response_shell_html,
            "template_shell_preview_html": shell_preview,
            "template_shell_html_chars": len(shell_html),
            "template_shell_preview_truncated": shell_preview_truncated,
            "template_shell_strategy": shell_strategy,
            "template_use_instruction": (
                "Use template_ref with body_html containing only the new content in compose_email "
                "or reply_to_email; do not reconstruct a large template from the preview."
            ),
            "content_markers": {
                "start": TEMPLATE_CONTENT_START,
                "end": TEMPLATE_CONTENT_END,
            },
            "suggested_compose_inputs": {
                "to_queries": to,
                "cc_queries": cc,
                "subject": source.get("subject"),
            },
            "history_boundary_strategy": boundary_strategy,
            "quoted_history_excluded": history_removed,
            "source_body_truncated": source_truncated,
            "source_body_server_truncated": bool(source.get("body_server_truncated")),
            "source_body_local_truncated": bool(source.get("body_local_truncated")),
            "source_chars_used": len(source_html),
            "unique_body_available": bool(unique_body_html),
            "unique_body_type": source.get("unique_body_type"),
            "unique_body_truncated": unique_body_truncated,
            "unique_body_chars_used": len(unique_body_html),
            "inline_content_ids": inline_ids,
            "inline_images_available": sum(
                bool(item.get("is_inline")) for item in attachment_metadata
            ),
            "normal_attachments_available": sum(
                not bool(item.get("is_inline")) for item in attachment_metadata
            ),
            "warnings": warnings,
        }

    def find_email(
        self,
        *,
        folders: list[str] | None = None,
        sender_query: str | None = None,
        participant_query: str | None = None,
        subject_contains: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        normalized_folders = (
            normalize_mail_folders(folders) if folders else ["inbox", "sentitems"]
        )
        criteria: dict[str, Any] = {
            "subject_contains": subject_contains,
            "after": after,
            "before": before,
            "limit": limit,
            "offset": 0,
        }
        person_resolution: dict[str, Any] = {}
        for field, query in (("sender", sender_query), ("participant_contains", participant_query)):
            if not query:
                continue
            resolved = self._resolve_recipient_slot(query, lookback_days=lookback_days)
            person_resolution[field] = resolved
            if resolved.get("status") != "resolved":
                token = self.store.create_action_session(
                    {
                        "action": "find_email",
                        "payload": {
                            "folders": normalized_folders,
                            "sender_query": sender_query,
                            "participant_query": participant_query,
                            "subject_contains": subject_contains,
                            "after": after,
                            "before": before,
                            "limit": limit,
                            "lookback_days": lookback_days,
                        },
                        "person_resolution": person_resolution,
                    },
                    ttl_hours=24,
                )
                return {
                    "status": "needs_confirmation",
                    "resume_token": token,
                    "pending": [{"field": field, **resolved}],
                    "person_resolution": person_resolution,
                }
            criteria[field] = resolved["email"]

        page = self.client.search_emails_multi_folder(
            folders=normalized_folders,
            **criteria,
        )
        items: list[dict[str, Any]] = []
        for raw in page.get("items") or []:
            item = dict(raw)
            item["message_ref"] = self._message_ref(item)
            items.append(item)
        status = "resolved" if len(items) == 1 else "multiple_matches" if items else "not_found"
        return {
            **page,
            "status": status,
            "person_resolution": person_resolution,
            "items": items,
            "selected_message": items[0] if len(items) == 1 else None,
        }

    def _resolve_message_for_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        message_ref = payload.get("message_ref")
        if message_ref:
            stored = self.store.get_reference(str(message_ref), expected_kind="message")
            return {
                "status": "resolved",
                "message_ref": message_ref,
                "item_id": stored.payload["item_id"],
                "change_key": stored.payload.get("change_key"),
            }
        found = self.find_email(
            folders=payload.get("folders"),
            sender_query=payload.get("sender_query"),
            participant_query=payload.get("participant_query"),
            subject_contains=payload.get("subject_contains"),
            after=payload.get("after"),
            before=payload.get("before"),
            limit=int(payload.get("limit") or 20),
            lookback_days=int(payload.get("lookback_days") or DEFAULT_HISTORY_DAYS),
        )
        if found.get("status") == "resolved":
            item = found["selected_message"]
            return {
                "status": "resolved",
                "message_ref": item["message_ref"],
                "item_id": item["item_id"],
                "change_key": item.get("change_key"),
                "find_result": found,
            }
        return found


    def reply_to_email(
        self,
        *,
        body_html: str | None = None,
        reply_all: bool = False,
        template_ref: str | None = None,
        copy_template_inline_images: bool = True,
        copy_template_attachments: bool = False,
        **message_query: Any,
    ) -> dict[str, Any]:
        rendered_body_html, template_render_strategy = self._resolve_template_body_input(
            body_html=body_html,
            template_ref=template_ref,
        )
        payload = {
            **message_query,
            "body_html": rendered_body_html,
            "reply_all": reply_all,
            "template_ref": template_ref,
            "template_render_strategy": template_render_strategy,
            "copy_template_inline_images": copy_template_inline_images,
            "copy_template_attachments": copy_template_attachments,
        }
        resolved = self._resolve_message_for_action(payload)
        if resolved.get("status") != "resolved":
            if resolved.get("status") == "not_found":
                return {"status": "not_found", "message_resolution": resolved, "sent": False}
            token = self.store.create_action_session(
                {"action": "reply_to_email", "payload": payload, "message_resolution": resolved},
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "message_resolution": resolved,
                "sent": False,
            }

        prepared, template_warnings, template_payload = self._prepare_template_attachments(
            template_ref=template_ref,
            rendered_body_html=rendered_body_html,
            copy_inline_images=copy_template_inline_images,
            copy_attachments=copy_template_attachments,
        )
        if prepared and not callable(getattr(self.client, "add_attachment_content_to_draft", None)):
            raise ValueError("当前 EWS Client 不支持复制模板附件。")
        draft = self.client.reply_as_draft(
            item_id=str(resolved["item_id"]),
            change_key=resolved.get("change_key"),
            body_html=rendered_body_html,
            reply_all=reply_all,
        )
        current_item_id, current_change_key, copied = self._copy_prepared_attachments_to_draft(
            item_id=draft.item_id,
            change_key=draft.change_key,
            prepared=prepared,
            context="模板",
        )
        draft_data = self._draft_dict(draft)
        if copied:
            draft_data["item_id"] = current_item_id
            draft_data["change_key"] = current_change_key
            draft_data["draft_ref"] = self.store.upsert_reference(
                kind="draft",
                external_key=current_item_id,
                payload={
                    "item_id": current_item_id,
                    "change_key": current_change_key,
                    "subject": draft.subject,
                    "folder": "drafts",
                    "draft_type": "reply_all" if reply_all else "reply",
                },
                ttl_days=30,
            )
        inline_count = sum(bool(item.get("is_inline")) for item in copied)
        return {
            "status": "draft_created",
            "draft": draft_data,
            "source_message": resolved,
            "template_ref": template_ref,
            "template_render_strategy": template_render_strategy,
            "template_source": (
                {
                    "message_ref": template_payload.get("source_message_ref"),
                    "subject": template_payload.get("source_subject"),
                }
                if template_payload else None
            ),
            "template_inline_images_copied": inline_count,
            "template_attachments_copied": len(copied) - inline_count,
            "copied_template_attachments": copied,
            "template_attachment_warnings": template_warnings,
            "sent": False,
        }

    def forward_email(
        self,
        *,
        to_queries: list[str],
        body_html: str,
        cc_queries: list[str] | None = None,
        bcc_queries: list[str] | None = None,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
        **message_query: Any,
    ) -> dict[str, Any]:
        if not body_html.strip():
            raise ValueError("body_html 不能为空。")
        slots = self._resolve_slots(
            to_queries=to_queries,
            cc_queries=cc_queries or [],
            bcc_queries=bcc_queries or [],
            lookback_days=lookback_days,
        )
        payload = {
            **message_query,
            "to_queries": to_queries,
            "cc_queries": cc_queries or [],
            "bcc_queries": bcc_queries or [],
            "body_html": body_html,
            "lookback_days": lookback_days,
        }
        pending = self._pending_slots(slots)
        if pending:
            token = self.store.create_action_session(
                {"action": "forward_email", "payload": payload, "recipient_slots": slots},
                ttl_hours=24,
            )
            return {"status": "needs_confirmation", "resume_token": token, "pending": pending, "sent": False}
        resolved_message = self._resolve_message_for_action(payload)
        if resolved_message.get("status") != "resolved":
            if resolved_message.get("status") == "not_found":
                return {"status": "not_found", "message_resolution": resolved_message, "sent": False}
            token = self.store.create_action_session(
                {
                    "action": "forward_email",
                    "payload": payload,
                    "recipient_slots": slots,
                    "message_resolution": resolved_message,
                },
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "message_resolution": resolved_message,
                "sent": False,
            }
        draft = self.client.forward_as_draft(
            item_id=str(resolved_message["item_id"]),
            change_key=resolved_message.get("change_key"),
            to=self._slot_addresses(slots, "to"),
            cc=self._slot_addresses(slots, "cc"),
            bcc=self._slot_addresses(slots, "bcc"),
            body_html=body_html,
        )
        notices = [str(slot["user_notice"]) for values in slots.values() for slot in values if slot.get("user_notice")]
        return {
            "status": "draft_created",
            "draft": self._draft_dict(draft),
            "source_message": resolved_message,
            "recipient_resolution": slots,
            "default_rule_notices": notices,
            "sent": False,
        }

    def _apply_people_selections(
        self,
        slots: dict[str, list[dict[str, Any]]],
        selections: dict[str, str],
    ) -> dict[str, list[dict[str, Any]]]:
        for field, values in slots.items():
            for slot in values:
                if slot.get("status") == "resolved":
                    continue
                query = str(slot.get("query") or "")
                selected_value = selections.get(query) or selections.get(f"{field}:{query}")
                if not selected_value:
                    continue
                chosen = None
                for candidate in slot.get("candidates") or []:
                    if selected_value in {candidate.get("person_ref"), candidate.get("email")}:
                        chosen = candidate
                        break
                if chosen is None:
                    raise ValueError(f"选择 {selected_value!r} 不属于查询 {query!r} 的候选。")
                slot.update(
                    {
                        "status": "resolved",
                        "email": chosen.get("email"),
                        "person_ref": chosen.get("person_ref"),
                        "selected": chosen,
                        "default_rule_applied": "user_confirmation",
                        "user_notice": None,
                    }
                )
        return slots

    def continue_action(self, *, resume_token: str, selections: dict[str, str]) -> dict[str, Any]:
        session = self.store.get_action_session(resume_token)
        if session["status"] not in {"pending", "needs_confirmation"}:
            raise ValueError(f"任务状态为 {session['status']}，不能继续。")
        state = session["state"]
        action = state.get("action")
        payload = state.get("payload") or {}

        if action == "compose_email":
            slots = self._apply_people_selections(state["recipient_slots"], selections)
            pending = self._pending_slots(slots)
            if pending:
                self.store.update_action_session(resume_token, state={**state, "recipient_slots": slots})
                return {"status": "needs_confirmation", "resume_token": resume_token, "pending": pending}
            result = self._execute_compose(payload, slots)
        elif action == "find_email":
            # Re-run with the selected SMTP address replacing the ambiguous person query.
            pending = state.get("person_resolution") or {}
            sender_query = payload.get("sender_query")
            participant_query = payload.get("participant_query")
            for field, slot in pending.items():
                query = str(slot.get("query") or "")
                selected_value = selections.get(query) or selections.get(field)
                if not selected_value:
                    continue
                chosen = next(
                    (
                        candidate
                        for candidate in slot.get("candidates") or []
                        if selected_value in {candidate.get("person_ref"), candidate.get("email")}
                    ),
                    None,
                )
                if chosen is None:
                    raise ValueError(f"人员选择无效：{selected_value!r}")
                if field == "sender":
                    sender_query = chosen.get("email")
                else:
                    participant_query = chosen.get("email")
            result = self.find_email(
                folders=payload.get("folders"),
                sender_query=sender_query,
                participant_query=participant_query,
                subject_contains=payload.get("subject_contains"),
                after=payload.get("after"),
                before=payload.get("before"),
                limit=int(payload.get("limit") or 20),
                lookback_days=int(payload.get("lookback_days") or DEFAULT_HISTORY_DAYS),
            )
        elif action in {"reply_to_email", "forward_email", "extract_email_template"}:
            message_resolution = state.get("message_resolution") or {}

            # The source-mail search itself may be blocked by an ambiguous sender/participant.
            # Apply that user selection before asking for a message_ref.
            person_resolution = message_resolution.get("person_resolution") or {}
            for field, slot in person_resolution.items():
                if slot.get("status") == "resolved":
                    continue
                query = str(slot.get("query") or "")
                selected_value = selections.get(query) or selections.get(field)
                if not selected_value:
                    continue
                chosen = next(
                    (candidate for candidate in slot.get("candidates") or []
                     if selected_value in {candidate.get("person_ref"), candidate.get("email")}),
                    None,
                )
                if chosen is None:
                    raise ValueError(f"人员选择无效：{selected_value!r}")
                if field == "sender":
                    payload["sender_query"] = chosen.get("email")
                else:
                    payload["participant_query"] = chosen.get("email")

            message_ref = selections.get("message") or selections.get("message_ref")
            pending_messages = list(message_resolution.get("items") or [])
            if not message_ref and pending_messages:
                return {
                    "status": "needs_confirmation",
                    "resume_token": resume_token,
                    "pending_messages": pending_messages,
                }
            if message_ref and pending_messages:
                allowed_refs = {
                    str(item.get("message_ref"))
                    for item in pending_messages
                    if item.get("message_ref")
                }
                if str(message_ref) not in allowed_refs:
                    raise ValueError("所选 message_ref 不属于本次待确认的邮件候选。")
            if message_ref:
                payload["message_ref"] = message_ref
            if action == "reply_to_email":
                result = self.reply_to_email(**payload)
            elif action == "extract_email_template":
                result = self.extract_email_template(**payload)
            else:
                slots = self._apply_people_selections(state.get("recipient_slots") or {}, selections)
                pending = self._pending_slots(slots)
                if pending:
                    return {"status": "needs_confirmation", "resume_token": resume_token, "pending": pending}
                # Use selected recipient emails directly so the second pass is deterministic.
                result = self.forward_email(
                    to_queries=self._slot_addresses(slots, "to"),
                    cc_queries=self._slot_addresses(slots, "cc"),
                    bcc_queries=self._slot_addresses(slots, "bcc"),
                    body_html=payload["body_html"],
                    lookback_days=int(payload.get("lookback_days") or DEFAULT_HISTORY_DAYS),
                    message_ref=payload.get("message_ref"),
                    folders=payload.get("folders"),
                    sender_query=payload.get("sender_query"),
                    participant_query=payload.get("participant_query"),
                    subject_contains=payload.get("subject_contains"),
                    after=payload.get("after"),
                    before=payload.get("before"),
                    limit=int(payload.get("limit") or 20),
                )
        else:
            raise ValueError(f"不支持的恢复任务类型：{action!r}")

        self.store.update_action_session(resume_token, status="completed", state={**state, "result": result})
        return {**result, "resumed_from": resume_token}
