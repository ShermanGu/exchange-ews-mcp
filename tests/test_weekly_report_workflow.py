from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import (
    AttachmentResult,
    DraftResult,
    normalize_mail_folder,
    normalize_mail_folders,
)
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.weekly_report import (
    _extract_from_word_section,
    _close_open_html_fragment,
    apply_editable_text_slot_changes,
    extract_latest_weekly_body,
    extract_editable_text_slots,
    html_structure_sha256,
    split_weekly_report_sections,
    validate_text_only_html_update,
)
from exchange_ews_mcp.weekly_separator_whitelist import (
    WEEKLY_REPORT_SEPARATOR_WHITELIST,
)
from exchange_ews_mcp.workflow import SemanticMailWorkflow


WK3_TOP = '''<table width="760" style="width:570pt;table-layout:fixed"><tr><td>日期：2026-07-27 至 2026-08-02</td></tr><tr><td>完成旧任务</td></tr><tr><td><img src="cid:image001.png"></td></tr></table>'''
WK2 = '<div data-test="wk2-history">WK2_MARKER</div>'
WK1 = '<div data-test="wk1-history">WK1_MARKER</div>'

MSO_WEEKLY_SEPARATOR = (
    "<p class=MsoNormal><span lang=EN-US style='font-family:等线'>"
    "<o:p>&nbsp;</o:p></span></p>"
)
QUOTED_MSO_WEEKLY_SEPARATOR = (
    '<p class="MsoNormal"><span lang="EN-US" style="font-family:等线">'
    '<o:p>&nbsp;</o:p></span></p>'
)
ENTITY_FONT_MSO_WEEKLY_SEPARATOR = (
    '<p class="MsoNormal"><span lang="EN-US" '
    'style="font-family:&#31561;&#32447;"><o:p>&#160;</o:p></span></p>'
)
ENGLISH_MSO_WEEKLY_SEPARATOR = (
    '<p class="MsoNormal"><span lang="EN-US" '
    'style="font-family:Calibri,sans-serif"><o:p>&nbsp;</o:p></span></p>'
)

WORD_SECTION_SOURCE_BODY = (
    "<html><body><div class=WordSection1>\n"
    + WK3_TOP
    + MSO_WEEKLY_SEPARATOR
    + WK2
    + MSO_WEEKLY_SEPARATOR
    + WK1
    + "</div></body></html>"
)
SOURCE_BODY = WORD_SECTION_SOURCE_BODY


