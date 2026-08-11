from __future__ import annotations

from pathlib import Path

import pytest

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import AttachmentResult, DraftResult, normalize_mail_folder, normalize_mail_folders
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.weekly_report import (
    _close_open_html_fragment,
    _extract_from_word_section,
    apply_editable_text_slot_changes,
    extract_editable_text_slots,
    extract_latest_weekly_body,
    html_structure_sha256,
    split_weekly_report_sections,
    validate_text_only_html_update,
)
from exchange_ews_mcp.workflow import SemanticMailWorkflow


WK3_TOP = (
    '<table width="760" style="width:570pt;table-layout:fixed">'
    '<tr><td>日期：2026-07-27 至 2026-08-02</td></tr>'
    '<tr><td>完成旧任务</td></tr>'
    '<tr><td><img src="cid:image001.png"></td></tr></table>'
)


def _reply_body(template: str, *, keyword: str = "From", older: str = "OLDER") -> str:
    # The history keyword is intentionally nested. The extractor must rewind to
    # the direct child div (depth zero relative to WordSection1), not cut at the
    # inner span/p/table.
    return (
        '<html><body><div class="WordSection1">'
        + template
        + '<div class="quoted-history"><table><tr><td><p><span>'
        + keyword
        + ': previous@company.com</span></p></td></tr></table><p>'
        + older
        + '</p></div></div></body></html>'
    )


def _fresh_body(template: str, *, word_section: bool = True) -> str:
    if word_section:
        return '<html><body><div class="WordSection1">' + template + '</div></body></html>'
    return '<html><body>' + template + '</body></html>'


