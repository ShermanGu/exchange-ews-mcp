from __future__ import annotations

from pathlib import Path

import pytest

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import AttachmentContent, AttachmentResult, DraftResult
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.workflow import (
    SemanticMailWorkflow,
    TEMPLATE_CONTENT_END,
    TEMPLATE_CONTENT_START,
    _latest_message_template_html,
)


def app_config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company.com"],
    )


class TemplateClient:
    def __init__(
        self,
        *,
        multiple: bool = False,
        no_history: bool = False,
        truncated: bool = False,
        reject_attachment: bool = False,
    ) -> None:
        self.multiple = multiple
        self.no_history = no_history
        self.truncated = truncated
        self.reject_attachment = reject_attachment
        self.created: list[dict] = []
        self.replied: list[dict] = []
        self.attached: list[dict] = []

    def search_emails_multi_folder(self, **kwargs):
        items = [
            {
                "item_id": "M1",
                "change_key": "K1",
                "subject": "上周项目周报",
                "folder": "sentitems",
                "sent_at": "2026-07-24T01:00:00Z",
            }
        ]
        if self.multiple:
            items.append(
                {
                    "item_id": "M2",
                    "change_key": "K2",
                    "subject": "上周研发周报",
                    "folder": "sentitems",
                    "sent_at": "2026-07-25T01:00:00Z",
                }
            )
        return {"returned": len(items), "items": items, "per_folder": []}

    def resolve_names(self, **kwargs):
        query = kwargs["query"]
        return {"items": [{"display_name": query, "email": query, "routing_type": "SMTP"}]}

    def get_email(self, *, item_id, **kwargs):
        if self.no_history:
            body = (
                "<html><head><style>.report{font-family:Arial}</style></head>"
                "<body class='report'><table><tr><td>唯一一封完整内容</td></tr></table>"
                "<img src='cid:logo-1'></body></html>"
            )
        else:
            body = (
                "<html><head><style>.report{font-family:Arial}</style></head>"
                "<body class='report'><div style='font-family:Arial;font-size:11pt'>"
                "<p>最上方第一封</p><img src='cid:logo-1'>"
                "<div id='divRplyFwdMsg'><b>From:</b> old@company.com</div>"
                "<p>第二封历史</p></div></body></html>"
            )
        return {
            "item_id": item_id,
            "change_key": "K1",
            "subject": "上周项目周报",
            "folder": "sentitems",
            "sent_at": "2026-07-24T01:00:00Z",
            "body_html": body,
            "body_truncated": self.truncated,
            "to": [
                {"name": "A", "email": "a@company.com"},
                {"name": "Duplicate", "email": "A@company.com"},
            ],
            "cc": [{"name": "B", "email": "b@company.com"}],
            "bcc": [{"name": "Secret", "email": "secret@company.com"}],
            "attachments": [
                {
                    "type": "FileAttachment",
                    "attachment_id": "INLINE1",
                    "name": "logo.png",
                    "content_type": "image/png",
                    "size": 4,
                    "is_inline": True,
                    "content_id": "logo-1",
                },
                {
                    "type": "FileAttachment",
                    "attachment_id": "FILE1",
                    "name": "old.xlsx",
                    "content_type": "application/vnd.ms-excel",
                    "size": 5,
                    "is_inline": False,
                    "content_id": None,
                },
            ],
        }

    def get_attachments(self, *, attachment_ids):
        values = {
            "INLINE1": AttachmentContent(
                attachment_id="INLINE1",
                attachment_type="FileAttachment",
                filename="logo.png",
                content_type="image/png",
                size=4,
                is_inline=True,
                content_id="logo-1",
                content=b"logo",
            ),
            "FILE1": AttachmentContent(
                attachment_id="FILE1",
                attachment_type="FileAttachment",
                filename="old.xlsx",
                content_type="application/vnd.ms-excel",
                size=5,
                is_inline=False,
                content_id=None,
                content=b"excel",
            ),
        }
        return [values[item] for item in attachment_ids]

    def validate_attachment_content(self, **kwargs):
        if self.reject_attachment:
            raise ValueError("template attachment rejected")
        return kwargs

    def create_draft(self, **kwargs):
        self.created.append(kwargs)
        return DraftResult(
            item_id="D1",
            change_key="DK1",
            subject=kwargs["subject"],
            to=kwargs["to"],
            cc=kwargs["cc"],
            bcc=kwargs["bcc"],
        )

    def reply_as_draft(self, **kwargs):
        self.replied.append(kwargs)
        return DraftResult(
            item_id="R1",
            change_key="RK1",
            subject="Re: target",
            to=["sender@company.com"],
            cc=[],
            bcc=[],
            draft_type="reply_all" if kwargs["reply_all"] else "reply",
        )

    def add_attachment_content_to_draft(self, **kwargs):
        self.attached.append(kwargs)
        root = "R1" if self.replied else "D1"
        index = len(self.attached)
        return AttachmentResult(
            attachment_id=f"NEW{index}",
            root_item_id=root,
            root_item_change_key=f"KNEW{index}",
            filename=kwargs["filename"],
            size=len(kwargs["content"]),
            content_type=kwargs["content_type"],
        )