class StatefulWeeklyClient:
    def __init__(
        self,
        *,
        include_unique_body: bool = True,
        preserve_markers: bool = True,
        unique_body_truncated: bool = False,
        source_body: str = SOURCE_BODY,
        unique_body_html: str | None = None,
    ) -> None:
        self.include_unique_body = include_unique_body
        self.preserve_markers = preserve_markers
        self.unique_body_truncated = unique_body_truncated
        self.source_body = source_body
        self.unique_body_html = unique_body_html
        self.search_calls: list[dict] = []
        self.reply_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.attachment_reads: list[str] = []
        self.attachment_writes: list[dict] = []
        self.draft_body: str | None = None
        self.draft_subject = "RE: 项目周报"
        self.draft_change_key = "DCK1"
        self.get_email_limits: list[int | None] = []

    def search_emails_multi_folder(self, *, conversation_id=None, **kwargs):
        self.search_calls.append({"conversation_id": conversation_id, **kwargs})
        if conversation_id:
            return {
                "returned": 2,
                "items": [
                    {"item_id": "WK3", "conversation_id": "CONV"},
                    {"item_id": "WK2", "conversation_id": "CONV"},
                ],
            }
        return {
            "returned": 1,
            "items": [
                {
                    "item_id": "WK3",
                    "change_key": "WK3CK",
                    "subject": "项目周报",
                    "folder": "sentitems",
                    "conversation_id": "CONV",
                    "sent_at": "2026-08-03T01:00:00Z",
                }
            ],
        }

    def get_email(self, *, item_id, change_key=None, max_body_chars=500000):
        self.get_email_limits.append(max_body_chars)
        if item_id == "WK3":
            return {
                "item_id": "WK3",
                "change_key": "WK3CK",
                "subject": "项目周报",
                "folder": "sentitems",
                "conversation_id": "CONV",
                "sent_at": "2026-08-03T01:00:00Z",
                "is_draft": False,
                "body_html": self.source_body,
                "body_type": "HTML",
                "body_truncated": False,
                "unique_body_html": (
                    self.unique_body_html
                    if self.unique_body_html is not None
                    else (WK3_TOP if self.include_unique_body else "")
                ),
                "unique_body_type": "HTML",
                "unique_body_truncated": self.unique_body_truncated,
                "attachments": [
                    {
                        "attachment_id": "ATT1",
                        "name": "image001.png",
                        "content_type": "image/png",
                        "is_inline": True,
                        "content_id": "image001.png",
                    }
                ],
            }
        assert item_id == "DRAFT1"
        assert self.draft_body is not None
        return {
            "item_id": "DRAFT1",
            "change_key": self.draft_change_key,
            "subject": self.draft_subject,
            "folder": "drafts",
            "is_draft": True,
            "body_html": self.draft_body,
            "body_type": "HTML",
            "body_truncated": False,
            "unique_body_html": "",
            "unique_body_type": "HTML",
            "unique_body_truncated": False,
            "attachments": [],
        }

    def reply_as_draft(self, **kwargs):
        self.reply_calls.append(kwargs)
        top = kwargs["body_html"]
        if not self.preserve_markers:
            import re

            top = re.sub(r"<!--EWSMCP_WEEKLY_[^>]+-->", "", top)
            top = re.sub(r'<span style="[^"]+">EWSMCP_WEEKLY_[^<]+</span>', "", top)
        source_inner = self.source_body.split("<body>", 1)[1].rsplit("</body>", 1)[0]
        self.draft_body = (
            '<html><body>'
            + top
            + '<div class="native-reply-history">'
            + source_inner
            + '</div></body></html>'
        )
        return DraftResult(
            item_id="DRAFT1",
            change_key=self.draft_change_key,
            subject=self.draft_subject,
            draft_type="reply_all",
        )

    def get_file_attachment(self, *, attachment_id):
        self.attachment_reads.append(attachment_id)
        return {
            "attachment_id": attachment_id,
            "filename": "image001.png",
            "content_type": "image/png",
            "content_id": "image001.png",
            "is_inline": True,
            "content": b"PNGDATA",
            "size": 7,
        }

    def add_file_attachment_bytes_to_draft(self, **kwargs):
        self.attachment_writes.append(kwargs)
        self.draft_change_key = "DCK2"
        return AttachmentResult(
            attachment_id="NEWATT1",
            root_item_id="DRAFT1",
            root_item_change_key=self.draft_change_key,
            filename=kwargs["filename"],
            size=len(kwargs["content"]),
            content_type=kwargs["content_type"],
        )

    def update_draft(self, **kwargs):
        self.update_calls.append(kwargs)
        if kwargs.get("body_html") is not None:
            self.draft_body = kwargs["body_html"]
        if kwargs.get("subject") is not None:
            self.draft_subject = kwargs["subject"]
        self.draft_change_key = "DCK3"
        return DraftResult(
            item_id="DRAFT1",
            change_key=self.draft_change_key,
            subject=self.draft_subject,
            draft_type="updated",
        )


def _config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company.com"],
    )


def _workflow(tmp_path: Path, client: StatefulWeeklyClient) -> SemanticMailWorkflow:
    return SemanticMailWorkflow(
        client,  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _config(),
    )


def _extract(body: str, *, unique: str = "SHOULD_NOT_BE_USED"):
    return extract_latest_weekly_body(
        full_body_html=body,
        unique_body_html=unique,
        unique_body_type="HTML",
        conversation_has_older_items=True,
    )


def test_extract_starts_immediately_after_word_section_and_stops_before_first_separator() -> None:
    extracted = _extract(WORD_SECTION_SOURCE_BODY)
    assert extracted.strategy == "word_section1_to_first_top_level_whitelist_separator"
    assert extracted.html.strip() == WK3_TOP
    assert not extracted.html.lstrip().startswith("<div class=WordSection1")
    assert "MsoNormal" not in extracted.html
    assert "WK2_MARKER" not in extracted.html
    assert "WK1_MARKER" not in extracted.html
    assert extracted.scanned_characters == len("\n" + WK3_TOP)
    assert extracted.scan_limit == 500_000


