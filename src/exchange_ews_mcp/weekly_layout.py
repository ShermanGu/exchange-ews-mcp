# -*- coding: utf-8 -*-
"""Best-effort layout context for weekly-report text slots.

This module never rewrites HTML and never participates in the actual slot
replacement.  It only gives the Agent structural hints.  Every inferred field
is nullable; uncertain layout is represented as ``None``/empty candidates
instead of being guessed.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
_PROTECTED_TAGS = {"script", "style", "title", "pre", "textarea"}
_HEADING_TAGS = {f"h{level}" for level in range(1, 7)}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "footer", "header",
    "main", "nav", "p", "section", "li", "dt", "dd", "figcaption",
    "caption", "td", "th", *_HEADING_TAGS,
}
_ATTR_RE = re.compile(
    r"([A-Za-z_:][\w:.-]*)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?",
    re.DOTALL,
)
_CSS_HEADER_HINT_RE = re.compile(
    r"(?:font-weight\s*:\s*(?:bold|[6-9]00)|background(?:-color)?\s*:|text-align\s*:\s*center)",
    re.IGNORECASE,
)


@dataclass
class _Lexeme:
    kind: str
    raw: str
    start: int
    end: int
    name: str | None = None
    closing: bool = False
    self_closing: bool = False


@dataclass
class _Node:
    node_id: int
    name: str
    attrs: dict[str, str | None]
    open_start: int
    open_end: int
    parent: _Node | None
    children: list[_Node] = field(default_factory=list)
    close_start: int | None = None
    close_end: int | None = None

    @property
    def end(self) -> int:
        return self.close_end if self.close_end is not None else self.open_end

    def ancestors(self) -> Iterable[_Node]:
        current = self.parent
        while current is not None:
            yield current
            current = current.parent


@dataclass
class _Cell:
    node: _Node
    table: _Table
    physical_index: int
    row_start: int
    column_start: int
    row_span: int
    column_span: int
    text: str | None
    slot_ids: list[str]

    @property
    def row_end(self) -> int:
        return self.row_start + self.row_span - 1

    @property
    def column_end(self) -> int:
        return self.column_start + self.column_span - 1

    @property
    def is_explicit_header(self) -> bool:
        return self.node.name == "th" or (self.node.attrs.get("scope") or "").casefold() in {
            "row", "rowgroup", "col", "colgroup",
        }


@dataclass
class _Table:
    node: _Node
    table_id: str
    table_index: int
    nesting_depth: int
    parent_table_id: str | None
    rows: list[_Node]
    cells: list[_Cell] = field(default_factory=list)
    grid: dict[tuple[int, int], _Cell] = field(default_factory=dict)
    row_count: int = 0
    column_count: int = 0
    role: str = "unknown"
    role_confidence: float = 0.0


@dataclass(frozen=True)
class _SlotView:
    slot_id: str
    index: int
    text: str
    start: int
    end: int


def _consume_markup(value: str, start: int) -> tuple[int, str, str | None, bool, bool] | None:
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


def _lex(value: str) -> list[_Lexeme]:
    result: list[_Lexeme] = []
    cursor = 0
    while cursor < len(value):
        if value[cursor] == "<":
            consumed = _consume_markup(value, cursor)
            if consumed is not None:
                end, kind, name, closing, self_closing = consumed
                result.append(
                    _Lexeme(kind, value[cursor:end], cursor, end, name, closing, self_closing)
                )
                cursor = end
                continue
        next_markup = value.find("<", cursor + 1)
        end = len(value) if next_markup < 0 else next_markup
        result.append(_Lexeme("text", value[cursor:end], cursor, end))
        cursor = end
    return result


def _parse_attrs(raw_tag: str, tag_name: str) -> dict[str, str | None]:
    head = re.match(rf"<\s*{re.escape(tag_name)}\b", raw_tag, re.IGNORECASE)
    if head is None:
        return {}
    body = raw_tag[head.end():]
    if body.endswith(">"): body = body[:-1]
    if body.rstrip().endswith("/"): body = body.rstrip()[:-1]
    attrs: dict[str, str | None] = {}
    for match in _ATTR_RE.finditer(body):
        key = match.group(1).casefold()
        value = next((part for part in match.groups()[1:] if part is not None), None)
        attrs[key] = html.unescape(value) if value is not None else None
    return attrs


def _build_tree(value: str) -> tuple[_Node, list[_Node]]:
    root = _Node(0, "#document", {}, 0, 0, None, close_start=len(value), close_end=len(value))
    nodes = [root]
    stack = [root]
    for token in _lex(value):
        if token.kind != "tag" or token.name is None:
            continue
        if token.closing:
            # The production lexer validates strict nesting.  Layout analysis is
            # intentionally tolerant: find the nearest matching open element.
            found = None
            for index in range(len(stack) - 1, 0, -1):
                if stack[index].name == token.name:
                    found = index
                    break
            if found is None:
                continue
            node = stack[found]
            node.close_start = token.start
            node.close_end = token.end
            del stack[found:]
            continue
        node = _Node(
            node_id=len(nodes),
            name=token.name,
            attrs=_parse_attrs(token.raw, token.name),
            open_start=token.start,
            open_end=token.end,
            parent=stack[-1],
        )
        stack[-1].children.append(node)
        nodes.append(node)
        if token.self_closing:
            node.close_start = token.end
            node.close_end = token.end
        else:
            stack.append(node)
    for node in stack[1:]:
        node.close_start = len(value)
        node.close_end = len(value)
    return root, nodes


def _normalize_visible(raw: str) -> str:
    decoded = html.unescape(raw).replace("\xa0", " ")
    return re.sub(r"\s+", " ", decoded).strip()


def _node_slots(node: _Node, slots: Sequence[_SlotView]) -> list[_SlotView]:
    return [slot for slot in slots if node.open_end <= slot.start and slot.end <= (node.close_start or node.end)]


def _node_text(node: _Node, slots: Sequence[_SlotView]) -> str | None:
    parts = [slot.text for slot in _node_slots(node, slots) if slot.text.strip()]
    value = _normalize_visible(" ".join(parts))
    return value or None


def _nearest_ancestor(node: _Node | None, names: set[str]) -> _Node | None:
    current = node
    while current is not None:
        if current.name in names:
            return current
        current = current.parent
    return None


def _nearest_table(node: _Node | None) -> _Node | None:
    return _nearest_ancestor(node, {"table"})


def _containing_node(nodes: Sequence[_Node], slot: _SlotView) -> _Node:
    candidates = [
        node for node in nodes
        if node.name != "#document" and node.open_end <= slot.start and slot.end <= (node.close_start or node.end)
    ]
    if not candidates:
        return nodes[0]
    return max(candidates, key=lambda item: item.open_start)


def _positive_span(value: str | None) -> int:
    try:
        parsed = int(str(value or "1").strip())
    except ValueError:
        return 1
    return parsed if 1 <= parsed <= 1000 else 1


def _direct_table_rows(table_node: _Node, nodes: Sequence[_Node]) -> list[_Node]:
    rows = []
    for node in nodes:
        if node.name != "tr":
            continue
        if _nearest_table(node.parent) is table_node:
            rows.append(node)
    return sorted(rows, key=lambda item: item.open_start)


def _direct_row_cells(row_node: _Node, table_node: _Node, nodes: Sequence[_Node]) -> list[_Node]:
    cells = []
    for node in nodes:
        if node.name not in {"td", "th"}:
            continue
        nearest_row = _nearest_ancestor(node.parent, {"tr"})
        if nearest_row is row_node and _nearest_table(node.parent) is table_node:
            cells.append(node)
    return sorted(cells, key=lambda item: item.open_start)


def _classify_table(table: _Table) -> tuple[str, float]:
    if table.row_count == 0 or table.column_count == 0:
        return "unknown", 0.0
    explicit_headers = sum(1 for cell in table.cells if cell.is_explicit_header)
    nonempty = sum(1 for cell in table.cells if cell.text)
    if table.row_count >= 2 and table.column_count >= 2 and explicit_headers:
        return "data", 0.95
    if table.row_count >= 2 and table.column_count >= 2 and nonempty >= 4:
        return "data", 0.65
    if table.row_count == 1 or table.column_count == 1:
        return "layout", 0.55
    return "unknown", 0.35


def _build_tables(nodes: Sequence[_Node], slots: Sequence[_SlotView]) -> tuple[list[_Table], dict[int, _Cell]]:
    table_nodes = [node for node in nodes if node.name == "table"]
    tables: list[_Table] = []
    cell_by_node: dict[int, _Cell] = {}
    table_by_node: dict[int, _Table] = {}
    for index, node in enumerate(table_nodes):
        parent_table_node = _nearest_table(node.parent)
        parent_table = table_by_node.get(parent_table_node.node_id) if parent_table_node else None
        table = _Table(
            node=node,
            table_id=f"table_{index}",
            table_index=index,
            nesting_depth=sum(1 for ancestor in node.ancestors() if ancestor.name == "table"),
            parent_table_id=parent_table.table_id if parent_table else None,
            rows=_direct_table_rows(node, nodes),
        )
        table_by_node[node.node_id] = table
        tables.append(table)

        for row_index, row in enumerate(table.rows):
            column = 0
            for cell_node in _direct_row_cells(row, node, nodes):
                while (row_index, column) in table.grid:
                    column += 1
                row_span = _positive_span(cell_node.attrs.get("rowspan"))
                column_span = _positive_span(cell_node.attrs.get("colspan"))
                contained_slots = _node_slots(cell_node, slots)
                cell = _Cell(
                    node=cell_node,
                    table=table,
                    physical_index=len(table.cells),
                    row_start=row_index,
                    column_start=column,
                    row_span=row_span,
                    column_span=column_span,
                    text=_node_text(cell_node, slots),
                    slot_ids=[slot.slot_id for slot in contained_slots],
                )
                table.cells.append(cell)
                cell_by_node[cell_node.node_id] = cell
                for logical_row in range(row_index, row_index + row_span):
                    for logical_column in range(column, column + column_span):
                        # Invalid overlapping spans occur in real-world malformed
                        # HTML. Preserve the first physical cell instead of
                        # silently replacing it with a later one.
                        table.grid.setdefault((logical_row, logical_column), cell)
                column += column_span
        if table.grid:
            table.row_count = max(row for row, _ in table.grid) + 1
            table.column_count = max(column for _, column in table.grid) + 1
        table.role, table.role_confidence = _classify_table(table)
    return tables, cell_by_node


def _style_header_hint(cell: _Cell) -> bool:
    style = cell.node.attrs.get("style") or ""
    return bool(_CSS_HEADER_HINT_RE.search(style))


def _short_label(text: str | None) -> bool:
    return bool(text and len(text) <= 40 and len(text.split()) <= 8)


def _header_candidate(cell: _Cell, *, axis: str, target: _Cell) -> dict[str, Any] | None:
    if not cell.text or cell is target:
        return None
    scope = (cell.node.attrs.get("scope") or "").casefold()
    confidence = 0.0
    reasons: list[str] = []
    if cell.node.name == "th":
        confidence += 0.55
        reasons.append("th")
    if axis == "column" and scope in {"col", "colgroup"}:
        confidence += 0.35
        reasons.append("scope_col")
    if axis == "row" and scope in {"row", "rowgroup"}:
        confidence += 0.35
        reasons.append("scope_row")
    if _style_header_hint(cell):
        confidence += 0.15
        reasons.append("header_style")
    if _short_label(cell.text):
        confidence += 0.15
        reasons.append("short_label")
    if axis == "column" and cell.row_start == 0:
        confidence += 0.35
        reasons.append("top_row")
    if axis == "row" and cell.column_start == 0:
        confidence += 0.35
        reasons.append("left_column")
    # A nearby non-explicit label can still be useful, but keep it below the
    # high-confidence threshold so the caller can distinguish hint vs header.
    distance = (
        target.row_start - cell.row_end
        if axis == "column"
        else target.column_start - cell.column_end
    )
    if distance == 1:
        confidence += 0.10
        reasons.append("adjacent")
    confidence = min(confidence, 1.0)
    if confidence <= 0:
        return None
    return {
        "text": cell.text,
        "confidence": round(confidence, 2),
        "source": "+".join(reasons) if reasons else "positional_hint",
        "cell_id": _cell_id(cell),
        "row_index": cell.row_start,
        "column_index": cell.column_start,
        "row_span": cell.row_span,
        "column_span": cell.column_span,
    }


def _cell_id(cell: _Cell) -> str:
    return f"{cell.table.table_id}_r{cell.row_start}_c{cell.column_start}"


def _different_cell_in_direction(cell: _Cell, dr: int, dc: int) -> _Cell | None:
    row = cell.row_start if dr <= 0 else cell.row_end
    column = cell.column_start if dc <= 0 else cell.column_end
    limit = max(cell.table.row_count, cell.table.column_count) + 2
    for _ in range(limit):
        row += dr
        column += dc
        if row < 0 or column < 0 or row >= cell.table.row_count or column >= cell.table.column_count:
            return None
        candidate = cell.table.grid.get((row, column))
        if candidate is not None and candidate is not cell:
            return candidate
    return None


def _table_context(cell: _Cell, slot: _SlotView, nodes: Sequence[_Node], cell_by_node: dict[int, _Cell]) -> dict[str, Any]:
    column_candidates: list[dict[str, Any]] = []
    seen_column: set[int] = set()
    nearest_seen_for_column: set[int] = set()
    for row in range(cell.row_start - 1, -1, -1):
        for column in range(cell.column_start, cell.column_end + 1):
            candidate = cell.table.grid.get((row, column))
            if candidate is None or candidate.node.node_id in seen_column or candidate is cell:
                continue
            is_nearest = column not in nearest_seen_for_column
            nearest_seen_for_column.add(column)
            is_header_like = candidate.is_explicit_header or _style_header_hint(candidate)
            is_topmost = candidate.row_start == 0
            if not (is_nearest or is_header_like or is_topmost):
                continue
            seen_column.add(candidate.node.node_id)
            item = _header_candidate(candidate, axis="column", target=cell)
            if item is not None:
                column_candidates.append(item)

    row_candidates: list[dict[str, Any]] = []
    seen_row: set[int] = set()
    nearest_seen_for_row: set[int] = set()
    for column in range(cell.column_start - 1, -1, -1):
        for row in range(cell.row_start, cell.row_end + 1):
            candidate = cell.table.grid.get((row, column))
            if candidate is None or candidate.node.node_id in seen_row or candidate is cell:
                continue
            is_nearest = row not in nearest_seen_for_row
            nearest_seen_for_row.add(row)
            is_header_like = candidate.is_explicit_header or _style_header_hint(candidate)
            is_leftmost = candidate.column_start == 0
            if not (is_nearest or is_header_like or is_leftmost):
                continue
            seen_row.add(candidate.node.node_id)
            item = _header_candidate(candidate, axis="row", target=cell)
            if item is not None:
                row_candidates.append(item)

    column_candidates.sort(key=lambda item: (-item["confidence"], -item["row_index"], item["column_index"]))
    row_candidates.sort(key=lambda item: (-item["confidence"], -item["column_index"], item["row_index"]))
    accepted_column_items = sorted(
        (item for item in column_candidates if item["confidence"] >= 0.5),
        key=lambda item: (item["row_index"], item["column_index"]),
    )
    accepted_row_items = sorted(
        (item for item in row_candidates if item["confidence"] >= 0.5),
        key=lambda item: (item["column_index"], item["row_index"]),
    )
    accepted_columns = [item["text"] for item in accepted_column_items]
    accepted_rows = [item["text"] for item in accepted_row_items]
    # Preserve order while removing duplicate text from merged cells.
    accepted_columns = list(dict.fromkeys(accepted_columns))
    accepted_rows = list(dict.fromkeys(accepted_rows))

    above = _different_cell_in_direction(cell, -1, 0)
    below = _different_cell_in_direction(cell, 1, 0)
    left = _different_cell_in_direction(cell, 0, -1)
    right = _different_cell_in_direction(cell, 0, 1)

    outer_cell = None
    current = cell.node.parent
    while current is not None:
        if current.name in {"td", "th"}:
            candidate = cell_by_node.get(current.node_id)
            if candidate is not None and candidate.table is not cell.table:
                outer_cell = candidate
                break
        current = current.parent

    slot_position = cell.slot_ids.index(slot.slot_id) if slot.slot_id in cell.slot_ids else None
    max_confidence = max(
        [item["confidence"] for item in column_candidates + row_candidates],
        default=0.0,
    )
    return {
        "table_id": cell.table.table_id,
        "parent_table_id": cell.table.parent_table_id,
        "table_index": cell.table.table_index,
        "nesting_depth": cell.table.nesting_depth,
        "table_role": cell.table.role,
        "table_role_confidence": cell.table.role_confidence,
        "row_count": cell.table.row_count,
        "column_count": cell.table.column_count,
        "cell_id": _cell_id(cell),
        "row_index": cell.row_start,
        "column_index": cell.column_start,
        "logical_row_start": cell.row_start,
        "logical_row_end": cell.row_end,
        "logical_column_start": cell.column_start,
        "logical_column_end": cell.column_end,
        "row_span": cell.row_span,
        "column_span": cell.column_span,
        "cell_tag": cell.node.name,
        "cell_role": "header" if cell.is_explicit_header else "data",
        "is_header_cell": cell.is_explicit_header,
        "cell_text": cell.text,
        "cell_slot_count": len(cell.slot_ids),
        "slot_index_in_cell": slot_position,
        "row_headers": accepted_rows,
        "column_headers": accepted_columns,
        "nearest_row_header": accepted_rows[-1] if accepted_rows else None,
        "nearest_column_header": accepted_columns[-1] if accepted_columns else None,
        "row_header_candidates": row_candidates,
        "column_header_candidates": column_candidates,
        "header_confidence": round(max_confidence, 2),
        "above_cell_text": above.text if above else None,
        "below_cell_text": below.text if below else None,
        "left_cell_text": left.text if left else None,
        "right_cell_text": right.text if right else None,
        "outer_cell_text": outer_cell.text if outer_cell else None,
    }


def _section_path(slot: _SlotView, nodes: Sequence[_Node], slots: Sequence[_SlotView]) -> list[str]:
    path: list[str] = []
    for node in sorted((node for node in nodes if node.name in _HEADING_TAGS and node.open_start < slot.start), key=lambda item: item.open_start):
        text = _node_text(node, slots)
        if not text:
            continue
        level = int(node.name[1])
        path = path[: level - 1]
        while len(path) < level - 1:
            path.append("")
        path.append(text)
    return [item for item in path if item]


def _nearest_block(node: _Node) -> _Node | None:
    return _nearest_ancestor(node, _BLOCK_TAGS)


def _block_nodes(nodes: Sequence[_Node], slots: Sequence[_SlotView]) -> list[_Node]:
    result: list[_Node] = []
    for node in nodes:
        if node.name not in _BLOCK_TAGS or not _node_text(node, slots):
            continue
        # Prefer leaf-ish blocks. A wrapper div containing paragraphs is not a
        # useful peer context and would duplicate all descendant text.
        if node.name == "div" and any(child.name in _BLOCK_TAGS for child in node.children):
            continue
        result.append(node)
    return sorted(result, key=lambda item: item.open_start)


def _neighbor_block_text(current: _Node | None, blocks: Sequence[_Node], slots: Sequence[_SlotView], direction: int) -> str | None:
    if current is None:
        return None
    try:
        index = blocks.index(current)
    except ValueError:
        return None
    cursor = index + direction
    while 0 <= cursor < len(blocks):
        candidate = blocks[cursor]
        # Ignore ancestor/descendant duplicates around the same slot.
        if not (candidate.open_start <= current.open_start and current.end <= candidate.end) and not (
            current.open_start <= candidate.open_start and candidate.end <= current.end
        ):
            text = _node_text(candidate, slots)
            if text:
                return text
        cursor += direction
    return None


def _list_context(node: _Node, slots: Sequence[_SlotView]) -> dict[str, Any] | None:
    li = _nearest_ancestor(node, {"li"})
    if li is None:
        return None
    list_node = _nearest_ancestor(li.parent, {"ul", "ol"})
    list_type = "ordered" if list_node and list_node.name == "ol" else "unordered" if list_node else None
    depth = sum(1 for ancestor in li.ancestors() if ancestor.name in {"ul", "ol"})
    siblings = [child for child in (list_node.children if list_node else []) if child.name == "li"]
    item_index = siblings.index(li) if li in siblings else None
    parent_li = None
    current = li.parent
    while current is not None:
        if current.name == "li":
            parent_li = current
            break
        current = current.parent
    previous_text = _node_text(siblings[item_index - 1], slots) if item_index is not None and item_index > 0 else None
    next_text = _node_text(siblings[item_index + 1], slots) if item_index is not None and item_index + 1 < len(siblings) else None
    return {
        "list_type": list_type,
        "list_depth": depth,
        "item_index": item_index,
        "item_text": _node_text(li, slots),
        "parent_item_text": _node_text(parent_li, slots) if parent_li else None,
        "previous_item_text": previous_text,
        "next_item_text": next_text,
    }


def _paragraph_context(node: _Node, blocks: Sequence[_Node], slots: Sequence[_SlotView]) -> dict[str, Any] | None:
    paragraph = _nearest_ancestor(node, {"p"}) or _nearest_block(node)
    if paragraph is None or paragraph.name in {"td", "th", "li"}:
        return None
    paragraph_blocks = [block for block in blocks if block.name not in {"td", "th", "li"}]
    try:
        index = paragraph_blocks.index(paragraph)
    except ValueError:
        index = None
    return {
        "paragraph_index": index,
        "paragraph_tag": paragraph.name,
        "paragraph_text": _node_text(paragraph, slots),
        "previous_paragraph_text": _neighbor_block_text(paragraph, paragraph_blocks, slots, -1),
        "next_paragraph_text": _neighbor_block_text(paragraph, paragraph_blocks, slots, 1),
    }


def _link_context(node: _Node) -> dict[str, Any] | None:
    anchor = _nearest_ancestor(node, {"a"})
    if anchor is None:
        return None
    return {
        "href": anchor.attrs.get("href"),
        "target": anchor.attrs.get("target"),
        "link_target_editable": False,
    }


def _container_type(node: _Node, cell: _Cell | None) -> str:
    if cell is not None:
        return "table_cell"
    if _nearest_ancestor(node, {"li"}) is not None:
        return "list_item"
    if _nearest_ancestor(node, _HEADING_TAGS) is not None:
        return "heading"
    if _nearest_ancestor(node, {"blockquote"}) is not None:
        return "blockquote"
    if _nearest_ancestor(node, {"a"}) is not None:
        return "link_text"
    if _nearest_ancestor(node, {"p"}) is not None:
        return "paragraph"
    if _nearest_block(node) is not None:
        return "block_text"
    return "inline_text"


def _semantic_location(
    *,
    section_path: list[str],
    table_context: dict[str, Any] | None,
    list_context: dict[str, Any] | None,
    paragraph_context: dict[str, Any] | None,
) -> str | None:
    parts: list[str] = []
    if section_path:
        parts.append("章节：" + " / ".join(section_path))
    if table_context:
        row_header = table_context.get("nearest_row_header")
        column_headers = list(table_context.get("column_headers") or [])
        if row_header:
            parts.append("行：" + str(row_header))
        if column_headers:
            parts.append("列：" + " / ".join(str(item) for item in column_headers))
        elif table_context.get("nearest_column_header"):
            parts.append("列：" + str(table_context["nearest_column_header"]))
        if not row_header and table_context.get("left_cell_text"):
            parts.append("左邻：" + str(table_context["left_cell_text"]))
        if not column_headers and table_context.get("above_cell_text"):
            parts.append("上邻：" + str(table_context["above_cell_text"]))
        if table_context.get("is_header_cell"):
            parts.append("单元格角色：表头")
    elif list_context:
        parts.append(
            "列表项：" + str((list_context.get("item_index") or 0) + 1)
        )
    elif paragraph_context and paragraph_context.get("paragraph_tag"):
        parts.append("文本块：" + str(paragraph_context["paragraph_tag"]))
    return "；".join(parts) or None


def build_layout_contexts(template_html: str, raw_slots: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Return a nullable, best-effort layout context keyed by ``slot_id``.

    ``raw_slots`` only needs ``slot_id``, ``index``, ``text``, ``start`` and
    ``end`` attributes.  Layout analysis is advisory: failures should be caught
    by the caller and represented as null context rather than affecting the
    deterministic HTML replacement path.
    """
    slots = [
        _SlotView(
            slot_id=str(item.slot_id),
            index=int(item.index),
            text=str(item.text),
            start=int(item.start),
            end=int(item.end),
        )
        for item in raw_slots
    ]
    _root, nodes = _build_tree(template_html)
    _tables, cell_by_node = _build_tables(nodes, slots)
    blocks = _block_nodes(nodes, slots)
    result: dict[str, dict[str, Any]] = {}

    for slot in slots:
        node = _containing_node(nodes, slot)
        cell_node = _nearest_ancestor(node, {"td", "th"})
        cell = cell_by_node.get(cell_node.node_id) if cell_node else None
        section_path = _section_path(slot, nodes, slots)
        current_block = _nearest_block(node)
        table_context = _table_context(cell, slot, nodes, cell_by_node) if cell else None
        list_context = _list_context(node, slots)
        paragraph_context = _paragraph_context(node, blocks, slots)
        link_context = _link_context(node)
        confidence_values = []
        if table_context:
            confidence_values.append(float(table_context.get("header_confidence") or 0.0))
        if section_path:
            confidence_values.append(0.9)
        if list_context:
            confidence_values.append(0.85)
        if paragraph_context:
            confidence_values.append(0.75)
        context_confidence = max(confidence_values, default=0.35)

        semantic_location = _semantic_location(
            section_path=section_path,
            table_context=table_context,
            list_context=list_context,
            paragraph_context=paragraph_context,
        )
        result[slot.slot_id] = {
            "container_type": _container_type(node, cell),
            "semantic_location": semantic_location,
            "document_context": {
                "section_path": section_path,
                "nearest_heading": section_path[-1] if section_path else None,
                "previous_block_text": None if cell else _neighbor_block_text(current_block, blocks, slots, -1),
                "next_block_text": None if cell else _neighbor_block_text(current_block, blocks, slots, 1),
            },
            "table_context": table_context,
            "list_context": list_context,
            "paragraph_context": paragraph_context,
            "link_context": link_context,
            "plain_text_context": None,
            "context_confidence": round(context_confidence, 2),
        }
    return result
