# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from .weekly_layout import build_layout_contexts
from .weekly_separator_whitelist import (
    MAX_WEEKLY_REPORT_SEPARATOR_LENGTH,
    WEEKLY_REPORT_SEPARATOR_NAME_BY_HTML,
)


WEEKLY_WORD_SECTION_SCAN_LIMIT = 500_000
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)
_CID_RE = re.compile(r"\bcid:([^\s\"'<>]+)", re.IGNORECASE)


_WORD_SECTION_OPEN_RE = re.compile(
    r"<div\b(?=[^>]*\bclass\s*=\s*(?:[\"'][^\"']*\bWordSection1\b[^\"']*[\"']|WordSection1\b))[^>]*>",
    re.IGNORECASE,
)

# Separator recognition is deliberately exact.  Candidate ``<p>...</p>``
# blocks are compared unchanged against the standalone whitelist, and only a
# direct child of ``div.WordSection1`` may terminate the copied report body.

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
_TAG_RE = re.compile(
    r"<!--.*?-->|<![^>]*>|<\?[^>]*\?>|<\s*(/?)\s*([A-Za-z][\w:.-]*)\b[^>]*>",
    re.DOTALL,
)



@dataclass(frozen=True)
class ExtractedWeeklyBody:
    html: str
    strategy: str
    boundary_offset: int | None
    appended_closing_tags: tuple[str, ...] = ()
    word_section_offset: int | None = None
    word_section_content_start: int | None = None
    scanned_characters: int | None = None
    scan_limit: int | None = None
    separator_variant: str | None = None
    separator_language: str | None = None


@dataclass(frozen=True)
class WordSectionParagraphBlock:
    start: int
    end: int
    raw_html: str
    depth: int
    whitelist_name: str | None

    @property
    def is_direct_child(self) -> bool:
        return self.depth == 0

    @property
    def accepted_separator(self) -> bool:
        return self.is_direct_child and self.whitelist_name is not None


def _body_inner(value: str) -> str:
    """Return body-inner HTML by slicing the original string without reserialization."""
    opening = _BODY_OPEN_RE.search(value)
    if opening is None:
        return value
    closing_matches = list(_BODY_CLOSE_RE.finditer(value, opening.end()))
    end = closing_matches[-1].start() if closing_matches else len(value)
    return value[opening.end() : end]


def _matching_div_close(value: str, opening: re.Match[str]) -> tuple[int, int] | None:
    """Find the closing div paired with ``opening`` without DOM reserialization."""
    depth = 1
    for match in _TAG_RE.finditer(value, opening.end()):
        name = (match.group(2) or "").casefold()
        if name != "div":
            continue
        raw = match.group(0)
        closing = bool(match.group(1))
        if closing:
            depth -= 1
            if depth == 0:
                return match.start(), match.end()
        elif not raw.rstrip().endswith("/>"):
            depth += 1
    return None


def _visible_text_for_validation(value: str) -> str:
    """Return normalized visible text only for non-empty validation.

    The EWS response is parsed from bytes according to its XML encoding. HTML
    entities are decoded here so Chinese text such as ``等线`` remains valid
    whether it arrived literally or as numeric/named entities.
    """
    without_comments = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    without_tags = re.sub(r"<[^>]+>", "", without_comments)
    decoded = html.unescape(without_tags).replace("\xa0", " ")
    normalized = unicodedata.normalize("NFKC", decoded)
    normalized = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _iter_word_section_paragraph_blocks(
    value: str,
    *,
    content_start: int,
    section_end: int,
    scan_limit: int | None = None,
) -> Iterator[WordSectionParagraphBlock]:
    """Yield complete ``p`` blocks with their nesting depth inside WordSection1.

    ``depth == 0`` means the paragraph is a direct child of WordSection1.  A
    paragraph inside a table, cell, nested div, or any other container has a
    greater depth and can never be accepted as a weekly-report separator.

    Raw candidate HTML is sliced directly from ``value``.  It is not decoded,
    normalized, reformatted, or reserialized before whitelist comparison.
    """
    if content_start < 0 or section_end < content_start:
        raise ValueError("WordSection1 扫描范围无效。")
    if scan_limit is not None and scan_limit <= 0:
        raise ValueError("scan_limit 必须大于 0。")

    scan_end = section_end
    if scan_limit is not None:
        scan_end = min(
            section_end,
            content_start + scan_limit + MAX_WEEKLY_REPORT_SEPARATOR_LENGTH,
        )

    # Stack entries are (tag_name, opening_offset, depth_before_opening).
    stack: list[tuple[str, int, int]] = []
    for match in _TAG_RE.finditer(value, content_start, scan_end):
        raw = match.group(0)
        if raw.startswith(("<!--", "<!", "<?")):
            continue
        closing = bool(match.group(1))
        name = (match.group(2) or "").casefold()
        if not name:
            continue

        if not closing:
            if name in _VOID_TAGS or raw.rstrip().endswith("/>"):
                continue
            stack.append((name, match.start(), len(stack)))
            continue

        matching_index = None
        for index in range(len(stack) - 1, -1, -1):
            if stack[index][0] == name:
                matching_index = index
                break
        if matching_index is None:
            continue

        opened_name, opened_at, depth = stack[matching_index]
        if opened_name == "p":
            candidate = value[opened_at : match.end()]
            yield WordSectionParagraphBlock(
                start=opened_at,
                end=match.end(),
                raw_html=candidate,
                depth=depth,
                whitelist_name=WEEKLY_REPORT_SEPARATOR_NAME_BY_HTML.get(candidate),
            )
        del stack[matching_index:]


