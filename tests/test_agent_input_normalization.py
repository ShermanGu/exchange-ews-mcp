from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import pytest

from exchange_ews_mcp import cli, server, service
from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import DraftResult
from exchange_ews_mcp.input_normalization import (
    normalize_attendee_type,
    normalize_importance,
    normalize_mail_folder,
    normalize_mail_folders,
    normalize_template_mode,
)
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.workflow import SemanticMailWorkflow, _render_template_body


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("inbox", "inbox"),
        ("Inbox", "inbox"),
        ("收件箱", "inbox"),
        ("Drafts", "drafts"),
        ("Sent Items", "sentitems"),
        ("sent-items", "sentitems"),
        ("Sent Mail", "sentitems"),
        ("已发送邮件", "sentitems"),
        ("Deleted Items", "deleteditems"),
        ("Trash", "deleteditems"),
        ("Junk Email", "junkemail"),
        ("Spam", "junkemail"),
        ("Outbox", "outbox"),
        ("发件箱", "outbox"),
    ],
)
def test_mail_folder_display_names_and_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_mail_folder(raw) == expected


def test_mail_folder_list_is_normalized_and_deduplicated() -> None:
    assert normalize_mail_folders(["Sent Items", "sentitems", "Inbox"]) == [
        "sentitems",
        "inbox",
    ]


def test_invalid_folder_error_lists_canonical_values_and_friendly_example() -> None:
    with pytest.raises(ValueError) as exc_info:
        normalize_mail_folder("archive")
    message = str(exc_info.value)
    assert "sentitems" in message
    assert "Sent Items" in message


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Clone", "clone"),
        ("exact-copy", "clone"),
        ("Replace Content", "replace_content"),
        ("replace-content", "replace_content"),
        ("Rendered HTML", "rendered_html"),
        ("rendered-html", "rendered_html"),
    ],
)
def test_template_mode_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_template_mode(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("organizer", "Organizer"),
        ("Required Attendee", "Required"),
        ("optional-attendee", "Optional"),
        ("Meeting Room", "Room"),
        ("equipment", "Resource"),
    ],
)
def test_attendee_type_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_attendee_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("low", "Low"), ("Low Importance", "Low"), ("NORMAL", "Normal"), ("medium", "Normal"), ("high", "High"), ("High Importance", "High")],
)
def test_importance_aliases_are_normalized(raw: str, expected: str) -> None:
    assert normalize_importance(raw) == expected


class SearchClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search_emails(self, **kwargs):
        self.calls.append(kwargs)
        return {"folder": kwargs["folder"], "items": [], "returned": 0}

    def search_emails_multi_folder(self, **kwargs):
        self.calls.append(kwargs)
        return {"folders": kwargs["folders"], "items": [], "returned": 0}


def test_search_emails_service_converts_outlook_display_name_before_client(monkeypatch, tmp_path: Path) -> None:
    client = SearchClient()
    monkeypatch.setattr(service, "configured_client", lambda: client)
    monkeypatch.setattr(service, "configured_store", lambda: ReferenceStore(tmp_path / "state.db"))
    result = service.search_emails(folder="Sent Items")
    assert client.calls[0]["folder"] == "sentitems"
    assert result["folder"] == "sentitems"


def test_multi_folder_service_converts_and_deduplicates_display_names(monkeypatch, tmp_path: Path) -> None:
    client = SearchClient()
    monkeypatch.setattr(service, "configured_client", lambda: client)
    monkeypatch.setattr(service, "configured_store", lambda: ReferenceStore(tmp_path / "state.db"))
    result = service.search_emails(folders=["Sent Items", "sentitems", "Inbox"])
    assert client.calls[0]["folders"] == ["sentitems", "inbox"]
    assert result["folders"] == ["sentitems", "inbox"]


class AvailabilityClient:
    def __init__(self) -> None:
        self.attendees = None

    def get_user_availability(self, **kwargs):
        self.attendees = kwargs["attendees"]
        return {
            "status": "success",
            "start": kwargs["start"],
            "end": kwargs["end"],
            "attendees": [],
        }


