from __future__ import annotations

import json

from exchange_ews_mcp import weekly_report
from exchange_ews_mcp.weekly_report import (
    compact_editable_text_slots_for_agent,
    editable_text_slots_for_agent,
    extract_editable_text_slots,
)


def _slots(html: str) -> dict[str, dict]:
    raw = extract_editable_text_slots(html)
    return {
        item["text"]: item
        for item in editable_text_slots_for_agent(raw, template_html=html)
    }


def test_simple_table_returns_row_column_and_neighbors() -> None:
    html = (
        "<table><tr><th>项目</th><th>本周进展</th><th>下周计划</th></tr>"
        "<tr><td>项目A</td><td><span>完成联调</span></td><td>性能测试</td></tr></table>"
    )
    item = _slots(html)["完成联调"]
    context = item["layout_context"]
    table = context["table_context"]
    assert context["container_type"] == "table_cell"
    assert context["semantic_location"] == "行：项目A；列：本周进展"
    assert table["row_index"] == 1
    assert table["column_index"] == 1
    assert table["nearest_row_header"] == "项目A"
    assert table["nearest_column_header"] == "本周进展"
    assert table["above_cell_text"] == "本周进展"
    assert table["left_cell_text"] == "项目A"
    assert table["right_cell_text"] == "性能测试"
    assert table["is_header_cell"] is False


def test_multilevel_headers_and_spans_expand_to_logical_grid() -> None:
    html = (
        '<table><thead><tr><th rowspan="2">项目</th><th colspan="2">工作内容</th></tr>'
        "<tr><th>本周进展</th><th>下周计划</th></tr></thead>"
        "<tbody><tr><td>项目A</td><td>完成联调</td><td>性能测试</td></tr></tbody></table>"
    )
    slots = _slots(html)
    progress = slots["完成联调"]["layout_context"]["table_context"]
    plan = slots["性能测试"]["layout_context"]["table_context"]
    assert progress["column_headers"] == ["工作内容", "本周进展"]
    assert plan["column_headers"] == ["工作内容", "下周计划"]
    assert progress["nearest_row_header"] == "项目A"
    project_header = slots["项目"]["layout_context"]["table_context"]
    assert project_header["row_span"] == 2
    assert project_header["logical_row_end"] == 1
    merged_header = slots["工作内容"]["layout_context"]["table_context"]
    assert merged_header["column_span"] == 2
    assert merged_header["logical_column_end"] == 2


def test_vertical_key_value_table_uses_left_row_label() -> None:
    html = (
        "<table><tr><td>字段</td><td>内容</td></tr>"
        "<tr><td>项目名称</td><td>项目A</td></tr>"
        "<tr><td>本周进展</td><td>完成联调</td></tr></table>"
    )
    table = _slots(html)["完成联调"]["layout_context"]["table_context"]
    assert table["nearest_row_header"] == "本周进展"
    assert table["nearest_column_header"] == "内容"
    assert table["left_cell_text"] == "本周进展"
    assert table["above_cell_text"] == "项目A"


def test_no_reliable_header_returns_null_and_keeps_neighbors() -> None:
    html = "<table><tr><td>很长的说明文字，不应被强制认定成表头，因为它只是布局内容</td><td>当前内容</td></tr></table>"
    table = _slots(html)["当前内容"]["layout_context"]["table_context"]
    assert table["table_role"] == "layout"
    assert table["nearest_column_header"] is None
    assert table["column_headers"] == []
    assert table["left_cell_text"].startswith("很长的说明文字")


def test_nested_tables_have_independent_ids_and_outer_cell_context() -> None:
    html = (
        "<table><tr><td>项目A</td><td><table>"
        "<tr><th>阶段</th><th>进展</th></tr><tr><td>测试</td><td>完成</td></tr>"
        "</table></td></tr></table>"
    )
    table = _slots(html)["完成"]["layout_context"]["table_context"]
    assert table["table_id"] == "table_1"
    assert table["parent_table_id"] == "table_0"
    assert table["nesting_depth"] == 1
    assert table["nearest_row_header"] == "测试"
    assert table["nearest_column_header"] == "进展"
    assert "阶段" in table["outer_cell_text"]