def _extract_from_word_section(
    value: str,
    *,
    scan_limit: int = WEEKLY_WORD_SECTION_SCAN_LIMIT,
) -> ExtractedWeeklyBody | None:
    """Copy WordSection1 inner HTML up to the first exact top-level separator.

    Production rules:

    1. Find the first ``div.WordSection1``.
    2. Start immediately after that opening tag.
    3. Scan at most ``scan_limit`` characters for a complete ``p`` block whose
       raw HTML exactly equals one standalone whitelist entry.
    4. Accept it only when it is a direct child of WordSection1.  Identical
       blocks inside tables, table cells, nested divs, or other containers are
       ignored.
    5. Copy the untouched raw HTML before the accepted block to Reply All.

    There is no semantic fallback, font-family inference, entity decoding,
    whitespace normalization, or DOM reserialization in separator matching.
    """
    if scan_limit <= 0:
        raise ValueError("scan_limit 必须大于 0。")

    section = _WORD_SECTION_OPEN_RE.search(value)
    if section is None:
        return None

    closing = _matching_div_close(value, section)
    section_end = closing[0] if closing else len(value)
    content_start = section.end()

    accepted: WordSectionParagraphBlock | None = None
    for block in _iter_word_section_paragraph_blocks(
        value,
        content_start=content_start,
        section_end=section_end,
        scan_limit=scan_limit,
    ):
        distance = block.start - content_start
        if distance > scan_limit:
            break
        if block.accepted_separator:
            accepted = block
            break

    if accepted is None:
        available = max(0, section_end - content_start)
        if available > scan_limit:
            raise ValueError(
                "已找到 WordSection1，但在找到顶层白名单周报分割线之前已超过 "
                f"{scan_limit} 字符阈值，本次不会创建草稿。"
            )
        raise ValueError(
            "已找到 WordSection1，但其直属子块中没有找到与周报分割线白名单完全一致的 "
            "HTML 块，本次不会创建草稿。"
        )

    boundary = accepted.start
    report_body = value[content_start:boundary]
    if not _visible_text_for_validation(report_body):
        raise ValueError("WordSection1 与第一个顶层白名单周报分割线之间没有可复制的正文。")

    closed_candidate, closers = _close_open_html_fragment(report_body)
    return ExtractedWeeklyBody(
        html=closed_candidate,
        strategy="word_section1_to_first_top_level_whitelist_separator",
        boundary_offset=boundary,
        appended_closing_tags=closers,
        word_section_offset=section.start(),
        word_section_content_start=content_start,
        scanned_characters=boundary - content_start,
        scan_limit=scan_limit,
        separator_variant=accepted.whitelist_name,
        separator_language="en-us",
    )


def _close_open_html_fragment(value: str) -> tuple[str, tuple[str, ...]]:
    """Append only missing end tags after a raw string slice.

    The original fragment is never parsed into a DOM or reserialized.  This
    keeps Outlook table attributes, conditional HTML, VML and whitespace intact.
    """
    stack: list[str] = []
    for match in _TAG_RE.finditer(value):
        raw = match.group(0)
        if raw.startswith(("<!--", "<!", "<?")):
            continue
        closing = bool(match.group(1))
        name = (match.group(2) or "").casefold()
        if not name:
            continue
        if closing:
            try:
                index = len(stack) - 1 - stack[::-1].index(name)
            except ValueError:
                continue
            del stack[index:]
            continue
        if name in _VOID_TAGS or raw.rstrip().endswith("/>"):
            continue
        stack.append(name)
    if not stack:
        return value, ()
    closers = tuple(reversed(stack))
    return value + "".join(f"</{name}>" for name in closers), closers


def extract_latest_weekly_body(
    *,
    full_body_html: str,
    unique_body_html: str | None,
    unique_body_type: str | None,
    conversation_has_older_items: bool,
) -> ExtractedWeeklyBody:
    """Extract the newest weekly report using only the confirmed Word layout.

    ``unique_body_html``, ``unique_body_type`` and
    ``conversation_has_older_items`` are retained in the public helper signature
    for compatibility, but the weekly extractor deliberately ignores them. The
    only accepted production layout is:

    ``WordSection1 opening tag -> newest report HTML -> first direct-child exact-whitelist separator``.
    """
    del unique_body_html, unique_body_type, conversation_has_older_items

    full_inner = _body_inner(full_body_html)
    result = _extract_from_word_section(full_inner)
    if result is None:
        raise ValueError(
            "最新周报中未找到 WordSection1，本次不会创建周报草稿。"
        )
    return result


def _scan_text_spans(fragment: str) -> Iterable[tuple[int, int, str]]:
    """Yield raw text spans outside tags/comments/script/style without rewriting HTML."""
    length = len(fragment)
    cursor = 0
    hidden_depth = 0
    while cursor < length:
        if fragment.startswith("<!--", cursor):
            end = fragment.find("-->", cursor + 4)
            cursor = length if end < 0 else end + 3
            continue
        if fragment[cursor] == "<":
            quote: str | None = None
            end = cursor + 1
            while end < length:
                char = fragment[end]
                if quote:
                    if char == quote:
                        quote = None
                elif char in {"'", '"'}:
                    quote = char
                elif char == ">":
                    break
                end += 1
            tag_end = min(end + 1, length)
            tag = fragment[cursor:tag_end]
            lowered = tag.casefold()
            if re.match(r"<\s*(script|style)\b", lowered):
                hidden_depth += 1
            elif re.match(r"<\s*/\s*(script|style)\b", lowered):
                hidden_depth = max(0, hidden_depth - 1)
            cursor = tag_end
            continue
        end = fragment.find("<", cursor)
        if end < 0:
            end = length
        if hidden_depth == 0 and end > cursor:
            yield cursor, end, fragment[cursor:end]
        cursor = end


def referenced_content_ids(fragment: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in _CID_RE.finditer(fragment):
        value = html.unescape(match.group(1)).strip().strip("<>")
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def visible_text(fragment: str, *, limit: int | None = 100_000) -> str:
    parts = [html.unescape(raw).replace("\xa0", " ") for _, _, raw in _scan_text_spans(fragment)]
    value = re.sub(r"[\t\r\f\v]+", " ", "".join(parts))
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r" {2,}", " ", value).strip()
    return value if limit is None else value[:limit]


