from __future__ import annotations

from pathlib import Path

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import DraftResult
from exchange_ews_mcp.state_store import ReferenceStore
from exchange_ews_mcp.workflow import SemanticMailWorkflow


class FakeSemanticClient:
    def __init__(self, *, both_have_history: bool = False) -> None:
        self.both_have_history = both_have_history
        self.created: list[dict] = []
        self.replied: list[dict] = []
        self.forwarded: list[dict] = []

    def resolve_names(self, *, query: str, limit: int = 20, **kwargs):
        if not query.isascii():
            return {
                "query": query,
                "status": "romanized_query_required",
                "returned": 0,
                "candidates": [],
                "message": "use romanized query",
            }
        if "@" in query:
            return {
                "query": query,
                "status": "resolved",
                "returned": 1,
                "candidates": [
                    {"display_name": "Exact", "email": query, "mailbox_type": "Mailbox"}
                ],
            }
        return {
            "query": query,
            "status": "resolved",
            "returned": 2,
            "candidates": [
                {
                    "display_name": "王小明一",
                    "email": "wangxiaoming1@company.com",
                    "mailbox_type": "Mailbox",
                },
                {
                    "display_name": "王小明二",
                    "email": "wangxiaoming2@company.com",
                    "mailbox_type": "Mailbox",
                },
            ],
        }

    def search_emails(self, *, folder, sender=None, participant_contains=None, **kwargs):
        target = sender or participant_contains or ""
        has_history = target in {"wangxiaoming1@company.com", "王小明一"} or self.both_have_history
        if has_history:
            prefix = "IN" if folder == "inbox" else "OUT"
            return {
                "returned": 1,
                "items": [{"item_id": f"{prefix}-1", "received_at": "2026-07-01T00:00:00Z"}],
            }
        return {"returned": 0, "items": []}

    def search_emails_multi_folder(self, *, participant_contains=None, **kwargs):
        if participant_contains:
            has_history = participant_contains.endswith("1@company.com") or self.both_have_history
            items = (
                [
                    {
                        "item_id": f"H-{participant_contains}",
                        "folder": "inbox",
                        "received_at": "2026-07-01T00:00:00Z",
                    }
                ]
                if has_history
                else []
            )
            return {
                "folders": ["inbox", "sentitems"],
                "returned": len(items),
                "items": items,
                "per_folder": [],
            }
        items = [
            {
                "item_id": "M1",
                "change_key": "CK1",
                "subject": "周报",
                "folder": "sentitems",
                "sent_at": "2026-07-20T00:00:00Z",
            }
        ]
        return {
            "folders": kwargs.get("folders") or ["inbox", "sentitems"],
            "returned": 1,
            "items": items,
            "per_folder": [],
        }

    def create_draft(self, *, to, cc, bcc, subject, body_html):
        self.created.append({"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body_html})
        return DraftResult(item_id="D1", change_key="DCK1", subject=subject, to=to, cc=cc, bcc=bcc)

    def add_attachment_to_draft(self, **kwargs):
        raise AssertionError("attachment not expected")

    def reply_as_draft(self, **kwargs):
        self.replied.append(kwargs)
        return DraftResult(item_id="R1", change_key="RCK1", draft_type="reply")

    def forward_as_draft(self, **kwargs):
        self.forwarded.append(kwargs)
        return DraftResult(item_id="F1", change_key="FCK1", to=kwargs["to"], draft_type="forward")


def _config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company.com"],
    )


def test_unique_prior_correspondent_is_auto_selected_and_disclosed(tmp_path: Path) -> None:
    workflow = SemanticMailWorkflow(
        FakeSemanticClient(),  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _config(),
    )
    result = workflow.resolve_people(query="wangxiaoming")
    assert result["selection_status"] == "auto_selected"
    assert result["default_rule_applied"] == "unique_prior_correspondent"
    assert result["selected"]["email"] == "wangxiaoming1@company.com"
    assert "已按默认规则自动选择" in result["user_notice"]
    assert all(item["is_company_address"] for item in result["candidates"])


def test_multiple_prior_correspondents_require_confirmation_and_can_resume(tmp_path: Path) -> None:
    client = FakeSemanticClient(both_have_history=True)
    store = ReferenceStore(tmp_path / "state.db")
    workflow = SemanticMailWorkflow(client, store, _config())  # type: ignore[arg-type]

    pending = workflow.compose_email(
        to_queries=["wangxiaoming"],
        subject="测试",
        body_html="<p>hello</p>",
    )
    assert pending["status"] == "needs_confirmation"
    candidates = pending["pending"][0]["candidates"]
    selected = next(item for item in candidates if item["email"] == "wangxiaoming2@company.com")

    resumed = workflow.continue_action(
        resume_token=pending["resume_token"],
        selections={"wangxiaoming": selected["person_ref"]},
    )
    assert resumed["status"] == "draft_created"
    assert client.created[0]["to"] == ["wangxiaoming2@company.com"]
    assert resumed["resumed_from"] == pending["resume_token"]


def test_compose_uses_unique_history_without_confirmation(tmp_path: Path) -> None:
    client = FakeSemanticClient()
    workflow = SemanticMailWorkflow(
        client,  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _config(),
    )
    result = workflow.compose_email(
        to_queries=["wangxiaoming"],
        subject="项目进展",
        body_html="<p>完成</p>",
    )
    assert result["status"] == "draft_created"
    assert client.created[0]["to"] == ["wangxiaoming1@company.com"]
    assert len(result["default_rule_notices"]) == 1
    assert result["sent"] is False


def test_find_and_reply_hide_item_id_behind_message_ref(tmp_path: Path) -> None:
    client = FakeSemanticClient()
    store = ReferenceStore(tmp_path / "state.db")
    workflow = SemanticMailWorkflow(client, store, _config())  # type: ignore[arg-type]

    found = workflow.find_email(subject_contains="周报")
    assert found["status"] == "resolved"
    message_ref = found["selected_message"]["message_ref"]
    assert message_ref.startswith("msg_")

    reply = workflow.reply_to_email(message_ref=message_ref, body_html="<p>已更新</p>")
    assert reply["status"] == "draft_created"
    assert client.replied[0]["item_id"] == "M1"
    assert reply["sent"] is False


def test_chinese_query_is_rejected_without_history_lookup(tmp_path: Path) -> None:
    workflow = SemanticMailWorkflow(
        FakeSemanticClient(),  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _config(),
    )
    result = workflow.resolve_people(query="王小明")
    assert result["selection_status"] == "needs_romanized_query"
    assert result["selected"] is None


class MultiDomainPersonClient(FakeSemanticClient):
    def __init__(self, history_emails: set[str]) -> None:
        super().__init__()
        self.history_emails = {value.casefold() for value in history_emails}

    def resolve_names(self, *, query: str, limit: int = 100, **kwargs):
        all_candidates = [
            {"display_name": "小明 A", "email": "xiaoming@company.com", "mailbox_type": "Mailbox"},
            {"display_name": "小明 B", "email": "xiaoming01@company.com", "mailbox_type": "Mailbox"},
            {"display_name": "小明 C", "email": "xiaoming369@company.com", "mailbox_type": "Mailbox"},
            {"display_name": "小明 D", "email": "xiaoming@company2.com", "mailbox_type": "Mailbox"},
            {"display_name": "小明 E", "email": "xiaoming468@company2.com", "mailbox_type": "Mailbox"},
            {"display_name": "外部小明", "email": "xiaoming@outside.com", "mailbox_type": "Mailbox"},
            {"display_name": "非前缀", "email": "otherxiaoming@company.com", "mailbox_type": "Mailbox"},
        ]
        if "@" in query:
            candidates = [
                item for item in all_candidates
                if item["email"].casefold() == query.casefold()
            ]
        else:
            candidates = all_candidates
        return {
            "query": query,
            "status": "resolved" if candidates else "not_found",
            "returned": len(candidates),
            "candidates": candidates[:limit],
        }

    def search_emails_multi_folder(self, *, participant_contains=None, **kwargs):
        target = str(participant_contains or "").casefold()
        if target in self.history_emails:
            return {
                "folders": ["inbox", "sentitems"],
                "returned": 1,
                "items": [
                    {
                        "item_id": f"H-{target}",
                        "folder": "inbox",
                        "received_at": "2026-07-20T00:00:00Z",
                    }
                ],
                "per_folder": [],
            }
        return {"folders": ["inbox", "sentitems"], "returned": 0, "items": [], "per_folder": []}

    def search_emails(self, **kwargs):
        return {"returned": 0, "items": []}


def _multi_domain_config() -> AppConfig:
    return AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        company_email_domains=["company.com", "company2.com"],
    )