def test_multiple_slots_in_same_cell_share_cell_context() -> None:
    html = "<table><tr><th>进展</th></tr><tr><td><span>完成开发</span><span>并完成测试</span></td></tr></table>"
    slots = _slots(html)
    first = slots["完成开发"]["layout_context"]["table_context"]
    second = slots["并完成测试"]["layout_context"]["table_context"]
    assert first["cell_id"] == second["cell_id"]
    assert first["cell_text"] == "完成开发 并完成测试"
    assert first["cell_slot_count"] == 2
    assert first["slot_index_in_cell"] == 0
    assert second["slot_index_in_cell"] == 1


def test_empty_image_cell_still_preserves_logical_column_coordinates() -> None:
    html = (
        "<table><tr><th>项目</th><th>图标</th><th>进展</th></tr>"
        '<tr><td>项目A</td><td><img src="cid:x"></td><td>完成联调</td></tr></table>'
    )
    table = _slots(html)["完成联调"]["layout_context"]["table_context"]
    assert table["column_index"] == 2
    assert table["nearest_column_header"] == "进展"
    assert table["left_cell_text"] is None


def test_non_table_html_has_heading_paragraph_and_list_context() -> None:
    html = (
        "<h2>项目A</h2><p>本周完成接口联调。</p>"
        "<ul><li>修复问题一</li><li>准备性能测试</li></ul>"
    )
    slots = _slots(html)
    paragraph = slots["本周完成接口联调。"]["layout_context"]
    assert paragraph["container_type"] == "paragraph"
    assert paragraph["table_context"] is None
    assert paragraph["document_context"]["section_path"] == ["项目A"]
    assert paragraph["paragraph_context"]["paragraph_text"] == "本周完成接口联调。"
    list_item = slots["准备性能测试"]["layout_context"]
    assert list_item["container_type"] == "list_item"
    assert list_item["list_context"]["list_type"] == "unordered"
    assert list_item["list_context"]["item_index"] == 1
    assert list_item["list_context"]["previous_item_text"] == "修复问题一"


def test_mixed_html_and_link_context_are_supported_without_editing_attributes() -> None:
    html = (
        "<h1>周报</h1><div><p><b>项目A：</b>完成联调，详见"
        '<a href="https://example.test/doc" target="_blank">项目文档</a>。</p></div>'
    )
    slots = _slots(html)
    link = slots["项目文档"]["layout_context"]
    assert link["container_type"] == "link_text"
    assert link["link_context"] == {
        "href": "https://example.test/doc",
        "target": "_blank",
        "link_target_editable": False,
    }
    assert link["document_context"]["nearest_heading"] == "周报"


def test_layout_analysis_failure_degrades_to_null_context(monkeypatch) -> None:
    def fail(_html, _slots):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(weekly_report, "build_layout_contexts", fail)
    html = "<p>完成联调</p>"
    raw = extract_editable_text_slots(html)
    item = editable_text_slots_for_agent(raw, template_html=html)[0]
    context = item["layout_context"]
    assert context["analysis_status"] == "unavailable"
    assert context["semantic_location"] is None
    assert context["table_context"] is None
    assert context["context_confidence"] == 0.0


def test_layout_context_does_not_change_slot_ids_or_replacement_safety() -> None:
    html = "<table><tr><td>项目A</td><td>完成旧任务</td></tr></table>"
    raw = extract_editable_text_slots(html)
    before = [slot.slot_id for slot in raw]
    editable_text_slots_for_agent(raw, template_html=html)
    after = [slot.slot_id for slot in extract_editable_text_slots(html)]
    assert before == after


def test_invalid_and_overlapping_spans_degrade_safely() -> None:
    html = (
        '<table><tr><td rowspan="bad" colspan="0">A</td><td>B</td></tr>'
        '<tr><td rowspan="2" colspan="2">C</td><td>D</td></tr>'
        '<tr><td>E</td></tr></table>'
    )
    slots = _slots(html)
    first = slots["A"]["layout_context"]["table_context"]
    assert first["row_span"] == 1
    assert first["column_span"] == 1
    assert slots["E"]["layout_context"]["table_context"]["table_id"] == "table_0"


def test_blockquote_and_plain_div_text_have_generic_context() -> None:
    html = "<h2>风险</h2><blockquote>测试资源不足</blockquote><div>等待环境扩容</div>"
    slots = _slots(html)
    quote = slots["测试资源不足"]["layout_context"]
    assert quote["container_type"] == "blockquote"
    assert quote["document_context"]["nearest_heading"] == "风险"
    div = slots["等待环境扩容"]["layout_context"]
    assert div["container_type"] == "block_text"
    assert div["table_context"] is None