@dataclass(frozen=True)
class WeeklyReportSection:
    """One report body split from the latest Outlook reply thread."""

    index: int
    html: str
    text: str
    start: int
    end: int
    appended_closing_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TextOnlyHtmlValidation:
    """Result of validating an Agent-produced HTML fragment."""

    tag_count: int
    text_slot_count: int
    changed_text_slots: int
    unchanged_text_slots: int
    structure_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag_count": self.tag_count,
            "text_slot_count": self.text_slot_count,
            "changed_text_slots": self.changed_text_slots,
            "unchanged_text_slots": self.unchanged_text_slots,
            "structure_sha256": self.structure_sha256,
        }


@dataclass(frozen=True)
class EditableTextSlot:
    """One non-empty, unprotected text node in the latest report template."""

    slot_id: str
    index: int
    text: str
    raw_text: str
    start: int
    end: int
    html_path: tuple[str, ...]
    leading_padding: str = ""
    trailing_padding: str = ""

    def as_agent_dict(
        self,
        *,
        previous_text: str | None = None,
        next_text: str | None = None,
        layout_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "index": self.index,
            "text": self.text,
            "html_path": "/".join(self.html_path),
            "previous_text": previous_text,
            "next_text": next_text,
            "layout_context": layout_context or _empty_layout_context(),
        }


@dataclass(frozen=True)
class TextSlotApplyResult:
    """Result of applying deterministic text-slot replacements."""

    html: str
    requested_changes: int
    applied_changes: int
    unchanged_changes: int
    slot_count: int
    changed_slot_ids: tuple[str, ...]
    html_validation: TextOnlyHtmlValidation

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_changes": self.requested_changes,
            "applied_changes": self.applied_changes,
            "unchanged_changes": self.unchanged_changes,
            "slot_count": self.slot_count,
            "changed_slot_ids": list(self.changed_slot_ids),
            "html_validation": self.html_validation.as_dict(),
        }


@dataclass(frozen=True)
class _HtmlLexeme:
    kind: str
    raw: str
    start: int
    end: int
    name: str | None = None
    closing: bool = False
    self_closing: bool = False
    protected_text: bool = False


def split_weekly_report_sections(
    full_body_html: str,
    *,
    max_reports: int = 5,
    scan_limit: int = 2_500_000,
) -> list[WeeklyReportSection]:
    """Split the first WordSection1 into latest-to-oldest report fragments.

    A boundary is accepted only when a direct-child ``p`` block is an exact
    member of the separator whitelist. One or two consecutive separators are
    naturally supported: empty/whitespace-only chunks between them are skipped.
    The original HTML is sliced; it is never DOM-reserialized.
    """
    if not 1 <= max_reports <= 20:
        raise ValueError("max_reports 必须在 1 到 20 之间。")
    if scan_limit <= 0:
        raise ValueError("scan_limit 必须大于 0。")
    if not full_body_html.strip():
        raise ValueError("最新周报 Body 为空。")

    section = _WORD_SECTION_OPEN_RE.search(full_body_html)
    if section is None:
        raise ValueError("最新周报中未找到 WordSection1，无法提取历史周报。")
    closing = _matching_div_close(full_body_html, section)
    section_end = closing[0] if closing else len(full_body_html)
    content_start = section.end()
    hard_end = min(section_end, content_start + scan_limit)

    separators: list[WordSectionParagraphBlock] = []
    for block in _iter_word_section_paragraph_blocks(
        full_body_html,
        content_start=content_start,
        section_end=section_end,
        scan_limit=scan_limit,
    ):
        if block.start > hard_end:
            break
        if block.accepted_separator:
            separators.append(block)

    if not separators:
        available = max(0, section_end - content_start)
        if available > scan_limit:
            raise ValueError(
                "已找到 WordSection1，但在找到顶层白名单周报分割线之前已超过 "
                f"{scan_limit} 字符阈值。"
            )
        raise ValueError(
            "已找到 WordSection1，但其直属子块中没有找到与周报分割线白名单完全一致的 HTML 块。"
        )

    sections: list[WeeklyReportSection] = []
    cursor = content_start

    def add_chunk(start: int, end: int) -> None:
        if len(sections) >= max_reports or end < start:
            return
        raw = full_body_html[start:end]
        if not _visible_text_for_validation(raw):
            return
        closed, closers = _close_open_html_fragment(raw)
        sections.append(
            WeeklyReportSection(
                index=len(sections) + 1,
                html=closed,
                text=visible_text(closed, limit=None),
                start=start,
                end=end,
                appended_closing_tags=closers,
            )
        )

    separator_index = 0
    while separator_index < len(separators) and len(sections) < max_reports:
        separator = separators[separator_index]
        add_chunk(cursor, separator.start)
        boundary_count = 1
        cursor = separator.end
        separator_index += 1
        while separator_index < len(separators):
            next_separator = separators[separator_index]
            between = full_body_html[cursor:next_separator.start]
            if _visible_text_for_validation(between):
                break
            boundary_count += 1
            if boundary_count > 2:
                raise ValueError(
                    "同一个周报边界连续出现超过 2 个白名单分割块；"
                    "无法确认历史结构，拒绝继续。"
                )
            cursor = next_separator.end
            separator_index += 1

    if len(sections) < max_reports and cursor < section_end:
        if hard_end < section_end:
            raise ValueError(
                "在完整提取所需历史周报之前已超过上下文扫描字符阈值；"
                "不会返回被截断的周报文本。"
            )
        add_chunk(cursor, section_end)

    if not sections:
        raise ValueError("WordSection1 中没有提取到任何非空周报正文。")
    return sections