def test_romanized_name_collects_all_matching_candidates_across_company_domains(tmp_path: Path) -> None:
    workflow = SemanticMailWorkflow(
        MultiDomainPersonClient({"xiaoming369@company.com"}),  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _multi_domain_config(),
    )
    result = workflow.resolve_people(query="xiaoming")

    assert result["query_mode"] == "romanized_name"
    assert result["returned"] == 5
    assert {item["email"] for item in result["candidates"]} == {
        "xiaoming@company.com",
        "xiaoming01@company.com",
        "xiaoming369@company.com",
        "xiaoming@company2.com",
        "xiaoming468@company2.com",
    }
    assert result["selected"]["email"] == "xiaoming369@company.com"
    assert result["default_rule_applied"] == "unique_prior_correspondent"
    assert result["prior_correspondent_count"] == 1


def test_exact_local_part_has_no_priority_in_romanized_name_mode(tmp_path: Path) -> None:
    workflow = SemanticMailWorkflow(
        MultiDomainPersonClient({"xiaoming369@company.com"}),  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _multi_domain_config(),
    )
    result = workflow.resolve_people(query="xiaoming")
    assert result["selected"]["email"] != "xiaoming@company.com"
    assert result["selected"]["email"] == "xiaoming369@company.com"


def test_two_prior_correspondents_require_user_confirmation(tmp_path: Path) -> None:
    workflow = SemanticMailWorkflow(
        MultiDomainPersonClient(
            {"xiaoming369@company.com", "xiaoming468@company2.com"}
        ),  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _multi_domain_config(),
    )
    result = workflow.resolve_people(query="xiaoming")
    assert result["selection_status"] == "needs_confirmation"
    assert result["selected"] is None
    assert result["prior_correspondent_count"] == 2
    assert result["ambiguity_reason"] == "multiple_prior_correspondents"
    assert "2 个与你有邮件来往" in result["user_notice"]


def test_complete_email_is_exact_search_mode(tmp_path: Path) -> None:
    workflow = SemanticMailWorkflow(
        MultiDomainPersonClient(set()),  # type: ignore[arg-type]
        ReferenceStore(tmp_path / "state.db"),
        _multi_domain_config(),
    )
    result = workflow.resolve_people(query="xiaoming468@company2.com")
    assert result["query_mode"] == "exact_email"
    assert result["selection_status"] == "resolved"
    assert result["default_rule_applied"] == "exact_email"
    assert result["returned"] == 1
    assert result["selected"]["email"] == "xiaoming468@company2.com"