def workflow(tmp_path: Path, client: TemplateClient) -> SemanticMailWorkflow:
    return SemanticMailWorkflow(client, ReferenceStore(tmp_path / "state.db"), app_config())  # type: ignore[arg-type]


def extract(wf: SemanticMailWorkflow) -> dict:
    result = wf.extract_email_template(subject_contains="周报", folders=["Sent Items"])
    assert result["status"] == "template_extracted"
    return result


def test_extract_reply_chain_keeps_only_top_first_message(tmp_path: Path) -> None:
    result = extract(workflow(tmp_path, TemplateClient()))
    assert "最上方第一封" in result["template_html"]
    assert "第二封历史" not in result["template_html"]
    assert "divRplyFwdMsg" not in result["template_html"]
    assert result["quoted_history_excluded"] is True
    assert result["history_boundary_strategy"] == "outlook_reply_forward_div"
    assert result["suggested_compose_inputs"]["to_queries"] == ["a@company.com"]
    assert result["suggested_compose_inputs"]["cc_queries"] == ["b@company.com"]
    assert result["template_ref"].startswith("tmpl_")
    assert TEMPLATE_CONTENT_START in result["template_shell_html"]
    assert TEMPLATE_CONTENT_END in result["template_shell_html"]


def test_extract_single_message_keeps_complete_message(tmp_path: Path) -> None:
    result = extract(workflow(tmp_path, TemplateClient(no_history=True)))
    assert "唯一一封完整内容" in result["template_html"]
    assert "<table>" in result["template_html"]
    assert result["quoted_history_excluded"] is False
    assert result["history_boundary_strategy"] == "latest_segment_no_history_boundary"


def test_extract_multiple_candidates_requires_confirmation_and_resume(tmp_path: Path) -> None:
    client = TemplateClient(multiple=True)
    store = ReferenceStore(tmp_path / "state.db")
    wf = SemanticMailWorkflow(client, store, app_config())  # type: ignore[arg-type]
    pending = wf.extract_email_template(subject_contains="周报")
    assert pending["status"] == "needs_confirmation"
    selected = pending["message_resolution"]["items"][1]["message_ref"]
    result = wf.continue_action(
        resume_token=pending["resume_token"], selections={"message_ref": selected}
    )
    assert result["status"] == "template_extracted"
    assert result["source_message"]["message_ref"] == selected


def test_extract_candidate_selection_rejects_outsider(tmp_path: Path) -> None:
    client = TemplateClient(multiple=True)
    store = ReferenceStore(tmp_path / "state.db")
    wf = SemanticMailWorkflow(client, store, app_config())  # type: ignore[arg-type]
    pending = wf.extract_email_template(subject_contains="周报")
    outsider = store.upsert_reference(
        kind="message", external_key="OUT", payload={"item_id": "OUT", "change_key": "KO"}
    )
    with pytest.raises(ValueError, match="不属于本次"):
        wf.continue_action(
            resume_token=pending["resume_token"], selections={"message_ref": outsider}
        )