def _consume_markup(value: str, start: int) -> tuple[int, str, str | None, bool, bool] | None:
    """Consume one HTML markup token with quote-aware ``>`` handling."""
    if not value.startswith("<", start):
        return None
    if value.startswith("<!--", start):
        end = value.find("-->", start + 4)
        if end < 0:
            raise ValueError("HTML 注释未闭合。")
        return end + 3, "comment", None, False, False
    if value.startswith("<!", start) or value.startswith("<?", start):
        quote: str | None = None
        cursor = start + 2
        while cursor < len(value):
            char = value[cursor]
            if quote:
                if char == quote:
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == ">":
                return cursor + 1, "declaration", None, False, False
            cursor += 1
        raise ValueError("HTML 声明未闭合。")

    head = re.match(r"<\s*(/?)\s*([A-Za-z][\w:.-]*)\b", value[start:])
    if head is None:
        return None
    closing = bool(head.group(1))
    name = head.group(2).casefold()
    quote = None
    cursor = start + head.end()
    while cursor < len(value):
        char = value[cursor]
        if quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == ">":
            raw = value[start : cursor + 1]
            self_closing = (not closing) and (
                name in _VOID_TAGS or raw.rstrip().endswith("/>")
            )
            return cursor + 1, "tag", name, closing, self_closing
        cursor += 1
    raise ValueError(f"HTML 标签未闭合：{name}")


def _lex_html(value: str) -> list[_HtmlLexeme]:
    result: list[_HtmlLexeme] = []
    cursor = 0
    stack: list[str] = []
    protected = {"script", "style", "title", "pre", "textarea"}
    while cursor < len(value):
        if value[cursor] == "<":
            consumed = _consume_markup(value, cursor)
            if consumed is not None:
                end, kind, name, closing, self_closing = consumed
                raw = value[cursor:end]
                result.append(
                    _HtmlLexeme(
                        kind=kind,
                        raw=raw,
                        start=cursor,
                        end=end,
                        name=name,
                        closing=closing,
                        self_closing=self_closing,
                    )
                )
                if kind == "tag" and name:
                    if closing:
                        if not stack or stack[-1] != name:
                            expected = stack[-1] if stack else "无"
                            raise ValueError(
                                f"HTML 标签闭合顺序异常：遇到 </{name}>，当前期望 </{expected}>。"
                            )
                        stack.pop()
                    elif not self_closing:
                        stack.append(name)
                cursor = end
                continue
        next_markup = value.find("<", cursor + 1)
        end = len(value) if next_markup < 0 else next_markup
        result.append(
            _HtmlLexeme(
                kind="text",
                raw=value[cursor:end],
                start=cursor,
                end=end,
                protected_text=any(name in protected for name in stack),
            )
        )
        cursor = end
    if stack:
        raise ValueError("HTML 结构不完整，未闭合标签：" + " → ".join(stack))
    return result


def _structure_tokens(value: str) -> tuple[list[str], list[_HtmlLexeme]]:
    lexemes = _lex_html(value)
    structural = [item.raw for item in lexemes if item.kind != "text"]
    return structural, lexemes




def _text_slots_from_lexemes(value: str, lexemes: list[_HtmlLexeme]) -> list[_HtmlLexeme]:
    """Return all N+1 text slots between structural tokens, including empty slots."""
    structural = [item for item in lexemes if item.kind != "text"]
    slots: list[_HtmlLexeme] = []
    stack: list[str] = []
    protected = {"script", "style", "title", "pre", "textarea"}
    cursor = 0
    for token in structural:
        slots.append(
            _HtmlLexeme(
                kind="text",
                raw=value[cursor:token.start],
                start=cursor,
                end=token.start,
                protected_text=any(name in protected for name in stack),
            )
        )
        if token.kind == "tag" and token.name:
            if token.closing:
                if stack and stack[-1] == token.name:
                    stack.pop()
            elif not token.self_closing:
                stack.append(token.name)
        cursor = token.end
    slots.append(
        _HtmlLexeme(
            kind="text",
            raw=value[cursor:],
            start=cursor,
            end=len(value),
            protected_text=any(name in protected for name in stack),
        )
    )
    return slots

def html_structure_sha256(value: str) -> str:
    structural, _ = _structure_tokens(value)
    payload = "\0".join(structural).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_text_only_html_update(
    template_html: str,
    candidate_html: str,
) -> TextOnlyHtmlValidation:
    """Require a generated candidate to preserve every HTML token byte-for-byte.

    Only text slots between the unchanged markup tokens may differ. Tags,
    attributes, comments, declarations and their order must be exactly equal.
    Script/style/title text is protected and must also remain unchanged.
    """
    if not candidate_html.strip():
        raise ValueError("candidate_html 不能为空。")
    if "```" in candidate_html:
        raise ValueError("candidate_html 不能包含 Markdown 代码围栏。")

    template_structural, template_lexemes = _structure_tokens(template_html)
    candidate_structural, candidate_lexemes = _structure_tokens(candidate_html)
    if len(template_structural) != len(candidate_structural):
        raise ValueError(
            "HTML 完整性校验失败：标签/注释数量发生变化；Agent 只能修改文本。"
        )
    for index, (expected, actual) in enumerate(
        zip(template_structural, candidate_structural, strict=True)
    ):
        if expected != actual:
            raise ValueError(
                "HTML 完整性校验失败：第 "
                f"{index + 1} 个标签或注释发生变化。期望 {expected!r}，实际 {actual!r}。"
            )

    template_text = _text_slots_from_lexemes(template_html, template_lexemes)
    candidate_text = _text_slots_from_lexemes(candidate_html, candidate_lexemes)
    if len(template_text) != len(candidate_text):
        raise ValueError("HTML 完整性校验失败：文本槽数量发生变化。")

    changed = 0
    for index, (expected, actual) in enumerate(zip(template_text, candidate_text, strict=True)):
        # Prevent wrappers such as ```html ... ``` or arbitrary text outside the
        # template root from being smuggled in as legal text-only changes.
        if index in {0, len(template_text) - 1} and not expected.raw.strip():
            if actual.raw.strip():
                raise ValueError("HTML 完整性校验失败：HTML 片段外部不能增加文字。")
        expected_visible = html.unescape(expected.raw).replace("\xa0", " ")
        if not expected_visible.strip() and expected.raw != actual.raw:
            raise ValueError("HTML 完整性校验失败：标签之间的空白文本不允许改写或重排。")
        if expected.protected_text and expected.raw != actual.raw:
            raise ValueError("HTML 完整性校验失败：script/style/title/pre/textarea 内容不允许修改。")
        if expected.raw != actual.raw:
            changed += 1

    return TextOnlyHtmlValidation(
        tag_count=len(template_structural),
        text_slot_count=len(template_text),
        changed_text_slots=changed,
        unchanged_text_slots=len(template_text) - changed,
        structure_sha256=hashlib.sha256(
            "\0".join(template_structural).encode("utf-8")
        ).hexdigest(),
    )