class StatefulWeeklyClient:
    def __init__(
        self,
        *,
        latest_body: str | None = None,
        latest_subject: str = "项目周报 2026-07-27 至 2026-08-02",
        include_history: bool = True,
    ) -> None:
        self.latest_subject = latest_subject
        self.search_calls: list[dict] = []
        self.reply_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.attachment_reads: list[str] = []
        self.attachment_writes: list[dict] = []
        self.draft_body: str | None = None
        self.draft_subject = "RE: " + latest_subject
        self.draft_change_key = "DCK1"
        self.get_email_limits: list[int | None] = []

        default_latest = _reply_body(WK3_TOP, older="WK2_QUOTED") if include_history else _fresh_body(WK3_TOP)
        self.messages: dict[str, dict] = {
            "WK3": {
                "item_id": "WK3",
                "change_key": "WK3CK",
                "subject": latest_subject,
                "folder": "sentitems",
                "conversation_id": "CONV",
                "sent_at": "2026-08-03T01:00:00Z",
                "is_draft": False,
                "body_html": latest_body if latest_body is not None else default_latest,
                "body_type": "HTML",
                "body_truncated": False,
                "to": [{"email": "team@company.com"}, {"email": "owner@company.com"}],
                "cc": [{"email": "manager@company.com"}],
                "bcc": [],
                "attachments": [
                    {
                        "attachment_id": "ATT1",
                        "name": "image001.png",
                        "content_type": "image/png",
                        "is_inline": True,
                        "content_id": "image001.png",
                    }
                ],
            },
            "WK2": {
                "item_id": "WK2", "change_key": "WK2CK", "subject": "项目周报",
                "folder": "sentitems", "conversation_id": "CONV", "sent_at": "2026-07-27T01:00:00Z",
                "is_draft": False, "body_html": _reply_body('<p>WK2_MARKER</p>', older="WK1_QUOTED"),
                "body_type": "HTML", "body_truncated": False, "to": [], "cc": [], "bcc": [], "attachments": [],
            },
            "WK1": {
                "item_id": "WK1", "change_key": "WK1CK", "subject": "项目周报",
                "folder": "sentitems", "conversation_id": "CONV", "sent_at": "2026-07-20T01:00:00Z",
                "is_draft": False, "body_html": _fresh_body('<p>WK1_MARKER</p>'),
                "body_type": "HTML", "body_truncated": False, "to": [], "cc": [], "bcc": [], "attachments": [],
            },
            "WK0": {
                "item_id": "WK0", "change_key": "WK0CK", "subject": "项目周报",
                "folder": "sentitems", "conversation_id": "CONV", "sent_at": "2026-07-13T01:00:00Z",
                "is_draft": False, "body_html": _fresh_body('<p>WK0_MARKER</p>'),
                "body_type": "HTML", "body_truncated": False, "to": [], "cc": [], "bcc": [], "attachments": [],
            },
            "WKM1": {
                "item_id": "WKM1", "change_key": "WKM1CK", "subject": "项目周报",
                "folder": "sentitems", "conversation_id": "CONV", "sent_at": "2026-07-06T01:00:00Z",
                "is_draft": False, "body_html": _fresh_body('<p>WKM1_MARKER</p>'),
                "body_type": "HTML", "body_truncated": False, "to": [], "cc": [], "bcc": [], "attachments": [],
            },
        }

    def search_emails_multi_folder(self, **kwargs):
        self.search_calls.append(dict(kwargs))
        if kwargs.get("participant_contains") or kwargs.get("sender"):
            return {"returned": 0, "items": []}
        items = [
            {k: v for k, v in self.messages[item_id].items() if k not in {"body_html", "attachments", "to", "cc", "bcc"}}
            for item_id in ("WK3", "WK2", "WK1", "WK0", "WKM1")
        ]
        limit = int(kwargs.get("limit") or len(items))
        return {"returned": min(limit, len(items)), "items": items[:limit]}

    def search_emails(self, **kwargs):
        return {"returned": 0, "items": []}

    def resolve_names(self, *, query, limit):
        return {"status": "resolved", "candidates": [{"email": query, "display_name": query}]}

    def get_email(self, *, item_id, change_key=None, max_body_chars=500000):
        self.get_email_limits.append(max_body_chars)
        if item_id == "DRAFT1":
            assert self.draft_body is not None
            return {
                "item_id": "DRAFT1", "change_key": self.draft_change_key,
                "subject": self.draft_subject, "folder": "drafts", "is_draft": True,
                "body_html": self.draft_body, "body_type": "HTML", "body_truncated": False,
                "attachments": [],
            }
        return dict(self.messages[item_id])

    def reply_as_draft(self, **kwargs):
        self.reply_calls.append(kwargs)
        top = kwargs["body_html"]
        source = self.messages["WK3"]["body_html"]
        self.draft_body = '<html><body>' + top + '<div class="native-reply-history">' + source + '</div></body></html>'
        return DraftResult(
            item_id="DRAFT1", change_key=self.draft_change_key,
            subject=self.draft_subject, draft_type="reply_all" if kwargs.get("reply_all") else "reply",
        )

    def create_draft(self, **kwargs):
        self.create_calls.append(kwargs)
        self.draft_body = kwargs["body_html"]
        self.draft_subject = kwargs["subject"]
        return DraftResult(
            item_id="DRAFT1", change_key=self.draft_change_key, subject=self.draft_subject,
            to=list(kwargs.get("to") or []), cc=list(kwargs.get("cc") or []),
            bcc=list(kwargs.get("bcc") or []), draft_type="new",
        )

    def get_file_attachment(self, *, attachment_id):
        self.attachment_reads.append(attachment_id)
        return {
            "attachment_id": attachment_id, "filename": "image001.png", "content_type": "image/png",
            "content_id": "image001.png", "is_inline": True, "content": b"PNGDATA", "size": 7,
        }

    def add_file_attachment_bytes_to_draft(self, **kwargs):
        self.attachment_writes.append(kwargs)
        self.draft_change_key = "DCK2"
        return AttachmentResult(
            attachment_id="NEWATT1", root_item_id="DRAFT1", root_item_change_key=self.draft_change_key,
            filename=kwargs["filename"], size=len(kwargs["content"]), content_type=kwargs["content_type"],
        )

    def update_draft(self, **kwargs):
        self.update_calls.append(kwargs)
        if kwargs.get("body_html") is not None:
            self.draft_body = kwargs["body_html"]
        if kwargs.get("subject") is not None:
            self.draft_subject = kwargs["subject"]
        self.draft_change_key = "DCK3"
        return DraftResult(
            item_id="DRAFT1", change_key=self.draft_change_key,
            subject=self.draft_subject, draft_type="updated",
        )