def test_compose_email_treats_body_as_new_content_with_template_ref(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    template = extract(wf)
    new_content_html = "<h2>本周新内容</h2><img src='cid:logo-1'>"
    result = wf.compose_email(
        to_queries=template["suggested_compose_inputs"]["to_queries"],
        cc_queries=template["suggested_compose_inputs"]["cc_queries"],
        subject="本周项目周报",
        body_html=new_content_html,
        template_ref=template["template_ref"],
    )
    assert result["status"] == "draft_created"
    assert "本周新内容" in client.created[0]["body_html"]
    assert "第二封历史" not in client.created[0]["body_html"]
    assert result["template_render_strategy"] == "explicit_content_markers"
    assert client.created[0]["to"] == ["a@company.com"]
    assert client.created[0]["cc"] == ["b@company.com"]
    assert [item["filename"] for item in client.attached] == ["logo.png"]
    assert result["template_inline_images_copied"] == 1
    assert result["template_attachments_copied"] == 0


def test_compose_email_can_copy_normal_template_attachments_explicitly(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    template = extract(wf)
    result = wf.compose_email(
        to_queries=["a@company.com"],
        subject="new",
        body_html="<p>new</p><img src='cid:logo-1'>",
        template_ref=template["template_ref"],
        copy_template_attachments=True,
    )
    assert [item["filename"] for item in client.attached] == ["logo.png", "old.xlsx"]
    assert result["template_attachments_copied"] == 1


def test_unreferenced_inline_image_is_not_copied(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    template = extract(wf)
    result = wf.compose_email(
        to_queries=["a@company.com"],
        subject="new",
        body_html="<p>new without image</p>",
        template_ref=template["template_ref"],
    )
    assert client.attached == []
    assert result["template_inline_images_copied"] == 0


def test_template_attachment_preflight_fails_before_draft_creation(tmp_path: Path) -> None:
    client = TemplateClient(reject_attachment=True)
    wf = workflow(tmp_path, client)
    template = extract(wf)
    with pytest.raises(ValueError, match="template attachment rejected"):
        wf.compose_email(
            to_queries=["a@company.com"],
            subject="new",
            body_html="<img src='cid:logo-1'>",
            template_ref=template["template_ref"],
        )
    assert client.created == []


def test_reply_target_and_template_source_are_independent(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    template = extract(wf)
    target_ref = wf.store.upsert_reference(
        kind="message",
        external_key="TARGET",
        payload={"item_id": "TARGET", "change_key": "TK"},
    )
    new_content_html = "<p>回复新内容</p><img src='cid:logo-1'>"
    result = wf.reply_to_email(
        message_ref=target_ref,
        body_html=new_content_html,
        reply_all=True,
        template_ref=template["template_ref"],
    )
    assert client.replied[0]["item_id"] == "TARGET"
    assert "回复新内容" in client.replied[0]["body_html"]
    assert "第二封历史" not in client.replied[0]["body_html"]
    assert client.replied[0]["reply_all"] is True
    assert result["template_render_strategy"] == "explicit_content_markers"
    assert result["template_source"]["message_ref"] == template["source_message"]["message_ref"]
    assert [item["filename"] for item in client.attached] == ["logo.png"]
    assert result["sent"] is False


def test_long_truncated_chain_still_extracts_first_message() -> None:
    html = (
        "<html><body><p>first</p><div id='divRplyFwdMsg'>old</div>" + "x" * 1000
    )
    latest, strategy, removed = _latest_message_template_html(html, source_truncated=True)
    assert removed is True
    assert strategy == "outlook_reply_forward_div"
    assert "first" in latest
    assert "old" not in latest


def test_truncated_single_message_is_balanced_and_warned(tmp_path: Path) -> None:
    client = TemplateClient(no_history=True, truncated=True)
    result = extract(workflow(tmp_path, client))
    assert result["source_body_truncated"] is True
    assert result["quoted_history_excluded"] is False
    assert result["warnings"]


def test_extract_defaults_to_inbox_and_sentitems(tmp_path: Path) -> None:
    class FolderClient(TemplateClient):
        def search_emails_multi_folder(self, **kwargs):
            assert kwargs["folders"] == ["inbox", "sentitems"]
            return super().search_emails_multi_folder(**kwargs)

    result = workflow(tmp_path, FolderClient()).extract_email_template(subject_contains="周报")
    assert result["status"] == "template_extracted"


def test_template_shell_preserves_head_body_and_outer_style(tmp_path: Path) -> None:
    result = extract(workflow(tmp_path, TemplateClient()))
    shell = result["template_shell_html"]
    assert ".report{font-family:Arial}" in shell
    assert "<body class='report'>" in shell
    assert "font-size:11pt" in shell
    assert "第二封历史" not in shell


def test_template_never_suggests_bcc_copy(tmp_path: Path) -> None:
    result = extract(workflow(tmp_path, TemplateClient()))
    suggested = result["suggested_compose_inputs"]
    assert set(suggested) == {"to_queries", "cc_queries", "subject"}
    assert "secret@company.com" not in str(result)


def test_copy_template_inline_images_can_be_disabled(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    template = extract(wf)
    result = wf.compose_email(
        to_queries=["a@company.com"],
        subject="new",
        body_html="<img src='cid:logo-1'>",
        template_ref=template["template_ref"],
        copy_template_inline_images=False,
    )
    assert client.attached == []
    assert result["template_inline_images_copied"] == 0


def test_non_template_reference_is_rejected_before_draft(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    message_ref = wf.store.upsert_reference(
        kind="message", external_key="M-X", payload={"item_id": "M-X"}
    )
    with pytest.raises(ValueError, match="不是 template"):
        wf.compose_email(
            to_queries=["a@company.com"],
            subject="new",
            body_html="<p>x</p>",
            template_ref=message_ref,
        )
    assert client.created == []


def test_reply_without_template_ref_keeps_original_behavior(tmp_path: Path) -> None:
    client = TemplateClient()
    wf = workflow(tmp_path, client)
    target_ref = wf.store.upsert_reference(
        kind="message", external_key="TARGET-PLAIN", payload={"item_id": "TARGET-PLAIN", "change_key": "K"}
    )
    result = wf.reply_to_email(
        message_ref=target_ref,
        body_html="<p>plain reply</p>",
        reply_all=False,
    )
    assert result["status"] == "draft_created"
    assert result["template_ref"] is None
    assert client.attached == []
    assert client.replied[0]["body_html"] == "<p>plain reply</p>"


@pytest.mark.parametrize(
    ("html", "expected_strategy"),
    [
        ("<p>new</p><blockquote type='cite'><p>old</p></blockquote>", "html_cite_blockquote"),
        ("<p>new</p><div class='gmail_quote'><p>old</p></div>", "gmail_quote"),
        ("<p>new</p>-----Original Message-----<p>old</p>", "original_message_separator"),
    ],
)
def test_extract_supports_multiple_history_boundary_styles(html: str, expected_strategy: str) -> None:
    latest, strategy, removed = _latest_message_template_html(html)
    assert removed is True
    assert strategy == expected_strategy
    assert "old" not in latest


def test_empty_source_html_is_rejected(tmp_path: Path) -> None:
    class EmptyClient(TemplateClient):
        def get_email(self, **kwargs):
            result = super().get_email(**kwargs)
            result["body_html"] = ""
            return result

    with pytest.raises(ValueError, match="没有 HTML 正文"):
        workflow(tmp_path, EmptyClient()).extract_email_template(subject_contains="周报")

class UniqueBodyClient(TemplateClient):
    def __init__(self, *, unique_html: str, full_html: str, body_truncated: bool = False, unique_truncated: bool = False):
        super().__init__()
        self.unique_html = unique_html
        self.full_html = full_html
        self.body_truncated_flag = body_truncated
        self.unique_truncated_flag = unique_truncated

    def get_email(self, *, item_id, **kwargs):
        result = super().get_email(item_id=item_id, **kwargs)
        result["body_html"] = self.full_html
        result["body_truncated"] = self.body_truncated_flag
        result["body_server_truncated"] = False
        result["body_local_truncated"] = self.body_truncated_flag
        result["unique_body_html"] = self.unique_html
        result["unique_body_type"] = "HTML"
        result["unique_body_truncated"] = self.unique_truncated_flag
        return result


def test_extract_prefers_exchange_unique_body_before_local_truncation(tmp_path: Path) -> None:
    # Reproduces the real failure: the full Body is already a long/truncated prefix
    # and contains no usable quote boundary in the returned range. UniqueBody is
    # nevertheless the authoritative current-message segment.
    full = "<html><head><style>.x{color:red}</style></head><body>" + ("X" * 5000) + "</body></html>"
    unique = "<div class='x'><p>当前第一封回复</p><p>签名</p></div>"
    result = extract(workflow(tmp_path, UniqueBodyClient(
        unique_html=unique,
        full_html=full,
        body_truncated=True,
    )))
    assert result["history_boundary_strategy"] == "ews_unique_body"
    assert result["unique_body_available"] is True
    assert "当前第一封回复" in (result["template_html"] or result["template_preview_html"])
    assert "X" * 100 not in (result["template_html"] or result["template_preview_html"])


def test_exchange_unique_body_single_message_is_not_reported_as_history(tmp_path: Path) -> None:
    body = "<html><body><div><p>唯一一封邮件</p></div></body></html>"
    unique = "<div><p>唯一一封邮件</p></div>"
    result = extract(workflow(tmp_path, UniqueBodyClient(unique_html=unique, full_html=body)))
    assert result["history_boundary_strategy"] == "ews_unique_body"
    assert result["quoted_history_excluded"] is False
    assert "唯一一封邮件" in (result["template_html"] or "")


def test_large_template_response_is_compact_but_full_template_stays_in_ref(tmp_path: Path) -> None:
    unique = "<div style='font-family:Arial'>" + ("A" * 8000) + "</div>"
    full = f"<html><head><style>.x{{}}</style></head><body>{unique}</body></html>"
    wf = workflow(tmp_path, UniqueBodyClient(unique_html=unique, full_html=full))
    result = extract(wf)
    assert result["template_html"] is None
    assert result["template_html_preview_truncated"] is True
    assert len(result["template_preview_html"]) == 2800
    assert result["template_html_chars"] > 8000
    stored = wf.store.get_reference(result["template_ref"], expected_kind="template")
    assert len(stored.payload["template_html"]) == result["template_html_chars"]


def test_compose_email_can_render_template_server_side(tmp_path: Path) -> None:
    client = TemplateClient(no_history=True)
    wf = workflow(tmp_path, client)
    template = extract(wf)
    result = wf.compose_email(
        to_queries=["a@company.com"],
        subject="server-side render",
        template_ref=template["template_ref"],
        body_html="<h2>新的正文</h2>",
    )
    assert result["status"] == "draft_created"
    assert "新的正文" in client.created[0]["body_html"]
    assert "唯一一封完整内容" not in client.created[0]["body_html"]
    assert result["template_render_strategy"] == "explicit_content_markers"


def test_reply_email_can_render_template_server_side(tmp_path: Path) -> None:
    client = TemplateClient(no_history=True)
    wf = workflow(tmp_path, client)
    template = extract(wf)
    target_ref = wf.store.upsert_reference(
        kind="message", external_key="TARGET2", payload={"item_id": "TARGET2", "change_key": "KT"}
    )
    result = wf.reply_to_email(
        message_ref=target_ref,
        reply_all=True,
        template_ref=template["template_ref"],
        body_html="<p>新的回复正文</p>",
    )
    assert result["status"] == "draft_created"
    assert "新的回复正文" in client.replied[0]["body_html"]
    assert "唯一一封完整内容" not in client.replied[0]["body_html"]
    assert result["template_render_strategy"] == "explicit_content_markers"


def test_fallback_detects_classic_outlook_header_block() -> None:
    html = """
    <html><body><div class='WordSection1'><p>当前回复</p>
      <div style='border:none;border-top:solid #E1E1E1 1.0pt;padding:3pt 0 0 0'>
        <p class='MsoNormal'><b>From:</b> old@company.com<br>
        <b>Sent:</b> Monday<br><b>To:</b> user@company.com<br>
        <b>Subject:</b> Old subject</p>
      </div>
      <p>历史正文</p>
    </div></body></html>
    """
    latest, strategy, removed = _latest_message_template_html(html)
    assert removed is True
    assert strategy == "outlook_classic_header_block"
    assert "当前回复" in latest
    assert "历史正文" not in latest