def test_empty_msonormal_with_lang_only_is_not_a_weekly_separator() -> None:
    leading_blank = (
        '<p class="MsoNormal" align="left" style="text-align:left">'
        '<span lang="EN-US"><o:p>&nbsp;</o:p></span></p>'
    )
    body = (
        '<html><body><div class="WordSection1">'
        + leading_blank + WK3_TOP + MSO_WEEKLY_SEPARATOR + WK2
        + '</div></body></html>'
    )
    extracted = _extract(body)
    assert leading_blank in extracted.html
    assert WK3_TOP in extracted.html
    assert "WK2_MARKER" not in extracted.html
    assert extracted.separator_variant == "outlook_dengxian_unquoted_single_quoted"


def test_lang_only_blank_at_word_section_start_does_not_create_empty_body_error() -> None:
    leading_blank = (
        '<p class=MsoNormal align=left style="text-align:left">'
        '<span lang=EN-US><o:p>&#160;</o:p></span></p>'
    )
    body = (
        '<html><body><div class=WordSection1>'
        + leading_blank + '<p>REAL_WEEKLY_BODY</p>'
        + QUOTED_MSO_WEEKLY_SEPARATOR + WK2 + '</div></body></html>'
    )
    extracted = _extract(body)
    assert "REAL_WEEKLY_BODY" in extracted.html
    assert "WK2_MARKER" not in extracted.html


def test_extract_accepts_only_exact_whitelist_serializations() -> None:
    expected_names = {
        MSO_WEEKLY_SEPARATOR: "outlook_dengxian_unquoted_single_quoted",
        QUOTED_MSO_WEEKLY_SEPARATOR: "outlook_dengxian_double_quoted",
    }
    assert set(WEEKLY_REPORT_SEPARATOR_WHITELIST.values()) == set(expected_names)
    for separator, expected_name in expected_names.items():
        body = (
            '<html><body><div class="WordSection1">'
            + WK3_TOP + separator + WK2 + separator + WK1
            + '</div></body></html>'
        )
        extracted = _extract(body)
        assert extracted.html == WK3_TOP
        assert extracted.separator_variant == expected_name
        assert extracted.separator_language == "en-us"


def test_extract_does_not_normalize_entity_encoded_near_match() -> None:
    body = (
        '<html><body><div class=WordSection1>'
        + WK3_TOP + ENTITY_FONT_MSO_WEEKLY_SEPARATOR + WK2
        + '</div></body></html>'
    )
    with pytest.raises(ValueError, match="白名单完全一致"):
        _extract(body)


def test_extract_does_not_accept_non_whitelisted_english_font() -> None:
    body = (
        '<html><body><div class=WordSection1>'
        + WK3_TOP + ENGLISH_MSO_WEEKLY_SEPARATOR + WK2
        + '</div></body></html>'
    )
    with pytest.raises(ValueError, match="白名单完全一致"):
        _extract(body)


def test_exact_whitelist_block_inside_table_is_ignored() -> None:
    nested = '<table><tr><td>' + MSO_WEEKLY_SEPARATOR + '</td></tr></table>'
    body = (
        '<html><body><div class=WordSection1>'
        + '<p>WK3_START</p>' + nested + '<p>WK3_END</p>'
        + QUOTED_MSO_WEEKLY_SEPARATOR + WK2
        + '</div></body></html>'
    )
    extracted = _extract(body)
    assert 'WK3_START' in extracted.html
    assert nested in extracted.html
    assert 'WK3_END' in extracted.html
    assert 'WK2_MARKER' not in extracted.html
    assert extracted.separator_variant == 'outlook_dengxian_double_quoted'


def test_nested_whitelist_without_top_level_separator_is_rejected() -> None:
    nested = '<table><tr><td>' + MSO_WEEKLY_SEPARATOR + '</td></tr></table>'
    body = (
        '<html><body><div class=WordSection1>'
        + '<p>WK3</p>' + nested + '<p>STILL_WK3</p>'
        + '</div></body></html>'
    )
    with pytest.raises(ValueError, match="直属子块"):
        _extract(body)