def _config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company.com"],
    )


def _workflow(tmp_path: Path, client: StatefulWeeklyClient) -> SemanticMailWorkflow:
    return SemanticMailWorkflow(client, ReferenceStore(tmp_path / "state.db"), _config())  # type: ignore[arg-type]


def _extract(body: str):
    return extract_latest_weekly_body(
        full_body_html=body, unique_body_html=None, unique_body_type=None,
        conversation_has_older_items=True,
    )


def test_history_keyword_from_rewinds_to_depth_zero_block() -> None:
    body = _reply_body('<p>WK3</p>', keyword="From", older="WK2")
    result = _extract(body)
    assert result.history_detected is True
    assert result.history_keyword == "From"
    assert result.strategy == "word_section1_to_first_history_header"
    assert result.html == '<p>WK3</p>'
    assert "quoted-history" not in result.html
    assert "WK2" not in result.html


def test_history_keyword_chinese_sender_is_supported() -> None:
    result = _extract(_reply_body('<p>本周进展</p>', keyword="发件人", older="旧周报"))
    assert result.history_detected is True
    assert result.history_keyword == "发件人"
    assert "旧周报" not in result.html


def test_history_keyword_search_ignores_attributes_script_and_fromage() -> None:
    body = (
        '<html><body><div class="WordSection1">'
        '<p data-label="From">Fromage 完成测试</p><style>.x{content:"发件人"}</style>'
        '</div></body></html>'
    )
    result = _extract(body)
    assert result.history_detected is False
    assert "Fromage 完成测试" in result.html


def test_no_history_keyword_means_fresh_message_and_wordsection_is_optional() -> None:
    result = _extract(_fresh_body('<p>新建周报</p>', word_section=False))
    assert result.history_detected is False
    assert result.strategy == "body_full_body_no_history_header"
    assert result.html == '<p>新建周报</p>'


def test_extract_from_word_section_uses_keyword_strategy_and_scan_limit() -> None:
    body = '<div class=WordSection1><p>WK3</p>' + ('x' * 101) + '</div>'
    with pytest.raises(ValueError, match="扫描阈值"):
        _extract_from_word_section(body, scan_limit=100)


def test_split_compatibility_returns_only_current_message_top_body() -> None:
    result = split_weekly_report_sections(_reply_body('<p>WK3</p>', older="WK2"))
    assert len(result) == 1
    assert result[0].text == "WK3"


def test_close_open_html_fragment_keeps_raw_attributes() -> None:
    fragment = '<div class="report"><table width="760"><tr><td>WK3</td></tr></table>'
    closed, closers = _close_open_html_fragment(fragment)
    assert closed == fragment + '</div>'
    assert closers == ('div',)


def test_folder_aliases_are_normalized() -> None:
    assert normalize_mail_folder(" 收件箱 ") == "inbox"
    assert normalize_mail_folder("Sent Items") == "sentitems"
    assert normalize_mail_folders(
        folder="收件箱", folders=["收件箱", "INBOX", "已发送邮件", "Sent Items"],
        default_folder="sentitems",
    ) == ["inbox", "sentitems"]


