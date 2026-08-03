from __future__ import annotations

from pathlib import Path

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.dt_config import DtTestConfig
from exchange_ews_mcp.dt_runner import run_template_v06_integration_tests
from exchange_ews_mcp.ews import AttachmentContent, AttachmentResult, DraftResult
from exchange_ews_mcp.state_store import ReferenceStore


class DtTemplateClient:
    def resolve_names(self, **kwargs):
        query = kwargs["query"]
        return {"items": [{"display_name": query, "email": query, "routing_type": "SMTP"}]}

    def search_emails_multi_folder(self, **kwargs):
        return {
            "returned": 1,
            "items": [{
                "item_id": "M1", "change_key": "K1", "subject": "周报",
                "folder": "sentitems", "sent_at": "2026-07-20T00:00:00Z",
            }],
            "per_folder": [],
        }

    def get_email(self, **kwargs):
        return {
            "item_id": "M1", "change_key": "K1", "subject": "周报",
            "sent_at": "2026-07-20T00:00:00Z", "body_type": "HTML",
            "body_html": "<html><body><p>x</p><img src='cid:c1'></body></html>",
            "body_truncated": False,
            "to": [{"email": "a@company.com"}], "cc": [{"email": "b@company.com"}],
            "attachments": [{
                "type": "FileAttachment", "attachment_id": "A1", "name": "logo.png",
                "content_type": "image/png", "is_inline": True, "content_id": "c1",
            }],
        }

    def get_attachments(self, *, attachment_ids):
        return [AttachmentContent(
            attachment_id="A1", attachment_type="FileAttachment", filename="logo.png",
            content_type="image/png", size=1, is_inline=True, content_id="c1", content=b"x",
        )]

    def validate_attachment_content(self, **kwargs):
        return kwargs

    def create_draft(self, **kwargs):
        return DraftResult(
            item_id="D1", change_key="D1K", subject=kwargs["subject"],
            to=kwargs["to"], cc=kwargs["cc"], bcc=kwargs["bcc"],
        )

    def reply_as_draft(self, **kwargs):
        return DraftResult(
            item_id="R1", change_key="R1K", subject="Re: 周报",
            to=["self@company.com"], cc=[], bcc=[], draft_type="reply",
        )

    def add_attachment_content_to_draft(self, **kwargs):
        return AttachmentResult(
            attachment_id="NEW1", root_item_id="D1", root_item_change_key="D2K",
            filename=kwargs["filename"], size=len(kwargs["content"]),
            content_type=kwargs["content_type"],
        )


def profile() -> DtTestConfig:
    return DtTestConfig(
        person_queries=["xiaoming"], senders=["sender@company.com"],
        draft_recipient="self@company.com",
    )


def app_config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail/EWS/Exchange.asmx", username="D\\u",
        primary_email="self@company.com", company_email_domains=["company.com"],
    )


def test_template_v06_dt_read_only(tmp_path: Path) -> None:
    result = run_template_v06_integration_tests(
        DtTemplateClient(), profile(), app_config=app_config(), read_only=True,
        store=ReferenceStore(tmp_path / "s.db"), stamp="STAMP",
    )
    assert result["summary"]["status"] == "PASS"
    assert result["steps"][-1]["status"] == "SKIP"
    assert result["steps"][-2]["status"] == "SKIP"


def test_template_v06_dt_full_creates_unsent_clone(tmp_path: Path) -> None:
    result = run_template_v06_integration_tests(
        DtTemplateClient(), profile(), app_config=app_config(), read_only=False,
        store=ReferenceStore(tmp_path / "s.db"), stamp="STAMP",
    )
    assert result["summary"]["status"] == "PASS"
    assert result["steps"][-1]["details"]["sent"] is False
    assert result["steps"][-2]["details"]["inline_images_copied"] == 1
    assert result["steps"][-1]["details"]["sent"] is False
    assert result["created_drafts"][0]["draft_type"] == "compose_email_template"
    assert result["created_drafts"][1]["draft_type"] == "reply_to_email_template"