_RAW_PADDING_TOKEN_RE = re.compile(
    r"(?:\s|&nbsp;|&#0*160;|&#x0*a0;)",
    re.IGNORECASE,
)


def _split_raw_text_padding(raw: str) -> tuple[str, str, str]:
    """Split HTML whitespace/entity padding from a visible text node.

    The returned middle part is the only part that may be replaced. Keeping
    the original leading/trailing bytes avoids reformatting Outlook's HTML
    indentation and non-breaking-space padding.
    """
    left = 0
    while left < len(raw):
        match = _RAW_PADDING_TOKEN_RE.match(raw, left)
        if match is None:
            break
        left = match.end()

    right = len(raw)
    while right > left:
        candidate = None
        # The token set is small; checking a bounded suffix keeps the logic
        # deterministic without decoding or reserializing the HTML fragment.
        for start in range(max(left, right - 12), right):
            match = _RAW_PADDING_TOKEN_RE.fullmatch(raw[start:right])
            if match is not None:
                candidate = start
                break
        if candidate is None:
            break
        right = candidate
    return raw[:left], raw[left:right], raw[right:]


def _slot_display_text(raw_core: str) -> str:
    return html.unescape(raw_core).replace("\xa0", " ")


def extract_editable_text_slots(template_html: str) -> list[EditableTextSlot]:
    """Return stable editable text slots without exposing HTML to the Agent.

    A slot is one non-empty text node outside script/style/title/pre/textarea.
    Tags, attributes, comments and whitespace-only nodes never become slots.
    """
    lexemes = _lex_html(template_html)
    stack: list[str] = []
    slots: list[EditableTextSlot] = []

    for lexeme in lexemes:
        if lexeme.kind == "tag" and lexeme.name:
            if lexeme.closing:
                if stack and stack[-1] == lexeme.name:
                    stack.pop()
            elif not lexeme.self_closing:
                stack.append(lexeme.name)
            continue
        if lexeme.kind != "text" or lexeme.protected_text:
            continue

        leading, core, trailing = _split_raw_text_padding(lexeme.raw)
        display = _slot_display_text(core)
        if not display.strip():
            continue

        index = len(slots) + 1
        start = lexeme.start + len(leading)
        end = lexeme.end - len(trailing)
        digest_source = "\0".join(
            [str(index), str(start), str(end), "/".join(stack), core]
        )
        slot_id = f"slot_{index:04d}_{hashlib.sha256(digest_source.encode('utf-8')).hexdigest()[:12]}"
        slots.append(
            EditableTextSlot(
                slot_id=slot_id,
                index=index,
                text=display,
                raw_text=core,
                start=start,
                end=end,
                html_path=tuple(stack),
                leading_padding=leading,
                trailing_padding=trailing,
            )
        )
    return slots


def _empty_layout_context(*, status: str = "unavailable") -> dict[str, Any]:
    return {
        "container_type": "unknown",
        "semantic_location": None,
        "document_context": {
            "section_path": [],
            "nearest_heading": None,
            "previous_block_text": None,
            "next_block_text": None,
        },
        "table_context": None,
        "list_context": None,
        "paragraph_context": None,
        "link_context": None,
        "plain_text_context": None,
        "context_confidence": 0.0,
        "analysis_status": status,
    }


def editable_text_slots_for_agent(
    slots: list[EditableTextSlot],
    *,
    template_html: str | None = None,
) -> list[dict[str, Any]]:
    layout_by_slot: dict[str, dict[str, Any]] = {}
    layout_status = "not_requested"
    if template_html is not None:
        try:
            layout_by_slot = build_layout_contexts(template_html, slots)
            layout_status = "analyzed"
        except Exception:  # Layout hints must never break deterministic editing.
            layout_by_slot = {}
            layout_status = "unavailable"

    result: list[dict[str, Any]] = []
    for index, slot in enumerate(slots):
        previous_text = slots[index - 1].text if index > 0 else None
        next_text = slots[index + 1].text if index + 1 < len(slots) else None
        layout_context = dict(
            layout_by_slot.get(slot.slot_id)
            or _empty_layout_context(status=layout_status)
        )
        layout_context.setdefault("analysis_status", layout_status)
        result.append(
            slot.as_agent_dict(
                previous_text=previous_text,
                next_text=next_text,
                layout_context=layout_context,
            )
        )
    return result


_COMPACT_SLOT_LOCATION_MAX_CHARS = 640


def _compact_context_text(value: Any, *, max_chars: int = _COMPACT_SLOT_LOCATION_MAX_CHARS) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _append_location_part(parts: list[str], label: str, value: Any, *, max_chars: int = 120) -> None:
    text = _compact_context_text(value, max_chars=max_chars)
    if text:
        parts.append(f"{label}：{text}")


def _unique_context_texts(values: Any, *, max_chars: int = 100) -> list[str]:
    result: list[str] = []
    for raw in list(values or []):
        text = _compact_context_text(raw, max_chars=max_chars)
        if text and text not in result:
            result.append(text)
    return result