def test_whitespace_changed_top_level_block_is_not_exact_match() -> None:
    changed = (
        '<p class="MsoNormal"> <span lang="EN-US" style="font-family:等线">'
        '<o:p>&nbsp;</o:p></span></p>'
    )
    body = '<div class=WordSection1><p>WK3</p>' + changed + '</div>'
    with pytest.raises(ValueError, match="白名单完全一致"):
        _extract_from_word_section(body)


def test_exact_whitelist_block_inside_nested_div_is_ignored() -> None:
    nested = '<div class="report-box">' + MSO_WEEKLY_SEPARATOR + '</div>'
    body = (
        '<html><body><div class=WordSection1>'
        + '<p>WK3_START</p>' + nested + '<p>WK3_END</p>'
        + MSO_WEEKLY_SEPARATOR + WK2
        + '</div></body></html>'
    )
    extracted = _extract(body)
    assert nested in extracted.html
    assert 'WK3_END' in extracted.html
    assert 'WK2_MARKER' not in extracted.html


def test_top_level_near_match_is_not_separator() -> None:
    near_match = (
        '<p class="MsoNormal" align="left"><span lang="EN-US" '
        'style="font-family:等线"><o:p>&nbsp;</o:p></span></p>'
    )
    body = (
        '<html><body><div class=WordSection1>'
        + '<p>WK3_START</p>' + near_match + '<p>WK3_END</p>'
        + MSO_WEEKLY_SEPARATOR + WK2 + '</div></body></html>'
    )
    extracted = _extract(body)
    assert near_match in extracted.html
    assert 'WK3_END' in extracted.html


def test_extract_uses_first_separator_only() -> None:
    body = (
        '<html><body><div class=WordSection1>'
        + '<p>WK3_ONLY</p>' + MSO_WEEKLY_SEPARATOR
        + '<p>WK2_MUST_NOT_COPY</p>' + MSO_WEEKLY_SEPARATOR
        + '<p>WK1_MUST_NOT_COPY</p></div></body></html>'
    )
    extracted = _extract(body)
    assert "WK3_ONLY" in extracted.html
    assert "WK2_MUST_NOT_COPY" not in extracted.html
    assert "WK1_MUST_NOT_COPY" not in extracted.html


def test_extract_requires_word_section1_even_when_unique_body_exists() -> None:
    with pytest.raises(ValueError, match="未找到 WordSection1"):
        _extract('<html><body>' + WK3_TOP + MSO_WEEKLY_SEPARATOR + WK2 + '</body></html>')


def test_extract_refuses_when_separator_is_beyond_scan_threshold() -> None:
    body_inner = (
        '<div class=WordSection1><p>WK3</p>'
        + ('x' * 101) + MSO_WEEKLY_SEPARATOR + WK2 + '</div>'
    )
    with pytest.raises(ValueError, match="超过 100 字符阈值"):
        _extract_from_word_section(body_inner, scan_limit=100)


def test_extract_refuses_when_separator_is_missing_before_section_end() -> None:
    body_inner = '<div class=WordSection1><p>WK3 without separator</p></div>'
    with pytest.raises(ValueError, match="白名单完全一致"):
        _extract_from_word_section(body_inner, scan_limit=500_000)


def test_close_open_html_fragment_keeps_raw_table_attributes() -> None:
    fragment = '<div class="report"><table width="760"><tr><td>WK3</td></tr></table>'
    closed, closers = _close_open_html_fragment(fragment)
    assert closed == fragment + '</div>'
    assert closers == ('div',)
    assert '<table width="760">' in closed


def test_folder_aliases_are_normalized_for_search_and_prepare() -> None:
    assert normalize_mail_folder(" 收件箱 ") == "inbox"
    assert normalize_mail_folder("Sent Items") == "sentitems"
    assert normalize_mail_folder("已发送邮件") == "sentitems"
    assert normalize_mail_folders(
        folder="收件箱",
        folders=["收件箱", "INBOX", "已发送邮件", "Sent Items"],
        default_folder="sentitems",
    ) == ["inbox", "sentitems"]
    with pytest.raises(ValueError, match="不支持的邮箱文件夹"):
        normalize_mail_folder("周报文件夹")