def test_compact_slots_keep_only_model_critical_fields() -> None:
    html = (
        "<table><tr><th>项目</th><th>本周进展</th><th>下周计划</th></tr>"
        "<tr><td>项目A</td><td>完成联调</td><td>性能测试</td></tr></table>"
    )
    raw = extract_editable_text_slots(html)
    compact = {
        item["text"]: item
        for item in compact_editable_text_slots_for_agent(raw, template_html=html)
    }
    assert set(compact["完成联调"]) == {"slot_id", "text", "location"}
    assert compact["完成联调"]["location"] == (
        "表格位置：第2行第2列；行表头：项目A；列表头：本周进展；右邻：性能测试"
    )
    assert compact["性能测试"]["location"] == (
        "表格位置：第2行第3列；行表头：项目A；列表头：下周计划；左邻：完成联调"
    )
    assert "layout_context" not in compact["完成联调"]
    assert "previous_text" not in compact["完成联调"]
    assert "next_text" not in compact["完成联调"]
    assert "html_path" not in compact["完成联调"]


def test_compact_slot_location_is_nullable_and_bounded() -> None:
    no_context_html = "<table><tr><td>孤立内容</td></tr></table>"
    no_context = compact_editable_text_slots_for_agent(
        extract_editable_text_slots(no_context_html),
        template_html=no_context_html,
    )[0]
    assert no_context["location"] is None

    long_header = "项目" * 200
    html = (
        f"<table><tr><th>{long_header}</th><th>本周进展</th></tr>"
        "<tr><td>项目A</td><td>完成联调</td></tr></table>"
    )
    compact = compact_editable_text_slots_for_agent(
        extract_editable_text_slots(html),
        template_html=html,
    )
    target = next(item for item in compact if item["text"] == "完成联调")
    assert target["location"] is not None
    assert len(target["location"]) <= 640




def test_compact_location_exposes_outlook_td_double_header_candidates() -> None:
    html = (
        '<table><tr><td rowspan="2">项目</td>'
        '<td colspan="2" style="text-align:center">工作内容</td></tr>'
        '<tr><td>本周进展</td><td>下周计划</td></tr>'
        '<tr><td>项目A</td><td>完成联调</td><td>性能测试</td></tr></table>'
    )
    compact = {
        item["text"]: item
        for item in compact_editable_text_slots_for_agent(
            extract_editable_text_slots(html), template_html=html
        )
    }
    progress_location = compact["完成联调"]["location"]
    plan_location = compact["性能测试"]["location"]
    assert isinstance(progress_location, str)
    assert "列表头：工作内容" in progress_location
    assert "列表头候选：本周进展" in progress_location
    assert "行表头：项目A" in progress_location
    assert "列表头候选：下周计划" in plan_location
    assert "左邻：完成联调" in plan_location

def test_compact_slot_payload_is_far_smaller_than_internal_layout_payload() -> None:
    rows = [
        "<table><tr><th>项目</th><th>本周进展</th><th>下周计划</th><th>风险</th></tr>"
    ]
    for index in range(20):
        rows.append(
            f"<tr><td>项目{index}</td><td>完成任务{index}</td>"
            f"<td>开展测试{index}</td><td>风险{index}</td></tr>"
        )
    rows.append("</table>")
    html = "".join(rows)
    raw = extract_editable_text_slots(html)
    full = editable_text_slots_for_agent(raw, template_html=html)
    compact = compact_editable_text_slots_for_agent(raw, template_html=html)
    full_size = len(json.dumps(full, ensure_ascii=False, separators=(",", ":")))
    compact_size = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
    assert compact_size < full_size * 0.10


def test_compact_layout_failure_still_returns_usable_slots(monkeypatch) -> None:
    def fail(_html, _slots):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(weekly_report, "build_layout_contexts", fail)
    html = "<p>完成联调</p>"
    compact = compact_editable_text_slots_for_agent(
        extract_editable_text_slots(html),
        template_html=html,
    )
    assert compact == [
        {
            "slot_id": compact[0]["slot_id"],
            "text": "完成联调",
            "location": None,
        }
    ]