def _candidate_header_texts(
    candidates: Any,
    *,
    accepted: list[str],
    axis: str,
) -> list[str]:
    """Return ordered, de-duplicated weak header hints not already accepted.

    Outlook frequently serializes visual table headers as ordinary ``td``
    elements.  Those cells may stay below the strong-header confidence
    threshold even though the layout analyser has still found them as nearby
    header candidates.  Production ``location`` strings therefore expose the
    candidate text with a clear ``候选`` marker instead of silently discarding
    it or upgrading it to a confirmed header.
    """
    raw_candidates = [item for item in list(candidates or []) if isinstance(item, dict)]
    confirmed_rows = [
        int(item["row_index"])
        for item in raw_candidates
        if _compact_context_text(item.get("text"), max_chars=100) in accepted
        and isinstance(item.get("row_index"), int)
    ]
    normalized: list[tuple[int, int, str]] = []
    for item in raw_candidates:
        text = _compact_context_text(item.get("text"), max_chars=100)
        if not text or text in accepted:
            continue
        row = item.get("row_index")
        column = item.get("column_index")
        source = str(item.get("source") or "")
        strong_source = any(
            marker in source
            for marker in ("th", "scope_col", "scope_row", "header_style", "top_row", "left_column")
        )
        if axis == "row" and not strong_source:
            # A plain cell immediately to the left is usually another data
            # column, not a second-level row header. It remains available as
            # ``left_cell_text`` instead of being mislabeled as a header.
            continue
        if axis == "column" and not strong_source:
            # Preserve an Outlook-style second header row serialized as plain
            # td only when it directly extends the confirmed top header band.
            if not (
                "adjacent" in source
                and isinstance(row, int)
                and confirmed_rows
                and row == max(confirmed_rows) + 1
            ):
                continue
        normalized.append(
            (
                int(row) if isinstance(row, int) else 10**9,
                int(column) if isinstance(column, int) else 10**9,
                text,
            )
        )
    if axis == "row":
        normalized.sort(key=lambda item: (item[1], item[0]))
    else:
        normalized.sort(key=lambda item: (item[0], item[1]))
    result: list[str] = []
    for _row, _column, text in normalized:
        if text not in result:
            result.append(text)
    return result


def _compact_slot_location(item: dict[str, Any]) -> str | None:
    """Collapse internal layout analysis into one rich model-facing string.

    Only one nullable string crosses the MCP boundary, but it preserves as much
    reliable positional information as practical: section path, logical table
    coordinates, confirmed and candidate multi-level headers, neighbouring
    cells, nested-table context, and non-table block/list neighbours.  Weak
    header evidence is explicitly marked as ``候选`` so the Agent can use it
    without mistaking inference for certainty.
    """
    layout = item.get("layout_context")
    if not isinstance(layout, dict):
        return None

    parts: list[str] = []
    document = layout.get("document_context")
    section_path: list[str] = []
    if isinstance(document, dict):
        section_path = _unique_context_texts(document.get("section_path"), max_chars=100)
        if section_path:
            parts.append("章节：" + " / ".join(section_path))
        else:
            _append_location_part(parts, "最近标题", document.get("nearest_heading"), max_chars=120)

    used_values = set(section_path)

    def append_unique(label: str, value: Any, *, max_chars: int = 120) -> None:
        text = _compact_context_text(value, max_chars=max_chars)
        if text and text not in used_values:
            parts.append(f"{label}：{text}")
            used_values.add(text)

    table = layout.get("table_context")
    if isinstance(table, dict):
        row_index = table.get("row_index")
        column_index = table.get("column_index")
        if isinstance(row_index, int) and isinstance(column_index, int):
            parts.append(f"表格位置：第{row_index + 1}行第{column_index + 1}列")

        nesting_depth = table.get("nesting_depth")
        if isinstance(nesting_depth, int) and nesting_depth > 0:
            parts.append(f"嵌套表格：第{nesting_depth + 1}层")

        row_headers = _unique_context_texts(table.get("row_headers"), max_chars=100)
        column_headers = _unique_context_texts(table.get("column_headers"), max_chars=100)
        row_candidates = _candidate_header_texts(
            table.get("row_header_candidates"), accepted=row_headers, axis="row"
        )
        column_candidates = _candidate_header_texts(
            table.get("column_header_candidates"), accepted=column_headers, axis="column"
        )

        if row_headers:
            parts.append("行表头：" + " / ".join(row_headers))
        elif nearest_row := _compact_context_text(table.get("nearest_row_header"), max_chars=100):
            parts.append("行表头：" + nearest_row)
        if row_candidates:
            parts.append("行表头候选：" + " / ".join(row_candidates))

        if column_headers:
            parts.append("列表头：" + " / ".join(column_headers))
        elif nearest_column := _compact_context_text(table.get("nearest_column_header"), max_chars=100):
            parts.append("列表头：" + nearest_column)
        if column_candidates:
            parts.append("列表头候选：" + " / ".join(column_candidates))

        # Neighbour cells remain useful even when a visual Outlook header was
        # represented by a plain td and therefore only classified as a weak
        # candidate.  Avoid exact duplicates already present in header paths.
        known_headers = set(row_headers + column_headers + row_candidates + column_candidates)
        for key, label in (
            ("above_cell_text", "上邻"),
            ("below_cell_text", "下邻"),
            ("left_cell_text", "左邻"),
            ("right_cell_text", "右邻"),
        ):
            value = _compact_context_text(table.get(key), max_chars=100)
            if value and value not in known_headers:
                parts.append(f"{label}：{value}")

        _append_location_part(parts, "外层单元格", table.get("outer_cell_text"), max_chars=140)
        if table.get("is_header_cell"):
            parts.append("单元格角色：表头")
        else:
            role = _compact_context_text(table.get("cell_role"), max_chars=30)
            if role and role != "data":
                parts.append("单元格角色：" + role)

        slot_count = table.get("cell_slot_count")
        slot_index = table.get("slot_index_in_cell")
        if isinstance(slot_count, int) and slot_count > 1 and isinstance(slot_index, int):
            parts.append(f"同格文本节点：第{slot_index + 1}/{slot_count}个")

    else:
        container_type = _compact_context_text(layout.get("container_type"), max_chars=40)
        container_labels = {
            "paragraph": "段落",
            "heading": "标题",
            "list_item": "列表项",
            "blockquote": "引用块",
            "link_text": "链接文字",
            "block_text": "文本块",
            "inline_text": "行内文字",
        }
        if container_type and container_type != "unknown":
            parts.append("内容类型：" + container_labels.get(container_type, container_type))

        list_context = layout.get("list_context")
        if isinstance(list_context, dict):
            append_unique("父列表项", list_context.get("parent_item_text"), max_chars=120)
            index = list_context.get("item_index")
            depth = list_context.get("list_depth")
            if isinstance(index, int):
                value = f"第{index + 1}项"
                if isinstance(depth, int) and depth > 0:
                    value += f"，第{depth}层"
                parts.append("列表位置：" + value)
            append_unique("上一列表项", list_context.get("previous_item_text"), max_chars=120)
            append_unique("下一列表项", list_context.get("next_item_text"), max_chars=120)

        paragraph = layout.get("paragraph_context")
        if isinstance(paragraph, dict):
            append_unique("文本块标签", paragraph.get("paragraph_tag"), max_chars=20)
            append_unique("上一文本块", paragraph.get("previous_paragraph_text"), max_chars=120)
            append_unique("下一文本块", paragraph.get("next_paragraph_text"), max_chars=120)

        if isinstance(document, dict):
            append_unique("上一相邻块", document.get("previous_block_text"), max_chars=120)
            append_unique("下一相邻块", document.get("next_block_text"), max_chars=120)

    # A bare logical coordinate or an unavailable-layout placeholder does not
    # explain the slot's meaning. Return null rather than spending tokens on
    # context that cannot help the Agent choose a destination.
    if len(parts) == 1 and parts[0].startswith("表格位置："):
        return None
    return _compact_context_text("；".join(parts))