def test_context_normalizes_folder_and_folders_override_scalar(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(source_body=WORD_SECTION_SOURCE_BODY)
    result = _workflow(tmp_path, client).get_weekly_report_context(
        user_input="更新项目进度",
        folder="收件箱",
        folders=["已发送邮件", "Sent Items"],
    )
    assert result["status"] == "context_ready"
    assert result["search_folders"] == ["sentitems"]
    assert client.search_calls[0]["folders"] == ["sentitems"]
    assert client.reply_calls == []
    assert client.update_calls == []


def test_split_history_supports_one_or_two_consecutive_separators() -> None:
    body = (
        '<html><body><div class=WordSection1>'
        + '<p>WK5</p>'
        + MSO_WEEKLY_SEPARATOR
        + MSO_WEEKLY_SEPARATOR
        + '<p>WK4</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>WK3</p>'
        + MSO_WEEKLY_SEPARATOR
        + MSO_WEEKLY_SEPARATOR
        + '<p>WK2</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>WK1</p>'
        + '</div></body></html>'
    )
    reports = split_weekly_report_sections(body, max_reports=5)
    assert [item.text for item in reports] == ["WK5", "WK4", "WK3", "WK2", "WK1"]


def test_context_returns_latest_template_and_five_full_text_reports(tmp_path: Path) -> None:
    body = (
        '<html><body><div class=WordSection1>'
        + '<table><tr><td>项目A：本周完成联调</td></tr></table>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>项目A：上周完成开发</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>项目A：完成设计</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>项目A：完成需求分析</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>项目A：项目启动</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>更早内容</p>'
        + '</div></body></html>'
    )
    client = StatefulWeeklyClient(source_body=body)
    result = _workflow(tmp_path, client).get_weekly_report_context(
        user_input="项目A完成测试，项目B保持不变",
        reference_materials=[{"name": "进展.txt", "content": "项目A测试通过"}],
        max_reports=5,
    )
    assert result["status"] == "context_ready"
    assert result["weekly_flow_token"].startswith("weeklyflow_")
    assert result["report_count"] == 5
    assert result["response_profile"] == "compact_slots_v1"
    assert result["editable_slot_count"] >= 1
    assert any("项目A：本周完成联调" in item["text"] for item in result["editable_slots"])
    assert all(set(item) == {"slot_id", "text", "location"} for item in result["editable_slots"])
    assert "项目A完成测试" in result["agent_prompt"]
    assert "项目A测试通过" in result["agent_prompt"]
    assert "项目A：本周完成联调" in result["agent_prompt"]
    assert "项目A：项目启动" in result["agent_prompt"]
    assert "不会接触或生成 HTML" in result["agent_prompt"]
    assert "不得机械复制用户的口语" in result["agent_prompt"]
    assert "完成接口联调" in result["agent_prompt"]
    assert "不得把“阶段完成”夸大为“项目完成”" in result["agent_prompt"]
    assert "weekly_flow_token" in result["agent_prompt"]
    assert "update_weekly_report" in result["agent_prompt"]
    assert "expected_text" not in result["agent_prompt"]
    assert "不要重复提交旧文本" in result["agent_prompt"]
    assert "【日期硬校验】" in result["agent_prompt"]
    assert "必须逐一检查本次 editable_slots 中所有包含日期" in result["agent_prompt"]
    assert "默认保持原格式并将最新周期整体顺延七天" in result["agent_prompt"]
    assert "所有应更新日期都已包含在 changes 中" in result["agent_prompt"]
    assert "请直接使用本次工具结果中的 editable_slots 字段" in result["agent_prompt"]
    assert all(item["slot_id"] not in result["agent_prompt"] for item in result["editable_slots"])
    assert result["agent_prompt"].count("[历史周报 ") == 5
    assert "latest_report_html" not in result
    assert "latest_report_text" not in result
    assert "historical_reports" not in result
    assert "reference_materials" not in result
    assert "editing_contract" not in result
    assert "layout_summary" not in result
    assert "layout_context_version" not in result
    assert client.reply_calls == []


def test_text_only_html_validation_accepts_text_changes_and_preserves_structure() -> None:
    template = '<table width="760"><tr><td>旧进展</td><td>保持不变</td></tr></table>'
    candidate = '<table width="760"><tr><td>新进展</td><td>保持不变</td></tr></table>'
    result = validate_text_only_html_update(template, candidate)
    assert result.changed_text_slots == 1
    assert result.tag_count == 8
    assert result.structure_sha256 == html_structure_sha256(template)


def test_text_only_html_validation_rejects_any_tag_or_attribute_change() -> None:
    template = '<table width="760"><tr><td>旧进展</td></tr></table>'
    with pytest.raises(ValueError, match="标签或注释发生变化"):
        validate_text_only_html_update(
            template,
            '<table width="761"><tr><td>新进展</td></tr></table>',
        )
    with pytest.raises(ValueError, match="标签/注释数量发生变化"):
        validate_text_only_html_update(
            template,
            '<table width="760"><tr><td><b>新进展</b></td></tr></table>',
        )


def test_text_only_html_validation_rejects_markdown_wrapper() -> None:
    with pytest.raises(ValueError, match="Markdown"):
        validate_text_only_html_update('<p>旧</p>', '```html\n<p>新</p>\n```')


def test_editable_slots_apply_plain_text_without_changing_html_structure() -> None:
    template = '<table width="760"><tr><td> 旧进展 </td><td>保持不变</td></tr></table>'
    slots = extract_editable_text_slots(template)
    target = next(item for item in slots if item.text.strip() == "旧进展")
    result = apply_editable_text_slot_changes(
        template,
        [
            {
                "slot_id": target.slot_id,
                "new_text": "新进展 <完成>",
            }
        ],
    )
    assert '<table width="760">' in result.html
    assert " 新进展 &lt;完成&gt; " in result.html
    assert result.html_validation.changed_text_slots == 1


def test_editable_slots_skip_whitespace_and_protected_text() -> None:
    template = (
        '<div>\n<span>项目A</span><style>.x{color:red}</style>'
        '<script>const x = 1;</script><span>&nbsp;</span><span>进展</span></div>'
    )
    slots = extract_editable_text_slots(template)
    assert [item.text for item in slots] == ["项目A", "进展"]
    assert slots[0].html_path[-1] == "span"


def test_slot_update_preserves_original_entity_when_text_is_unchanged() -> None:
    template = '<p>A&amp;B</p>'
    slot = extract_editable_text_slots(template)[0]
    result = apply_editable_text_slot_changes(
        template,
        [{"slot_id": slot.slot_id, "new_text": "A&B"}],
    )
    assert result.html == template
    assert result.unchanged_changes == 1


def test_slot_update_requires_exact_schema_without_expected_text() -> None:
    template = '<p>旧文本</p>'
    slot = extract_editable_text_slots(template)[0]
    with pytest.raises(ValueError, match="new_text 缺失"):
        apply_editable_text_slot_changes(
            template,
            [{"slot_id": slot.slot_id}],
        )
    with pytest.raises(ValueError, match="不支持的字段"):
        apply_editable_text_slot_changes(
            template,
            [
                {
                    "slot_id": slot.slot_id,
                    "new_text": "新文本",
                    "expected_text": "旧接口字段",
                }
            ],
        )


def test_update_replies_all_once_with_slot_text_and_no_body_update(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(
        user_input="日期改到下周，完成测试",
        reference_materials=[{"name": "测试结果", "content": "全部通过"}],
    )
    slots = {item["text"]: item for item in context["editable_slots"]}
    changes = [
        {
            "slot_id": slots["日期：2026-07-27 至 2026-08-02"]["slot_id"],
            "new_text": "日期：2026-08-03 至 2026-08-09",
        },
        {
            "slot_id": slots["完成旧任务"]["slot_id"],
            "new_text": "完成旧任务并通过测试",
        },
    ]

    result = workflow.update_weekly_report(
        weekly_flow_token=context["weekly_flow_token"],
        changes=changes,
        subject="项目周报 2026-08-03 至 2026-08-09",
    )

    assert result["status"] == "draft_created"
    assert result["weekly_flow_status"] == "completed"
    assert workflow.store.get_action_session(context["weekly_flow_token"])["status"] == "completed"
    assert result["reply_all"] is True
    assert result["body_update_after_reply"] is False
    assert len(client.reply_calls) == 1
    call = client.reply_calls[0]
    assert call["item_id"] == "WK3"
    assert call["reply_all"] is True
    assert "日期：2026-08-03 至 2026-08-09" in call["body_html"]
    assert "完成旧任务并通过测试" in call["body_html"]
    assert '<table width="760" style="width:570pt;table-layout:fixed">' in call["body_html"]
    assert "WK2_MARKER" not in call["body_html"]
    assert "WK1_MARKER" not in call["body_html"]
    # update_draft is allowed only for Subject; the Body must never be updated.
    assert len(client.update_calls) == 1
    assert client.update_calls[0]["subject"] == "项目周报 2026-08-03 至 2026-08-09"
    assert client.update_calls[0].get("body_html") is None
    assert result["slot_update"]["applied_changes"] == 2
    assert result["html_validation"]["changed_text_slots"] == 2
    assert client.attachment_reads == ["ATT1"]
    assert client.attachment_writes[0]["content_id"] == "image001.png"
    assert client.draft_body is not None
    assert client.draft_body.count("WK2_MARKER") == 1
    assert client.draft_body.count("WK1_MARKER") == 1


def test_update_rejects_unknown_or_legacy_field_before_reply(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新进度")
    with pytest.raises(ValueError, match="未知或已过期"):
        workflow.update_weekly_report(
            weekly_flow_token=context["weekly_flow_token"],
            changes=[{"slot_id": "slot_missing", "new_text": "新进展"}],
        )
    failed_flow = workflow.store.get_action_session(context["weekly_flow_token"])
    assert failed_flow["status"] == "failed"

    second_context = workflow.get_weekly_report_context(user_input="更新进度")
    first = second_context["editable_slots"][0]
    with pytest.raises(ValueError, match="不支持的字段"):
        workflow.update_weekly_report(
            weekly_flow_token=second_context["weekly_flow_token"],
            changes=[
                {
                    "slot_id": first["slot_id"],
                    "new_text": "新进展",
                    "expected_text": "旧接口字段",
                }
            ],
        )
    assert workflow.store.get_action_session(second_context["weekly_flow_token"])["status"] == "failed"
    assert client.reply_calls == []
    assert client.update_calls == []


def test_update_rejects_stale_context_without_creating_draft(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新进度")
    target = next(item for item in context["editable_slots"] if item["text"] == "完成旧任务")
    client.source_body = client.source_body.replace("WK2_MARKER", "WK2_CHANGED")
    result = workflow.update_weekly_report(
        weekly_flow_token=context["weekly_flow_token"],
        changes=[
            {
                "slot_id": target["slot_id"],
                "new_text": "完成新任务",
            }
        ],
    )
    assert result["status"] == "context_stale"
    assert result["weekly_flow_status"] == "context_stale"
    assert workflow.store.get_action_session(context["weekly_flow_token"])["status"] == "context_stale"
    assert result["draft_created"] is False
    assert client.reply_calls == []


def test_context_does_not_accept_more_than_five_reports(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    with pytest.raises(ValueError, match="max_reports"):
        workflow.get_weekly_report_context(user_input="更新", max_reports=6)



def test_text_only_html_validation_rejects_whitespace_reformatting() -> None:
    template = '<table>\n<tr><td>旧</td></tr>\n</table>'
    candidate = '<table><tr><td>新</td></tr></table>'
    with pytest.raises(ValueError, match="空白文本"):
        validate_text_only_html_update(template, candidate)


def test_split_never_returns_truncated_final_report() -> None:
    body = (
        '<div class=WordSection1><p>WK2</p>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>' + ('x' * 200) + '</p></div>'
    )
    with pytest.raises(ValueError, match="不会返回被截断"):
        split_weekly_report_sections(body, max_reports=5, scan_limit=100)


def test_update_rejects_context_when_a_newer_weekly_report_appears(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新进度")
    original_search = client.search_emails_multi_folder

    def newer_search(*, conversation_id=None, **kwargs):
        if conversation_id:
            return original_search(conversation_id=conversation_id, **kwargs)
        return {
            "returned": 1,
            "items": [{"item_id": "WK4", "change_key": "WK4CK", "subject": "项目周报"}],
        }

    client.search_emails_multi_folder = newer_search  # type: ignore[method-assign]
    result = workflow.update_weekly_report(
        weekly_flow_token=context["weekly_flow_token"],
        changes=[],
        subject="项目周报",
    )
    assert result["status"] == "context_stale"
    assert client.reply_calls == []



def test_update_requires_fresh_one_shot_flow_token(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    with pytest.raises(ValueError, match="必须先调用 get_weekly_report_context"):
        workflow.update_weekly_report(
            weekly_flow_token="weeklyflow_not_real",
            changes=[],
            subject="项目周报",
        )
    assert client.reply_calls == []


def test_completed_weekly_flow_token_cannot_be_reused(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="只更新标题")
    token = context["weekly_flow_token"]
    first = workflow.update_weekly_report(
        weekly_flow_token=token,
        changes=[],
        subject="新周报标题",
    )
    assert first["weekly_flow_status"] == "completed"
    with pytest.raises(ValueError, match="已使用"):
        workflow.update_weekly_report(
            weekly_flow_token=token,
            changes=[],
            subject="再次更新",
        )
    assert len(client.reply_calls) == 1


def test_new_context_supersedes_older_unused_token(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    first = workflow.get_weekly_report_context(user_input="项目A完成开发")
    second = workflow.get_weekly_report_context(user_input="项目A完成联调")
    assert first["weekly_flow_token"] != second["weekly_flow_token"]
    assert workflow.store.get_action_session(first["weekly_flow_token"])["status"] == "superseded"
    assert workflow.store.get_action_session(second["weekly_flow_token"])["status"] == "context_ready"
    with pytest.raises(ValueError, match="新的上下文取代"):
        workflow.update_weekly_report(
            weekly_flow_token=first["weekly_flow_token"],
            changes=[],
            subject="旧流程",
        )
    assert client.reply_calls == []


def test_weekly_flow_context_has_short_expiry(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新进度")
    assert context["weekly_flow_ttl_minutes"] == 30
    assert context["weekly_flow_expires_at"]


def test_split_rejects_more_than_two_consecutive_separators() -> None:
    body = (
        '<div class=WordSection1><p>WK2</p>'
        + MSO_WEEKLY_SEPARATOR * 3
        + '<p>WK1</p></div>'
    )
    with pytest.raises(ValueError, match="超过 2 个"):
        split_weekly_report_sections(body, max_reports=5)


def test_context_supports_non_table_html_layout(tmp_path: Path) -> None:
    body = (
        '<html><body><div class=WordSection1>'
        '<h2>项目A</h2><p>完成接口联调。</p><ul><li>准备性能测试</li></ul>'
        + MSO_WEEKLY_SEPARATOR
        + '<p>历史内容</p></div></body></html>'
    )
    client = StatefulWeeklyClient(source_body=body)
    result = _workflow(tmp_path, client).get_weekly_report_context(
        user_input="项目A联调完成，下周做性能测试"
    )
    slots = {item["text"]: item for item in result["editable_slots"]}
    assert result["effective_body_type"] == "HTML"
    assert slots["完成接口联调。"]["location"] == (
        "章节：项目A；内容类型：段落；文本块标签：p；下一相邻块：准备性能测试"
    )
    assert slots["准备性能测试"]["location"] == (
        "章节：项目A；内容类型：列表项；列表位置：第1项，第1层；上一相邻块：完成接口联调。"
    )
    assert all("layout_context" not in item for item in result["editable_slots"])


def test_context_rejects_non_html_ews_body_type_clearly(tmp_path: Path) -> None:
    class TextBodyClient(StatefulWeeklyClient):
        def get_email(self, **kwargs):
            result = super().get_email(**kwargs)
            result["body_type"] = "Text"
            return result

    client = TextBodyClient(source_body=WORD_SECTION_SOURCE_BODY)
    with pytest.raises(ValueError, match="只接受 EWS 返回的 HTML 正文"):
        _workflow(tmp_path, client).get_weekly_report_context(user_input="更新项目A")