def test_context_returns_compact_three_week_contract(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    result = _workflow(tmp_path, client).get_weekly_report_context(
        user_input="项目A完成联调", reference_materials=[{"name": "测试", "content": "全部通过"}]
    )
    assert set(result) == {"resume_token", "mode", "subject", "request", "slots", "history"}
    assert result["resume_token"].startswith("weeklyflow_")
    assert result["mode"] == "reply_all"
    assert result["subject"] == "项目周报 2026-08-03 至 2026-08-09"
    assert result["request"] == "项目A完成联调"
    assert [item["text"] for item in result["history"]] == ["WK2_MARKER", "WK1_MARKER"]
    assert len(result["history"]) == 2
    assert all(set(slot) <= {"id", "text", "loc"} for slot in result["slots"])
    assert [slot["id"] for slot in result["slots"]] == [f"s{i}" for i in range(1, len(result["slots"]) + 1)]
    assert all(not slot["id"].startswith("slot_") for slot in result["slots"])
    assert client.search_calls[0]["limit"] == 3
    assert all(limit is None for limit in client.get_email_limits)



def test_context_reply_history_records_reply_all_mode(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path, StatefulWeeklyClient())
    context = workflow.get_weekly_report_context(user_input="更新")
    state = workflow.store.get_action_session(context["resume_token"])["state"]
    assert state["draft_mode"] == "reply_all"
    assert state["history_boundary_keyword"] == "From"


def test_context_no_history_records_compose_and_copies_addresses(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path, StatefulWeeklyClient(include_history=False))
    context = workflow.get_weekly_report_context(user_input="更新")
    state = workflow.store.get_action_session(context["resume_token"])["state"]
    assert state["draft_mode"] == "compose"
    assert state["source_to"] == ["team@company.com", "owner@company.com"]
    assert state["source_cc"] == ["manager@company.com"]
    assert state["source_sent_at"] == "2026-08-03T01:00:00Z"


def test_context_compose_requires_original_to(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(include_history=False)
    client.messages["WK3"]["to"] = []
    with pytest.raises(ValueError, match="To 收件人"):
        _workflow(tmp_path, client).get_weekly_report_context(user_input="更新")


def test_text_only_html_validation_accepts_text_changes_and_preserves_structure() -> None:
    template = '<table width="760"><tr><td>旧</td><td>保持</td></tr></table>'
    candidate = '<table width="760"><tr><td>新</td><td>保持</td></tr></table>'
    result = validate_text_only_html_update(template, candidate)
    assert result.changed_text_slots == 1
    assert result.structure_sha256 == html_structure_sha256(template)


def test_text_only_html_validation_rejects_tag_attribute_and_markdown_changes() -> None:
    with pytest.raises(ValueError, match="标签或注释"):
        validate_text_only_html_update('<p class="a">旧</p>', '<p class="b">新</p>')
    with pytest.raises(ValueError, match="代码围栏"):
        validate_text_only_html_update('<p>旧</p>', '```html\n<p>新</p>\n```')


def test_editable_slots_apply_plain_text_and_escape_html() -> None:
    template = '<table><tr><td>旧</td><td>保持</td></tr></table>'
    slots = extract_editable_text_slots(template)
    first = next(slot for slot in slots if slot.text == "旧")
    result = apply_editable_text_slot_changes(
        template, [{"slot_id": first.slot_id, "new_text": "<b>新</b>"}]
    )
    assert '&lt;b&gt;新&lt;/b&gt;' in result.html
    assert '<b>新</b>' not in result.html
    assert result.html_validation.changed_text_slots == 1


def test_slot_update_rejects_unknown_and_legacy_fields() -> None:
    template = '<p>旧</p>'
    slot = extract_editable_text_slots(template)[0]
    with pytest.raises(ValueError, match="不属于当前模板"):
        apply_editable_text_slot_changes(template, [{"slot_id": "missing", "new_text": "新"}])
    with pytest.raises(ValueError, match="不支持的字段"):
        apply_editable_text_slot_changes(
            template, [{"slot_id": slot.slot_id, "new_text": "新", "expected_text": "旧"}]
        )


def test_continue_action_creates_reply_all_once_and_never_updates_body(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="日期改到下周，完成测试")
    slots = {item["text"]: item for item in context["slots"]}
    result = workflow.continue_action(
        resume_token=context["resume_token"],
        selections={
            "changes": [
                {"id": slots["日期：2026-07-27 至 2026-08-02"]["id"], "text": "日期：2026-08-03 至 2026-08-09"},
                {"id": slots["完成旧任务"]["id"], "text": "完成旧任务并通过测试"},
            ],
            "subject": "项目周报 2026-08-03 至 2026-08-09",
        },
    )
    assert result["status"] == "draft_created"
    assert result["weekly_flow_status"] == "completed"
    assert result["draft_mode"] == "reply_all"
    assert result["reply_all"] is True
    assert len(client.reply_calls) == 1
    assert client.create_calls == []
    assert "完成旧任务并通过测试" in client.reply_calls[0]["body_html"]
    assert "quoted-history" not in client.reply_calls[0]["body_html"]
    assert len(client.update_calls) == 1
    assert client.update_calls[0]["subject"] == "项目周报 2026-08-03 至 2026-08-09"
    assert client.update_calls[0].get("body_html") is None
    assert client.attachment_reads == ["ATT1"]
    assert client.attachment_writes[0]["content_id"] == "image001.png"


def test_continue_action_without_history_creates_new_draft_with_copied_to_cc(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(include_history=False)
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="完成测试")
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")
    result = workflow.continue_action(
        resume_token=context["resume_token"],
        selections={
            "changes": [{"id": target["id"], "text": "完成测试"}],
            "subject": "项目周报 2026-08-03 至 2026-08-09",
        },
    )
    assert result["draft_mode"] == "compose"
    assert result["reply_all"] is False
    assert client.reply_calls == []
    assert len(client.create_calls) == 1
    call = client.create_calls[0]
    assert call["to"] == ["team@company.com", "owner@company.com"]
    assert call["cc"] == ["manager@company.com"]
    assert call["subject"] == "项目周报 2026-08-03 至 2026-08-09"
    assert "完成测试" in call["body_html"]
    # Compose mode sets Subject during creation, so no second subject UpdateItem.
    assert client.update_calls == []


def test_reply_all_without_period_preserves_exchange_native_subject(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(include_history=True, latest_subject="项目周报")
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="完成测试")
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")
    workflow.continue_action(
        resume_token=context["resume_token"],
        selections={"changes": [{"id": target["id"], "text": "完成测试"}]},
    )
    assert len(client.reply_calls) == 1
    assert client.update_calls == []
    assert client.draft_subject == "RE: 项目周报"


def test_compose_mode_preserves_original_subject_when_agent_omits_subject(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(include_history=False, latest_subject="项目周报")
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="完成测试")
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")
    workflow.continue_action(
        resume_token=context["resume_token"],
        selections={"changes": [{"id": target["id"], "text": "完成测试"}]},
    )
    assert client.create_calls[0]["subject"] == "项目周报"


def test_subject_period_marker_is_rolled_forward_server_side(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(include_history=False)
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="完成测试")
    assert context["subject"] == "项目周报 2026-08-03 至 2026-08-09"
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")
    result = workflow.continue_action(
        resume_token=context["resume_token"],
        selections={"changes": [{"id": target["id"], "text": "完成测试"}]},
    )
    assert result["status"] == "draft_created"
    assert client.create_calls[0]["subject"] == "项目周报 2026-08-03 至 2026-08-09"


def test_subject_explicit_old_period_is_rejected_without_consuming_token(tmp_path: Path) -> None:
    client = StatefulWeeklyClient(include_history=False)
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="完成测试")
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")
    with pytest.raises(ValueError, match="不能显式恢复为上一周主题"):
        workflow.continue_action(
            resume_token=context["resume_token"],
            selections={
                "changes": [{"id": target["id"], "text": "完成测试"}],
                "subject": "项目周报 2026-07-27 至 2026-08-02",
            },
        )
    assert workflow.store.get_action_session(context["resume_token"])["status"] == "context_ready"


def test_unknown_slot_id_does_not_consume_weekly_token_and_can_retry(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="完成测试")
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")

    with pytest.raises(ValueError, match="slot id 不属于当前周报上下文"):
        workflow.continue_action(
            resume_token=context["resume_token"],
            selections={
                "changes": [{"id": "s999", "text": "完成测试"}],
                "subject": "项目周报 2026-08-03 至 2026-08-09",
            },
        )

    # Agent payload validation is read-only. The same token remains bound to
    # the same hidden HTML/context and may be corrected without weekly_report.
    assert workflow.store.get_action_session(context["resume_token"])["status"] == "context_ready"
    assert client.reply_calls == []
    assert client.create_calls == []

    result = workflow.continue_action(
        resume_token=context["resume_token"],
        selections={
            "changes": [{"id": target["id"], "text": "完成测试"}],
            "subject": "项目周报 2026-08-03 至 2026-08-09",
        },
    )
    assert result["status"] == "draft_created"
    assert workflow.store.get_action_session(context["resume_token"])["status"] == "completed"
    assert len(client.reply_calls) == 1


def test_weekly_continue_action_rejects_unknown_selection_fields_before_write(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新")
    with pytest.raises(ValueError, match="只支持 changes 和 subject"):
        workflow.continue_action(
            resume_token=context["resume_token"],
            selections={"changes": [], "html": "<p>bad</p>"},
        )
    assert client.reply_calls == []
    assert client.create_calls == []


def test_update_rejects_stale_source_before_creating_draft(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新")
    target = next(item for item in context["slots"] if item["text"] == "完成旧任务")
    client.messages["WK3"]["body_html"] = client.messages["WK3"]["body_html"].replace("WK2_QUOTED", "CHANGED")
    result = workflow.continue_action(
        resume_token=context["resume_token"],
        selections={
            "changes": [{"id": target["id"], "text": "新任务"}],
            "subject": "项目周报 2026-08-03 至 2026-08-09",
        },
    )
    assert result["status"] == "context_stale"
    assert client.reply_calls == [] and client.create_calls == []


def test_update_rejects_when_newer_weekly_report_appears(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    context = workflow.get_weekly_report_context(user_input="更新")
    original = client.search_emails_multi_folder

    def newer(**kwargs):
        if int(kwargs.get("limit") or 0) == 1 and not kwargs.get("participant_contains"):
            return {"returned": 1, "items": [{"item_id": "WK4", "change_key": "X", "subject": "项目周报"}]}
        return original(**kwargs)

    client.search_emails_multi_folder = newer  # type: ignore[method-assign]
    result = workflow.continue_action(
        resume_token=context["resume_token"],
        selections={"changes": [], "subject": "项目周报"},
    )
    assert result["status"] == "context_stale"
    assert client.reply_calls == []


def test_weekly_token_is_one_shot_and_new_context_supersedes_old(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    workflow = _workflow(tmp_path, client)
    first = workflow.get_weekly_report_context(user_input="第一次")
    second = workflow.get_weekly_report_context(user_input="第二次")
    assert workflow.store.get_action_session(first["resume_token"])["status"] == "superseded"
    target = next(item for item in second["slots"] if item["text"] == "完成旧任务")
    done = workflow.continue_action(
        resume_token=second["resume_token"],
        selections={
            "changes": [{"id": target["id"], "text": "完成新任务"}],
            "subject": "项目周报 2026-08-03 至 2026-08-09",
        },
    )
    assert done["weekly_flow_status"] == "completed"
    with pytest.raises(ValueError, match="已使用"):
        workflow.continue_action(
            resume_token=second["resume_token"], selections={"changes": [], "subject": "再次"}
        )


def test_context_max_reports_is_three(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_reports"):
        _workflow(tmp_path, StatefulWeeklyClient()).get_weekly_report_context(user_input="更新", max_reports=4)


def test_context_supports_non_table_latest_layout(tmp_path: Path) -> None:
    body = _reply_body('<h2>项目A</h2><p>完成接口联调。</p><ul><li>准备性能测试</li></ul>')
    result = _workflow(tmp_path, StatefulWeeklyClient(latest_body=body)).get_weekly_report_context(
        user_input="项目A联调完成，下周做性能测试"
    )
    slots = {item["text"]: item for item in result["slots"]}
    assert "项目A" in slots
    assert "完成接口联调。" in slots
    assert "准备性能测试" in slots
    assert slots["完成接口联调。"]["loc"] is not None


def test_context_rejects_non_html_latest_message(tmp_path: Path) -> None:
    client = StatefulWeeklyClient()
    client.messages["WK3"]["body_type"] = "Text"
    with pytest.raises(ValueError, match="只接受 HTML"):
        _workflow(tmp_path, client).get_weekly_report_context(user_input="更新")