def compact_editable_text_slots_for_agent(
    slots: list[EditableTextSlot],
    *,
    template_html: str | None = None,
) -> list[dict[str, Any]]:
    """Return the compact production view of editable text slots.

    Full layout analysis is still performed internally, but only ``slot_id``,
    current ``text`` and one nullable ``location`` summary cross the MCP tool
    boundary.  This prevents large tables from exhausting the Agent context.
    """
    layout_by_slot: dict[str, dict[str, Any]] = {}
    layout_status = "not_requested"
    if template_html is not None:
        try:
            layout_by_slot = build_layout_contexts(template_html, slots)
            layout_status = "analyzed"
        except Exception:  # Advisory layout hints must never block editing.
            layout_by_slot = {}
            layout_status = "unavailable"

    compact: list[dict[str, Any]] = []
    for slot in slots:
        layout_context = dict(
            layout_by_slot.get(slot.slot_id)
            or _empty_layout_context(status=layout_status)
        )
        layout_context.setdefault("analysis_status", layout_status)
        compact.append(
            {
                "slot_id": slot.slot_id,
                "text": slot.text,
                "location": _compact_slot_location(
                    {"layout_context": layout_context}
                ),
            }
        )
    return compact


def editable_slot_manifest_sha256(slots: list[EditableTextSlot]) -> str:
    payload = "\0".join(
        "\x1f".join(
            [
                slot.slot_id,
                str(slot.start),
                str(slot.end),
                "/".join(slot.html_path),
                slot.raw_text,
            ]
        )
        for slot in slots
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_editable_text_slot_changes(
    template_html: str,
    changes: list[dict[str, Any]] | None,
) -> TextSlotApplyResult:
    """Apply Agent-selected plain-text replacements to the stored template.

    ``new_text`` is always HTML-escaped. The Agent never supplies markup and
    never controls offsets, tags or attributes.
    """
    if changes is None:
        changes = []
    if not isinstance(changes, list):
        raise ValueError("changes 必须是数组。")
    if len(changes) > 200:
        raise ValueError("changes 最多允许 200 项。")

    slots = extract_editable_text_slots(template_html)
    by_id = {slot.slot_id: slot for slot in slots}
    seen: set[str] = set()
    replacements: list[tuple[int, int, str, str]] = []
    unchanged = 0

    for index, raw_change in enumerate(changes):
        if not isinstance(raw_change, dict):
            raise ValueError(f"changes[{index}] 必须是对象。")
        unknown_fields = set(raw_change) - {"slot_id", "new_text"}
        if unknown_fields:
            raise ValueError(
                f"changes[{index}] 包含不支持的字段：{', '.join(sorted(unknown_fields))}。"
            )
        if not isinstance(raw_change.get("slot_id"), str):
            raise ValueError(f"changes[{index}].slot_id 必须是字符串。")
        slot_id = raw_change["slot_id"].strip()
        if not slot_id:
            raise ValueError(f"changes[{index}].slot_id 不能为空。")
        if slot_id in seen:
            raise ValueError(f"changes 中重复提交了 slot_id：{slot_id}。")
        seen.add(slot_id)
        slot = by_id.get(slot_id)
        if slot is None:
            raise ValueError(f"未知或已过期的 slot_id：{slot_id}。")

        if "new_text" not in raw_change:
            raise ValueError(f"changes[{index}].new_text 缺失。")
        if not isinstance(raw_change.get("new_text"), str):
            raise ValueError(f"changes[{index}].new_text 必须是字符串。")
        new_text = raw_change["new_text"]
        if "\x00" in new_text:
            raise ValueError(f"changes[{index}].new_text 不能包含 NUL 字符。")
        if len(new_text) > 100_000:
            raise ValueError(f"changes[{index}].new_text 超过 100000 字符。")


        if new_text == slot.text:
            unchanged += 1
            continue
        escaped = html.escape(new_text, quote=False)
        replacements.append((slot.start, slot.end, escaped, slot_id))

    updated = template_html
    for start, end, escaped, _slot_id in sorted(replacements, reverse=True):
        updated = updated[:start] + escaped + updated[end:]

    validation = validate_text_only_html_update(template_html, updated)
    return TextSlotApplyResult(
        html=updated,
        requested_changes=len(changes),
        applied_changes=len(replacements),
        unchanged_changes=unchanged,
        slot_count=len(slots),
        changed_slot_ids=tuple(item[3] for item in replacements),
        html_validation=validation,
    )


def build_weekly_report_agent_prompt(
    *,
    user_input: str,
    reports: list[WeeklyReportSection],
    editable_slots: list[dict[str, Any]],
    reference_materials: list[dict[str, str]] | None = None,
    embed_slots: bool = True,
) -> str:
    """Build deterministic instructions for polished slot-based editing."""
    materials = reference_materials or []
    material_parts: list[str] = []
    for index, item in enumerate(materials, start=1):
        name = str(item.get("name") or f"材料 {index}").strip()
        content = str(item.get("content") or "")
        material_parts.append(f"[参考材料 {index}: {name}]\n{content}")
    report_parts = [
        f"[历史周报 {item.index}，1 为最新]\n{item.text}" for item in reports
    ]
    slot_payload = json.dumps(editable_slots, ensure_ascii=False, separators=(",", ":"))
    slot_section = (
        f"[可编辑文本槽位]\n{slot_payload}"
        if embed_slots
        else "[可编辑文本槽位]\n请直接使用本次工具结果中的 editable_slots 字段；不要从其他会话或旧工具结果复制 slot_id。"
    )
    return "\n\n".join(
        [
            "【任务目标】\n你正在生成下一周周报。必须完整比较所有历史周报，理解每个项目连续几周的变化，并结合用户本次输入和参考材料决定哪些槽位需要更新。",
            "【强制工具顺序】\n当前上下文来自 get_weekly_report_context。完成分析后，只能使用本次返回的 weekly_flow_token 调用一次 update_weekly_report。不得跳过第一步、不得自行构造 token、不得复用旧 token。",
            "【信息优先级】\n用户本次明确输入 > 本次参考材料 > 最新一周周报 > 更早历史周报。历史周报用于识别项目正式名称、术语、上下文和连续变化，不得用过期状态覆盖用户本次事实。",
            "【日期硬校验】\n在生成 changes 前，必须逐一检查本次 editable_slots 中所有包含日期、日期范围、星期、周次或月份的文本槽位，不得只检查标题。凡属于周报周期、周报标题、表头、本周计划或下周计划的继承日期，都必须更新为新一周对应日期，即使用户没有额外提醒‘改日期’也不能遗漏。若历史周报呈固定七天周期，默认保持原格式并将最新周期整体顺延七天。项目事实中的固定发生日期、里程碑日期或用户明确要求保留的日期不得机械平移。若无法判断某个日期是周期日期还是事实日期，或无法确定正确的新日期，不得猜测、不得调用 update_weekly_report，应先向用户确认。调用 update_weekly_report 前必须再次复核全部日期槽位，确保所有应更新日期都已包含在 changes 中。",
            "【周报化改写要求】\n用户输入只是口语化事实记录，不是可以直接粘贴的最终周报。你必须先理解事实，再将其改写为简洁、正式、客观、适合工作周报的书面表达。不得机械复制用户的口语；若用户输入本身已经是规范周报表述，可以保留其准确措辞。",
            "周报化改写应遵守：\n1. 删除“这周、那个、差不多、没怎么、已经吧”等无必要口语和语气词；\n2. 优先使用清晰的动宾结构，例如“完成接口联调”“开展性能测试”“等待测试环境就绪”；\n3. 延续历史周报中的正式项目名称、技术术语、语气和详略程度；\n4. 根据槽位上下文决定是否需要项目名，避免在同一行重复项目名称；\n5. 合并重复信息，new_text 必须是该槽位最终应显示的完整文本，而不是修改说明或零散补丁；\n6. 不得添加用户和材料未提供的事实，不得把“阶段完成”夸大为“项目完成”，不得虚构时间、指标、结果或负责人；\n7. 用户说“没有变化”“保持不变”时，不要把这句话写入周报，也不要提交该槽位；\n8. 用户明确要求删除时，将对应槽位 new_text 设置为空字符串。",
            "【改写示例】\n用户输入：“A这周联调完了。” → 合适的进展槽位文本：“完成接口联调。”\n用户输入：“B还在等环境。” → 合适的进展槽位文本：“等待测试环境就绪。”\n用户输入：“项目C没什么变化。” → 不提交项目C槽位，不要写“暂无变化”。\n用户输入：“邮件那个项目现在可以reply all了。” → 结合历史正式名称改写为类似“完成 Reply All 功能开发。”，但不得照搬示例中的项目名称或事实。",
            "【槽位与版面规则】\n你不会接触或生成 HTML。只能从【可编辑文本槽位】中选择需要变化的 slot_id，并为它提供完整的新纯文本。生产槽位只包含 slot_id、text 和可空的 location：location 是 Server 根据复杂表格逻辑网格、rowspan/colspan、多级表头、嵌套表、标题、段落和列表等完整内部分析压缩得到的位置摘要。location 为 null 表示无法可靠判断，不得自行补造。表格槽位必须同时结合 location 中的行信息与列信息，尤其不得把“下周计划”写入“本周进展”；location 含“表头”时，除非用户明确要求修改表头或日期，否则不要提交。未被用户输入或参考材料明确影响的槽位不要提交，默认原样继承最新一周。不得虚构 slot_id，不得在 new_text 中输出 HTML 标签。",
            "【调用格式】\n调用 update_weekly_report 时，changes 的每一项只能包含 slot_id 和 new_text。slot_id 必须直接取自本次【可编辑文本槽位】；new_text 是周报化改写后的完整纯文本。不要重复提交旧文本，不要返回完整 HTML，不要在工具调用之外输出修改说明。",
            f"[用户本周输入]\n{user_input}",
            "\n\n".join(material_parts) if material_parts else "[参考材料]\n无",
            "\n\n".join(report_parts),
            slot_section,
        ]
    )