def test_availability_service_normalizes_role_alias_and_field_alias(monkeypatch, tmp_path: Path) -> None:
    client = AvailabilityClient()
    config = AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx",
        username="D\\u",
        primary_email="self@example.com",
        calendar_time_zone="UTC",
    )
    monkeypatch.setattr(service, "configured_client", lambda: client)
    monkeypatch.setattr(service, "configured_store", lambda: ReferenceStore(tmp_path / "state.db"))
    monkeypatch.setattr(service, "load_config", lambda: config)
    service.get_user_availability(
        attendees=[{"email": "room@example.com", "type": "Meeting Room"}],
        start="2026-08-03T09:00:00Z",
        end="2026-08-03T10:00:00Z",
    )
    assert client.attendees == [{"email": "room@example.com", "attendee_type": "Room"}]


class TemplateClient:
    def search_emails_multi_folder(self, **kwargs):
        assert kwargs["folders"] == ["sentitems"]
        return {"returned": 0, "items": [], "per_folder": []}


def test_workflow_normalizes_friendly_folder_before_template_search(tmp_path: Path) -> None:
    config = AppConfig(
        ews_url="https://mail.example.com/EWS/Exchange.asmx",
        username="D\\u",
        primary_email="self@example.com",
        company_email_domains=["example.com"],
    )
    workflow = SemanticMailWorkflow(
        TemplateClient(), ReferenceStore(tmp_path / "state.db"), config  # type: ignore[arg-type]
    )
    result = workflow.extract_email_template(folders=["Sent Items"])
    assert result["status"] == "not_found"


def test_render_template_accepts_friendly_mode() -> None:
    rendered, strategy = _render_template_body(
        "<html><body>old</body></html>",
        mode="Replace Content",
        new_content_html="<p>new</p>",
    )
    assert "<p>new</p>" in rendered
    assert strategy == "replace_body_preserve_head_and_body_attributes"


def test_cli_extract_email_template_accepts_friendly_folder(monkeypatch, capsys) -> None:
    received = {}

    def fake(**kwargs):
        received.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(service, "extract_email_template", fake)
    args = argparse.Namespace(
        message_ref=None,
        folders=["Sent Items"],
        sender_query=None,
        participant_query=None,
        subject_contains="周报",
        after=None,
        before=None,
        limit=20,
        lookback_days=365,
    )
    assert cli.extract_email_template_command(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert received["folders"] == ["Sent Items"]


def test_agent_tool_docs_explain_template_decoupling() -> None:
    extract_doc = server.extract_email_template.__doc__ or ""
    compose_doc = server.compose_email.__doc__ or ""
    reply_doc = server.reply_to_email.__doc__ or ""
    assert "template_ref" in extract_doc
    assert "template_ref" in compose_doc
    assert "template_ref" in reply_doc
    assert "第一封" in extract_doc


def test_template_writers_expose_only_one_html_body_parameter() -> None:
    for writer in (server.compose_email, server.reply_to_email, service.compose_email, service.reply_to_email):
        parameters = inspect.signature(writer).parameters
        assert "body_html" in parameters
        assert "template_ref" in parameters
        assert "template_content_html" not in parameters


def test_cli_compose_email_passes_body_html_with_template_ref(monkeypatch, capsys) -> None:
    received = {}

    def fake(**kwargs):
        received.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(service, "compose_email", fake)
    args = argparse.Namespace(
        to=["a@example.com"],
        cc=None,
        bcc=None,
        subject="subject",
        html="<p>new content</p>",
        html_file=None,
        attachment=None,
        template_ref="tmpl_x",
        no_template_inline_images=False,
        copy_template_attachments=False,
        lookback_days=365,
    )
    assert cli.compose_email_command(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert received["body_html"] == "<p>new content</p>"
    assert received["template_ref"] == "tmpl_x"
    assert "template_content_html" not in received


def test_cli_reply_email_passes_template_ref(monkeypatch, capsys) -> None:
    received = {}

    def fake(**kwargs):
        received.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(service, "reply_to_email", fake)
    args = argparse.Namespace(
        html="<p>new</p>",
        html_file=None,
        reply_all=False,
        template_ref="tmpl_x",
        no_template_inline_images=False,
        copy_template_attachments=False,
        message_ref="msg_x",
        folders=["Sent Items"],
        sender_query=None,
        participant_query=None,
        subject_contains="周报",
        after=None,
        before=None,
        limit=20,
        lookback_days=365,
    )
    assert cli.reply_email_command(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert received["template_ref"] == "tmpl_x"
    assert received["folders"] == ["Sent Items"]
