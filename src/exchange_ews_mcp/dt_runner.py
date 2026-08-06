from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from platformdirs import user_config_dir

from . import __version__
from .config import AppConfig
from .dt_config import DtTestConfig, VALID_GROUPS
from .ews import EwsClient
from .phase2_config import Phase2TestConfig
from .state_store import ReferenceStore
from .workflow import SemanticMailWorkflow
from .calendar_workflow import CalendarWorkflow
from .calendar_utils import (
    apply_current_user_working_hours_override, configured_working_intervals, format_utc
)
from .workflow_test_config import Phase023TestConfig


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _redacted_message(message: dict[str, Any]) -> dict[str, Any]:
    body = message.get("body_html") or ""
    return {
        "item_id": message.get("item_id"),
        "change_key_present": bool(message.get("change_key")),
        "subject": message.get("subject"),
        "sender": message.get("sender") or message.get("from"),
        "received_at": message.get("received_at"),
        "is_draft": message.get("is_draft"),
        "body_type": message.get("body_type"),
        "body_chars_fetched": len(body),
        "body_truncated": message.get("body_truncated"),
        "attachment_count": len(message.get("attachments") or []),
    }


def _report_path(stamp: str, prefix: str = "phase2") -> Path:
    root = Path(user_config_dir("exchange-ews-mcp", appauthor=False)) / "test-reports"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{prefix}-{stamp}.json"


def run_phase2_integration_tests(
    client: EwsClient,
    config: Phase2TestConfig,
    *,
    read_only: bool = False,
    write_report: bool = True,
    stamp: str | None = None,
) -> dict[str, Any]:
    profile = config.normalized()
    stamp = stamp or _utc_stamp()
    marker = f"EWS-MCP-v{__version__}-{stamp}"
    steps: list[dict[str, Any]] = []
    matched_messages: list[dict[str, Any]] = []
    created_drafts: list[dict[str, Any]] = []

    def run_step(name: str, action: Callable[[], Any]) -> Any | None:
        started = datetime.now(timezone.utc)
        try:
            details = action()
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append({"name": name, "status": "PASS", "elapsed_ms": elapsed, "details": details})
            return details
        except Exception as exc:  # noqa: BLE001 - diagnostics must continue after failures
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "elapsed_ms": elapsed,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            return None

    run_step("connection", lambda: {"inbox_display_name": client.test_connection()})

    def list_inbox() -> dict[str, Any]:
        page = client.list_emails(folder="inbox", limit=min(5, profile.search_limit))
        return {
            "returned": page["returned"],
            "total_items_in_view": page["total_items_in_view"],
            "includes_last_item": page["includes_last_item"],
        }

    run_step("list_inbox", list_inbox)

    for index, sender in enumerate(profile.senders, start=1):
        def search_sender(sender_address: str = sender) -> dict[str, Any]:
            page = client.search_emails(
                folder="inbox",
                sender=sender_address,
                subject_contains=profile.subject_contains,
                limit=profile.search_limit,
            )
            if not page["items"]:
                extra = f"，主题包含 {profile.subject_contains!r}" if profile.subject_contains else ""
                raise RuntimeError(f"未找到发件人为 {sender_address}{extra} 的邮件。")
            first = page["items"][0]
            matched_messages.append(first)
            return {
                "sender": sender_address,
                "returned": page["returned"],
                "selected_item_id": first.get("item_id"),
                "selected_change_key_present": bool(first.get("change_key")),
                "selected_subject": first.get("subject"),
            }

        search_result = run_step(f"search_sender_{index}", search_sender)
        if not search_result:
            continue

        selected = matched_messages[-1]

        def get_selected(message: dict[str, Any] = selected) -> dict[str, Any]:
            detail = client.get_email(
                item_id=str(message["item_id"]),
                change_key=message.get("change_key"),
                max_body_chars=5000,
            )
            if not detail.get("item_id"):
                raise RuntimeError("GetItem 成功但未返回 item_id。")
            return _redacted_message(detail)

        run_step(f"get_sender_message_{index}", get_selected)

    if not read_only and matched_messages:
        source = matched_messages[0]
        source_id = str(source["item_id"])

        def create_reply() -> dict[str, Any]:
            # Deliberately omit ChangeKey: v0.3 must fetch the current value automatically.
            result = client.reply_as_draft(
                item_id=source_id,
                body_html=f"<p>{marker}: reply draft integration test.</p>",
                reply_all=False,
                change_key=None,
            )
            data = result.as_dict()
            created_drafts.append(data)
            if not result.change_key:
                raise RuntimeError("回复草稿未返回 ChangeKey。")
            verified = client.get_email(
                item_id=result.item_id,
                change_key=result.change_key,
                max_body_chars=5000,
            )
            if verified.get("is_draft") is not True:
                raise RuntimeError("回复项目不是草稿。")
            return {"draft": data, "verified": _redacted_message(verified), "change_key_mode": "auto"}

        run_step("reply_as_draft_auto_changekey", create_reply)

        def create_forward() -> dict[str, Any]:
            result = client.forward_as_draft(
                item_id=source_id,
                to=[profile.draft_recipient],
                body_html=f"<p>{marker}: forward draft integration test.</p>",
                change_key=None,
            )
            data = result.as_dict()
            created_drafts.append(data)
            verified = client.get_email(
                item_id=result.item_id,
                change_key=result.change_key,
                max_body_chars=5000,
            )
            if verified.get("is_draft") is not True:
                raise RuntimeError("转发项目不是草稿。")
            return {"draft": data, "verified": _redacted_message(verified), "change_key_mode": "auto"}

        run_step("forward_as_draft_auto_changekey", create_forward)

        new_draft_holder: dict[str, Any] = {}

        def create_new_draft() -> dict[str, Any]:
            result = client.create_draft(
                to=[profile.draft_recipient],
                subject=f"[{marker}] create_draft integration test",
                body_html=f"<p>{marker}: new draft integration test.</p>",
            )
            data = result.as_dict()
            created_drafts.append(data)
            new_draft_holder.update(data)
            verified = client.get_email(
                item_id=result.item_id,
                change_key=result.change_key,
                max_body_chars=5000,
            )
            if verified.get("is_draft") is not True:
                raise RuntimeError("新建项目不是草稿。")
            return {"draft": data, "verified": _redacted_message(verified)}

        new_draft = run_step("create_and_get_new_draft", create_new_draft)

        if new_draft and new_draft_holder.get("item_id"):
            test_file: Path | None = None

            def attach_test_file() -> dict[str, Any]:
                nonlocal test_file
                roots = client.attachment_roots()
                if not roots:
                    raise RuntimeError("没有可用的附件允许目录。")
                test_dir = roots[0] / "ExchangeEwsMcpTests"
                test_dir.mkdir(parents=True, exist_ok=True)
                test_file = test_dir / f"{marker}.txt"
                test_file.write_text(
                    f"Exchange EWS MCP v{__version__} integration test\nmarker={marker}\n",
                    encoding="utf-8",
                )
                result = client.add_attachment_to_draft(
                    item_id=str(new_draft_holder["item_id"]),
                    change_key=None,
                    file_path=str(test_file),
                )
                verified = client.get_email(
                    item_id=result.root_item_id,
                    change_key=result.root_item_change_key,
                    max_body_chars=5000,
                )
                names = [str(item.get("name")) for item in verified.get("attachments") or []]
                if test_file.name not in names:
                    raise RuntimeError("附件操作返回成功，但重新读取草稿时未找到测试附件。")
                return {
                    "attachment": result.as_dict(),
                    "verified_attachment_names": names,
                    "local_test_file_removed": True,
                }

            try:
                run_step("add_attachment_and_verify", attach_test_file)
            finally:
                if test_file is not None:
                    try:
                        test_file.unlink(missing_ok=True)
                        test_file.parent.rmdir()
                    except OSError:
                        pass

    elif read_only:
        steps.append(
            {
                "name": "write_tests",
                "status": "SKIP",
                "details": "read_only=true；未创建回复、转发、新邮件或附件草稿。",
            }
        )
    else:
        steps.append(
            {
                "name": "write_tests",
                "status": "SKIP",
                "details": "没有任何测试发件人搜索成功，无法选择源邮件。",
            }
        )

    passed = sum(step["status"] == "PASS" for step in steps)
    failed = sum(step["status"] == "FAIL" for step in steps)
    skipped = sum(step["status"] == "SKIP" for step in steps)
    report = {
        "version": __version__,
        "started_at": stamp,
        "read_only": read_only,
        "phase2_config": {
            "senders": profile.senders,
            "draft_recipient": profile.draft_recipient,
            "subject_contains": profile.subject_contains,
            "search_limit": profile.search_limit,
        },
        "summary": {
            "status": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "steps": steps,
        "created_drafts": created_drafts,
        "notes": [
            "报告不会保存邮件正文，只保存主题、发件人、正文长度等诊断元数据。",
            "所有写操作均为 SaveOnly 草稿，不会发送邮件。",
            "测试生成的 Exchange 草稿不会自动删除，请在 Outlook/OWA 草稿箱中检查后手动清理。",
        ],
    }
    if write_report:
        path = _report_path(stamp, prefix="atomic-dt")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(path)
    return report


def run_phase023_integration_tests(
    client: EwsClient,
    config: Phase023TestConfig,
    *,
    read_only: bool = False,
    store: ReferenceStore | None = None,
    write_report: bool = True,
    stamp: str | None = None,
) -> dict[str, Any]:
    """Run real-Exchange DT for v0.3 Workflow Primitives.

    No message is ever sent. Write mode creates one ordinary draft and updates the
    same draft in place to verify ChangeKey refresh and draft_ref resolution.
    """

    profile = config.normalized()
    store = store or ReferenceStore()
    stamp = stamp or _utc_stamp()
    marker = f"EWS-MCP-v{__version__}-{stamp}"
    steps: list[dict[str, Any]] = []
    created_drafts: list[dict[str, Any]] = []
    selected_message: dict[str, Any] = {}

    def run_step(name: str, action: Callable[[], Any]) -> Any | None:
        started = datetime.now(timezone.utc)
        try:
            details = action()
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append({"name": name, "status": "PASS", "elapsed_ms": elapsed, "details": details})
            return details
        except Exception as exc:  # noqa: BLE001 - diagnostics should continue
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "elapsed_ms": elapsed,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            return None

    run_step("connection", lambda: {"inbox_display_name": client.test_connection()})

    def current_user() -> dict[str, Any]:
        result = client.get_current_user()
        if result.get("status") != "resolved" or not result.get("primary_email"):
            raise RuntimeError(
                "当前用户无法唯一解析。请先运行 set-current-user --email 你的公司邮箱。"
            )
        ref = store.upsert_reference(
            kind="person",
            external_key=str(result["primary_email"]).casefold(),
            payload={
                "display_name": result.get("display_name"),
                "email": result.get("primary_email"),
            },
            ttl_days=30,
        )
        return {
            "status": result.get("status"),
            "display_name": result.get("display_name"),
            "primary_email": result.get("primary_email"),
            "person_ref": ref,
            "source": result.get("source"),
        }

    run_step("get_current_user", current_user)

    for index, query in enumerate(profile.person_queries, start=1):
        def resolve_person(value: str = query) -> dict[str, Any]:
            result = client.resolve_names(query=value, limit=20)
            if not result["candidates"]:
                warning_text = "; ".join(
                    str(item.get("message") or "") for item in result.get("warnings", [])
                )
                suffix = f"；warnings={warning_text}" if warning_text else ""
                raise RuntimeError(f"人员解析未找到 {value!r} 的候选人{suffix}。")
            candidates: list[dict[str, Any]] = []
            for candidate in result["candidates"][:10]:
                email = candidate.get("email")
                person_ref = None
                if email:
                    person_ref = store.upsert_reference(
                        kind="person",
                        external_key=str(email).casefold(),
                        payload={
                            "display_name": candidate.get("display_name"),
                            "email": email,
                        },
                        ttl_days=30,
                    )
                candidates.append(
                    {
                        "display_name": candidate.get("display_name"),
                        "email": email,
                        "mailbox_type": candidate.get("mailbox_type"),
                        "sources": candidate.get("sources") or [],
                        "person_ref": person_ref,
                    }
                )
            return {
                "query": value,
                "strategy": result.get("strategy"),
                "returned": result["returned"],
                "warnings": result.get("warnings") or [],
                "candidates": candidates,
            }

        run_step(f"resolve_names_{index}", resolve_person)

    def search_sender() -> dict[str, Any]:
        page = client.search_emails(
            folder="inbox",
            sender=profile.sender,
            subject_contains=profile.subject_contains,
            limit=profile.search_limit,
        )
        if not page["items"]:
            raise RuntimeError("增强搜索没有找到测试发件人的邮件。")
        selected_message.update(page["items"][0])
        return {
            "returned": page["returned"],
            "selected_subject": selected_message.get("subject"),
            "display_to": selected_message.get("display_to"),
            "display_cc": selected_message.get("display_cc"),
            "conversation_id_present": bool(selected_message.get("conversation_id")),
            "parent_folder_id_present": bool(selected_message.get("parent_folder_id")),
        }

    run_step("enhanced_search_sender", search_sender)

    def search_participant() -> dict[str, Any]:
        page = client.search_emails(
            folder="inbox",
            participant_contains=profile.sender,
            subject_contains=profile.subject_contains,
            limit=profile.search_limit,
        )
        if not page["items"]:
            raise RuntimeError("participant_contains 未匹配到测试邮件。")
        return {"returned": page["returned"], "selected_subject": page["items"][0].get("subject")}

    run_step("enhanced_search_participant", search_participant)

    def multi_folder_search() -> dict[str, Any]:
        page = client.search_emails_multi_folder(
            folders=["inbox", "sentitems"], limit=min(10, profile.search_limit)
        )
        if not page["items"]:
            raise RuntimeError("跨文件夹搜索没有返回任何邮件。")
        return {
            "folders": page["folders"],
            "returned": page["returned"],
            "per_folder": page["per_folder"],
        }

    run_step("multi_folder_search", multi_folder_search)

    if selected_message.get("item_id"):
        def reference_and_get() -> dict[str, Any]:
            message_ref = store.upsert_reference(
                kind="message",
                external_key=str(selected_message["item_id"]),
                payload={
                    "item_id": selected_message["item_id"],
                    "change_key": selected_message.get("change_key"),
                    "subject": selected_message.get("subject"),
                    "folder": "inbox",
                },
                ttl_days=7,
            )
            stored = store.get_reference(message_ref, expected_kind="message")
            detail = client.get_email(
                item_id=str(stored.payload["item_id"]),
                change_key=stored.payload.get("change_key"),
                max_body_chars=5000,
            )
            return {"message_ref": message_ref, "message": _redacted_message(detail)}

        run_step("message_ref_get_email", reference_and_get)

    def action_session_roundtrip() -> dict[str, Any]:
        token = store.create_action_session(
            {"intent": "recipient_selection", "candidates": ["one", "two"]}, ttl_hours=1
        )
        before = store.get_action_session(token)
        after = store.update_action_session(
            token,
            state={"intent": "recipient_selection", "selection": "one"},
            status="resolved",
        )
        removed = store.delete_action_session(token)
        if before["status"] != "pending" or after["status"] != "resolved" or not removed:
            raise RuntimeError("ActionSessionStore 状态流转不正确。")
        return {"resume_token_prefix": token.split("_", 1)[0], "deleted": removed}

    run_step("action_session_store_roundtrip", action_session_roundtrip)

    if read_only:
        steps.append(
            {
                "name": "draft_write_tests",
                "status": "SKIP",
                "details": "read_only=true；未创建或更新草稿。",
            }
        )
    else:
        draft_holder: dict[str, Any] = {}

        def create_ref_draft() -> dict[str, Any]:
            result = client.create_draft(
                to=[profile.draft_recipient],
                subject=f"[{marker}] before update",
                body_html=f"<p>{marker}: initial body.</p>",
            )
            data = result.as_dict()
            draft_ref = store.upsert_reference(
                kind="draft",
                external_key=result.item_id,
                payload={
                    "item_id": result.item_id,
                    "change_key": result.change_key,
                    "subject": result.subject,
                    "folder": "drafts",
                },
                ttl_days=30,
            )
            data["draft_ref"] = draft_ref
            draft_holder.update(data)
            created_drafts.append(data)
            return data

        created = run_step("create_draft_with_ref", create_ref_draft)

        if created:
            def update_by_ref() -> dict[str, Any]:
                stored = store.get_reference(str(draft_holder["draft_ref"]), expected_kind="draft")
                updated = client.update_draft(
                    item_id=str(stored.payload["item_id"]),
                    change_key=stored.payload.get("change_key"),
                    subject=f"[{marker}] updated",
                    body_html=f"<h2>{marker}</h2><p>updated in place.</p>",
                    to=[profile.draft_recipient],
                    importance="High",
                )
                draft_ref = store.upsert_reference(
                    kind="draft",
                    external_key=updated.item_id,
                    payload={
                        "item_id": updated.item_id,
                        "change_key": updated.change_key,
                        "subject": updated.subject,
                        "folder": "drafts",
                    },
                    ttl_days=30,
                )
                verified = client.get_email(
                    item_id=updated.item_id,
                    change_key=updated.change_key,
                    max_body_chars=5000,
                )
                if verified.get("is_draft") is not True:
                    raise RuntimeError("更新后的项目不是草稿。")
                if verified.get("subject") != f"[{marker}] updated":
                    raise RuntimeError("草稿主题没有按预期更新。")
                if verified.get("importance") != "High":
                    raise RuntimeError("草稿重要性没有按预期更新。")
                return {
                    "draft_ref": draft_ref,
                    "change_key_present": bool(updated.change_key),
                    "verified": _redacted_message(verified),
                    "verified_importance": verified.get("importance"),
                }

            run_step("update_draft_by_ref", update_by_ref)

    passed = sum(step["status"] == "PASS" for step in steps)
    failed = sum(step["status"] == "FAIL" for step in steps)
    skipped = sum(step["status"] == "SKIP" for step in steps)
    report = {
        "version": __version__,
        "started_at": stamp,
        "read_only": read_only,
        "v03_dt_config": {
            "person_queries": profile.person_queries,
            "sender": profile.sender,
            "draft_recipient": profile.draft_recipient,
            "subject_contains": profile.subject_contains,
            "search_limit": profile.search_limit,
        },
        "summary": {
            "status": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "steps": steps,
        "created_drafts": created_drafts,
        "notes": [
            "报告不保存邮件正文，只保存主题、正文长度和引用等诊断元数据。",
            "所有写操作只创建或更新 SaveOnly 草稿，不会发送邮件。",
            "测试草稿不会自动删除，请在 Outlook/OWA 检查后手动清理。",
        ],
    }
    if write_report:
        path = _report_path(stamp, prefix="workflow-v03-dt")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(path)
    return report



def run_semantic_v04_integration_tests(
    client: EwsClient,
    config: DtTestConfig,
    *,
    app_config: AppConfig,
    read_only: bool = False,
    store: ReferenceStore | None = None,
    stamp: str | None = None,
) -> dict[str, Any]:
    """Run v0.4 semantic-mail workflow DT without sending mail."""
    profile = config.normalized()
    stamp = stamp or _utc_stamp()
    marker = f"EWS-MCP-v{__version__}-{stamp}"
    store = store or ReferenceStore()
    workflow = SemanticMailWorkflow(client, store, app_config)
    steps: list[dict[str, Any]] = []
    created_drafts: list[dict[str, Any]] = []

    def run_step(name: str, operation: Callable[[], dict[str, Any]]) -> None:
        try:
            details = operation()
            steps.append({"name": name, "status": "PASS", "details": details})
        except Exception as exc:
            steps.append({"name": name, "status": "FAIL", "error": str(exc)})

    def company_domains() -> dict[str, Any]:
        domains = sorted(workflow.company_domains)
        if not domains:
            raise RuntimeError("未配置 company_email_domains，也无法从 primary_email 推导。")
        return {"company_email_domains": domains}

    run_step("company_email_domains", company_domains)

    def resolve_people() -> dict[str, Any]:
        query = profile.person_queries[0]
        result = workflow.resolve_people(query=query, limit=max(profile.search_limit, 100))
        if result.get("selection_status") in {"not_found", "needs_romanized_query"}:
            raise RuntimeError(f"语义人员解析失败：{result.get('selection_status')}")
        candidates = list(result.get("candidates") or [])
        if not candidates:
            raise RuntimeError("语义人员解析未返回候选。")
        removed_rules = {"exact_company_email_local_part", "exact_email_local_part"}
        if result.get("default_rule_applied") in removed_rules:
            raise RuntimeError("仍在使用已删除的裸邮箱 local-part 优先规则。")
        if "@" not in query:
            stem = query.casefold()
            allowed_domains = set(result.get("company_email_domains") or [])
            for candidate in candidates:
                local = str(candidate.get("email_local_part") or "").casefold()
                domain = str(candidate.get("email_domain") or "").casefold()
                if not local.startswith(stem):
                    raise RuntimeError(f"候选不属于拼音组 {query!r}: {candidate.get('email')}")
                if allowed_domains and domain not in allowed_domains:
                    raise RuntimeError(f"候选来自未配置域名：{candidate.get('email')}")
        return {
            "query": query,
            "query_mode": result.get("query_mode"),
            "selection_status": result.get("selection_status"),
            "default_rule_applied": result.get("default_rule_applied"),
            "ambiguity_reason": result.get("ambiguity_reason"),
            "prior_correspondent_count": result.get("prior_correspondent_count"),
            "company_email_domains": result.get("company_email_domains"),
            "returned": len(candidates),
            "candidate_emails": [item.get("email") for item in candidates],
            "selected_email": (result.get("selected") or {}).get("email"),
            "user_notice": result.get("user_notice"),
        }

    run_step("resolve_people_with_history", resolve_people)

    def semantic_find() -> dict[str, Any]:
        result = workflow.find_email(
            folders=["inbox", "sentitems"],
            participant_query=profile.senders[0],
            subject_contains=profile.subject_contains,
            limit=profile.search_limit,
        )
        if result.get("status") in {"not_found", "needs_confirmation"}:
            raise RuntimeError(f"语义邮件定位失败：{result.get('status')}")
        if not result.get("items"):
            raise RuntimeError("语义邮件定位未返回邮件。")
        return {
            "status": result.get("status"),
            "returned": len(result.get("items") or []),
            "first_message_ref": result["items"][0].get("message_ref"),
            "first_subject": result["items"][0].get("subject"),
        }

    run_step("find_email_semantic", semantic_find)

    if read_only:
        steps.append({
            "name": "compose_email_draft",
            "status": "SKIP",
            "details": "read_only=true；未创建 v0.4 语义草稿。",
        })
    else:
        def compose() -> dict[str, Any]:
            result = workflow.compose_email(
                to_queries=[profile.draft_recipient],
                subject=f"[{marker}] semantic compose",
                body_html=f"<p>{marker}: v0.4 semantic workflow draft.</p>",
            )
            if result.get("status") != "draft_created":
                raise RuntimeError(f"compose_email 未创建草稿：{result.get('status')}")
            draft = result["draft"]
            created_drafts.append({
                "draft_type": "semantic_compose",
                "draft_ref": draft.get("draft_ref"),
                "item_id": draft.get("item_id"),
            })
            return {
                "draft_ref": draft.get("draft_ref"),
                "subject": draft.get("subject"),
                "sent": result.get("sent"),
                "default_rule_notices": result.get("default_rule_notices"),
            }

        run_step("compose_email_draft", compose)

    passed = sum(step["status"] == "PASS" for step in steps)
    failed = sum(step["status"] == "FAIL" for step in steps)
    skipped = sum(step["status"] == "SKIP" for step in steps)
    return {
        "version": __version__,
        "started_at": stamp,
        "read_only": read_only,
        "summary": {
            "status": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "steps": steps,
        "created_drafts": created_drafts,
    }


def run_calendar_v05_integration_tests(
    client: EwsClient,
    profile: DtTestConfig,
    *,
    app_config: AppConfig,
    read_only: bool,
    store: ReferenceStore,
    stamp: str,
) -> dict[str, Any]:
    """Validate v0.5 calendar primitives and coordination without sending invitations."""
    steps: list[dict[str, Any]] = []
    created_calendar_items: list[dict[str, Any]] = []
    workflow = CalendarWorkflow(
        client, store, app_config, SemanticMailWorkflow(client, store, app_config)
    )

    def run_step(name: str, action: Callable[[], Any]) -> Any | None:
        started = datetime.now(timezone.utc)
        try:
            details = action()
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append({"name": name, "status": "PASS", "elapsed_ms": elapsed, "details": details})
            return details
        except Exception as exc:  # noqa: BLE001
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append({
                "name": name, "status": "FAIL", "elapsed_ms": elapsed,
                "error_type": type(exc).__name__, "error": str(exc),
            })
            return None

    current_email = (app_config.primary_email or profile.draft_recipient).strip()
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(days=1)
    window_start = base.replace(hour=0, minute=0)
    window_end = window_start + timedelta(days=7)

    run_step("calendar_preferences", lambda: {
        "time_zone": app_config.calendar_time_zone,
        "workday_start": app_config.calendar_workday_start,
        "workday_end": app_config.calendar_workday_end,
        "workdays": app_config.calendar_workdays or [0, 1, 2, 3, 4],
        "slot_interval_minutes": app_config.calendar_slot_interval_minutes,
        "generated_work_intervals": len(configured_working_intervals(
            window_start=window_start, window_end=window_end,
            zone_name=app_config.calendar_time_zone,
            workday_start=app_config.calendar_workday_start,
            workday_end=app_config.calendar_workday_end,
            workdays=app_config.calendar_workdays or [0, 1, 2, 3, 4],
        )),
    })

    def availability_self() -> dict[str, Any]:
        result = client.get_user_availability(
            attendees=[{"email": current_email, "attendee_type": "Organizer"}],
            start=format_utc(window_start), end=format_utc(window_end),
            interval_minutes=app_config.calendar_slot_interval_minutes,
        )
        result = apply_current_user_working_hours_override(
            result,
            current_user_email=current_email,
            zone_name=app_config.calendar_time_zone,
            workday_start=app_config.calendar_workday_start,
            workday_end=app_config.calendar_workday_end,
            workdays=app_config.calendar_workdays or [0, 1, 2, 3, 4],
        )
        if not result.get("attendees"):
            raise RuntimeError("GetUserAvailability 未返回当前用户结果。")
        if result["attendees"][0].get("status") != "success":
            raise RuntimeError(f"当前用户忙闲查询失败：{result['attendees'][0]}")
        item = result["attendees"][0]
        working_hours = item.get("working_hours")
        if working_hours:
            if item.get("working_hours_source") != "local_config_override":
                raise RuntimeError("当前用户 WorkingHours 未应用 MCP 本地配置覆盖。")
            zone = working_hours.get("time_zone") or {}
            if zone.get("iana_name") != app_config.calendar_time_zone:
                raise RuntimeError("当前用户 WorkingHours 时区未使用 calendar_time_zone。")
            periods = working_hours.get("working_periods") or []
            if not periods:
                raise RuntimeError("当前用户本地 WorkingHours 缺少工作周期。")
            first = periods[0]
            if first.get("start") != app_config.calendar_workday_start or first.get("end") != app_config.calendar_workday_end:
                raise RuntimeError("当前用户 WorkingHours 未使用本地配置的开始/结束时间。")
            missing_period = sorted({"days", "start", "end", "start_minutes", "end_minutes"} - set(first))
            if missing_period:
                raise RuntimeError(f"WorkingPeriod 缺少规范化字段：{missing_period}")
        return {
            "email": current_email, "response_code": item.get("response_code"),
            "event_count": len(item.get("events") or []),
            "working_hours_present": bool(working_hours),
            "working_hours_source": item.get("working_hours_source"),
            "working_hours": working_hours,
            "exchange_working_hours": item.get("exchange_working_hours"),
        }
    run_step("availability_self", availability_self)

    def calendar_list() -> dict[str, Any]:
        result = client.list_calendar_events(
            start=format_utc(window_start), end=format_utc(window_end), limit=25
        )
        return {"returned": result.get("returned"), "window_start": result.get("start"), "window_end": result.get("end")}
    run_step("calendar_list_window", calendar_list)

    def common_slots() -> dict[str, Any]:
        result = workflow.find_meeting_times(
            attendee_queries=[current_email],
            window_start=format_utc(window_start), window_end=format_utc(window_end),
            duration_minutes=60, max_results=3, include_self=True,
        )
        if result.get("status") not in {"resolved", "not_found"}:
            raise RuntimeError(f"共同空闲查询返回异常状态：{result.get('status')}")
        slots = result.get("slots") or []
        if slots:
            first = slots[0]
            required = {"start", "end", "start_utc", "end_utc", "local_start", "local_end", "display_time_zone"}
            missing = sorted(required - set(first))
            if missing:
                raise RuntimeError(f"候选时间缺少展示字段：{missing}")
        return {
            "status": result.get("status"), "returned": result.get("returned"),
            "slots": slots,
            "calendar_time_zone": result.get("calendar_time_zone"),
            "display_time_zone": result.get("display_time_zone"),
            "transport_time_zone": result.get("transport_time_zone"),
            "local_window_start": result.get("local_window_start"),
            "local_window_end": result.get("local_window_end"),
        }
    slots_result = run_step("find_common_meeting_times", common_slots)

    if read_only:
        steps.append({
            "name": "calendar_write_tests", "status": "SKIP",
            "details": "read_only=true；未创建任何日历项目，也未发送邀请。",
        })
    else:
        holder: dict[str, Any] = {}

        def create_unsent_meeting() -> dict[str, Any]:
            preferred = ((slots_result or {}).get("slots") or [])
            if preferred:
                start_value, end_value = preferred[0]["start"], preferred[0]["end"]
            else:
                # DT 只验证 SendToNone 保存能力；没有空闲候选时选未来固定窗口且不发送。
                start_value = format_utc(window_start + timedelta(hours=12))
                end_value = format_utc(window_start + timedelta(hours=13))
            result = client.create_meeting(
                subject=f"[EWS-MCP-v{__version__}-{stamp}] calendar DT",
                body_html=f"<p>Exchange EWS MCP v{__version__} calendar integration test. SendToNone.</p>",
                start=start_value, end=end_value,
                required_attendees=[current_email], optional_attendees=[],
                location="EWS MCP DT", reminder_minutes=0, send_invitations=False,
            )
            data = result.as_dict()
            holder.update(data)
            created_calendar_items.append(data)
            if data.get("sent") is not False:
                raise RuntimeError("DT 会议意外标记为已发送。")
            return data
        created = run_step("create_meeting_send_to_none", create_unsent_meeting)

        if created and holder.get("item_id"):
            def verify_meeting() -> dict[str, Any]:
                item = client.get_calendar_item(
                    item_id=str(holder["item_id"]), change_key=holder.get("change_key")
                )
                if item.get("subject") != holder.get("subject"):
                    raise RuntimeError("重新读取的会议主题不一致。")
                return {
                    "item_id": item.get("item_id"), "subject": item.get("subject"),
                    "start": item.get("start"), "end": item.get("end"),
                    "required_attendees": item.get("required_attendees"),
                }
            run_step("get_created_calendar_item", verify_meeting)

            def cleanup_meeting() -> dict[str, Any]:
                return client.delete_calendar_item(
                    item_id=str(holder["item_id"]), change_key=holder.get("change_key")
                )
            run_step("cleanup_unsent_calendar_item", cleanup_meeting)

    passed = sum(step["status"] == "PASS" for step in steps)
    failed = sum(step["status"] == "FAIL" for step in steps)
    skipped = sum(step["status"] == "SKIP" for step in steps)
    return {
        "version": __version__, "started_at": stamp, "read_only": read_only,
        "summary": {
            "status": "PASS" if failed == 0 else "FAIL",
            "passed": passed, "failed": failed, "skipped": skipped,
        },
        "steps": steps, "created_calendar_items": created_calendar_items,
    }


def run_weekly_report_v06_integration_tests(
    client: EwsClient,
    profile: DtTestConfig,
    *,
    app_config: AppConfig,
    read_only: bool,
    store: ReferenceStore,
    stamp: str,
) -> dict[str, Any]:
    """Validate the v0.6 weekly-report context/update chain without sending mail.

    A subject filter is required because weekly-report extraction must target a
    known report family. Read-only mode validates search, extraction, slots,
    compact locations, the full Agent prompt, and the one-time token. Full mode
    additionally creates one native Reply All draft using an unchanged slot and
    the existing subject; it never sends the draft.
    """
    steps: list[dict[str, Any]] = []
    created_drafts: list[dict[str, Any]] = []
    workflow = SemanticMailWorkflow(client, store, app_config)
    context_holder: dict[str, Any] = {}

    def run_step(name: str, action: Callable[[], Any]) -> Any | None:
        started = datetime.now(timezone.utc)
        try:
            details = action()
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append({"name": name, "status": "PASS", "elapsed_ms": elapsed, "details": details})
            return details
        except Exception as exc:  # noqa: BLE001 - DT must continue and report failures
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            steps.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "elapsed_ms": elapsed,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            return None

    if not profile.subject_contains:
        steps.append(
            {
                "name": "weekly_report_subject_filter",
                "status": "SKIP",
                "details": (
                    "DT 配置未设置 subject_contains；为避免误选普通邮件，"
                    "weekly-report-v06 未运行。请重新 configure-dt 并提供周报主题关键字。"
                ),
            }
        )
    else:
        def get_context() -> dict[str, Any]:
            result = workflow.get_weekly_report_context(
                user_input=f"{stamp} DT：验证周报上下文与 Reply All 草稿链路。",
                subject_contains=profile.subject_contains,
                folders=["sentitems"],
                lookback_days=365,
                max_reports=5,
            )
            if result.get("status") != "context_ready":
                raise RuntimeError(f"周报上下文状态异常：{result.get('status')}")
            token = str(result.get("weekly_flow_token") or "")
            slots = list(result.get("editable_slots") or [])
            prompt = str(result.get("agent_prompt") or "")
            if not token.startswith("weeklyflow_"):
                raise RuntimeError("未返回 weeklyflow_ 一次性 token。")
            if not slots:
                raise RuntimeError("未返回可编辑周报槽位。")
            if any(set(item) - {"slot_id", "text", "location"} for item in slots):
                raise RuntimeError("Production 周报槽位包含非紧凑字段。")
            if "【日期硬校验】" not in prompt or "【周报化改写要求】" not in prompt:
                raise RuntimeError("Agent Prompt 缺少日期硬校验或周报化改写规则。")
            context_holder.update(result)
            return {
                "source_subject": result.get("source_subject"),
                "report_count": result.get("report_count"),
                "editable_slot_count": result.get("editable_slot_count"),
                "response_profile": result.get("response_profile"),
                "token_prefix": token.split("_", 1)[0],
                "prompt_chars": len(prompt),
                "locations_present": sum(item.get("location") is not None for item in slots),
                "draft_created": result.get("draft_created"),
                "sent": result.get("sent"),
            }

        context_result = run_step("weekly_report_context", get_context)

        if read_only:
            steps.append(
                {
                    "name": "weekly_report_reply_all_draft",
                    "status": "SKIP",
                    "details": "read_only=true；未创建周报 Reply All 草稿。",
                }
            )
        elif context_result is not None:
            def create_reply_all_draft() -> dict[str, Any]:
                slots = list(context_holder.get("editable_slots") or [])
                first = slots[0]
                result = workflow.update_weekly_report(
                    weekly_flow_token=str(context_holder["weekly_flow_token"]),
                    changes=[
                        {
                            "slot_id": str(first["slot_id"]),
                            "new_text": str(first["text"]),
                        }
                    ],
                    subject=str(context_holder.get("source_subject") or profile.subject_contains),
                )
                if result.get("status") != "draft_created":
                    raise RuntimeError(f"周报 update 未创建草稿：{result.get('status')}")
                if result.get("sent") is not False or result.get("reply_all") is not True:
                    raise RuntimeError("周报 DT 草稿不是未发送的 Reply All。")
                if result.get("body_update_after_reply") is not False:
                    raise RuntimeError("周报 DT 意外在 Reply All 后二次覆盖 Body。")
                draft = dict(result.get("draft") or {})
                created_drafts.append(
                    {
                        "draft_type": "weekly_report_reply_all",
                        "draft_ref": result.get("draft_ref"),
                        "item_id": draft.get("item_id"),
                        "subject": draft.get("subject"),
                    }
                )
                return {
                    "draft_ref": result.get("draft_ref"),
                    "subject": draft.get("subject"),
                    "reply_all": result.get("reply_all"),
                    "sent": result.get("sent"),
                    "weekly_flow_status": result.get("weekly_flow_status"),
                    "requested_changes": (result.get("slot_update") or {}).get("requested_changes"),
                    "applied_changes": (result.get("slot_update") or {}).get("applied_changes"),
                    "structure_sha256_present": bool(
                        (result.get("html_validation") or {}).get("structure_sha256")
                    ),
                }

            run_step("weekly_report_reply_all_draft", create_reply_all_draft)

    passed = sum(step["status"] == "PASS" for step in steps)
    failed = sum(step["status"] == "FAIL" for step in steps)
    skipped = sum(step["status"] == "SKIP" for step in steps)
    return {
        "version": __version__,
        "started_at": stamp,
        "read_only": read_only,
        "summary": {
            "status": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "steps": steps,
        "created_drafts": created_drafts,
    }

def run_dt_suite(
    client: EwsClient,
    config: DtTestConfig,
    *,
    read_only: bool = False,
    groups: list[str] | None = None,
    store: ReferenceStore | None = None,
    app_config: AppConfig | None = None,
) -> dict[str, Any]:
    """Run the cumulative real-Exchange DT suite grouped by capability layer.

    Default groups:
      - atomic: EWS atomic mail capabilities
      - workflow-v03: v0.3 workflow primitives
      - semantic-mail-v04: v0.4 semantic mail workflow
      - calendar-v05: v0.5 calendar coordination
      - weekly-report-v06: v0.6 weekly-report context and Reply All draft

    Future releases can append new groups without creating new DT commands.
    """

    profile = config.normalized()
    requested = groups or list(VALID_GROUPS)
    normalized_groups: list[str] = []
    for raw in requested:
        value = raw.strip().lower()
        if value not in VALID_GROUPS:
            raise ValueError(
                f"未知 DT group：{raw!r}；可选值为 {', '.join(VALID_GROUPS)}。"
            )
        if value not in normalized_groups:
            normalized_groups.append(value)

    stamp = _utc_stamp()
    group_reports: list[dict[str, Any]] = []
    all_steps: list[dict[str, Any]] = []
    created_drafts: list[dict[str, Any]] = []
    created_calendar_items: list[dict[str, Any]] = []
    store = store or ReferenceStore()

    if "atomic" in normalized_groups:
        atomic_config = Phase2TestConfig(
            senders=profile.senders,
            draft_recipient=profile.draft_recipient,
            subject_contains=profile.subject_contains,
            search_limit=profile.search_limit,
        )
        atomic = run_phase2_integration_tests(
            client, atomic_config, read_only=read_only, write_report=False, stamp=stamp
        )
        group_reports.append(
            {
                "id": "atomic",
                "label": "Atomic Core DT",
                "summary": atomic["summary"],
                "steps": atomic["steps"],
                "created_drafts": atomic.get("created_drafts") or [],
            }
        )

    if "workflow-v03" in normalized_groups:
        workflow_config = Phase023TestConfig(
            person_queries=profile.person_queries,
            sender=profile.senders[0],
            draft_recipient=profile.draft_recipient,
            subject_contains=profile.subject_contains,
            search_limit=profile.search_limit,
        )
        workflow = run_phase023_integration_tests(
            client,
            workflow_config,
            read_only=read_only,
            store=store,
            write_report=False,
            stamp=stamp,
        )
        group_reports.append(
            {
                "id": "workflow-v03",
                "label": "v0.3 Workflow Primitives DT",
                "summary": workflow["summary"],
                "steps": workflow["steps"],
                "created_drafts": workflow.get("created_drafts") or [],
            }
        )

    if "semantic-mail-v04" in normalized_groups:
        resolved_app_config = app_config or getattr(client, "config", None)
        if resolved_app_config is None:
            domain = profile.draft_recipient.rsplit("@", 1)[1]
            resolved_app_config = AppConfig(
                ews_url="https://example.invalid/EWS/Exchange.asmx",
                username="dt",
                primary_email=profile.draft_recipient,
                company_email_domains=[domain],
            )
        semantic = run_semantic_v04_integration_tests(
            client,
            profile,
            app_config=resolved_app_config,
            read_only=read_only,
            store=store,
            stamp=stamp,
        )
        group_reports.append(
            {
                "id": "semantic-mail-v04",
                "label": "v0.4 Semantic Mail Workflow DT",
                "summary": semantic["summary"],
                "steps": semantic["steps"],
                "created_drafts": semantic.get("created_drafts") or [],
            }
        )

    if "calendar-v05" in normalized_groups:
        resolved_app_config = app_config or getattr(client, "config", None)
        if resolved_app_config is None:
            domain = profile.draft_recipient.rsplit("@", 1)[1]
            resolved_app_config = AppConfig(
                ews_url="https://example.invalid/EWS/Exchange.asmx",
                username="dt", primary_email=profile.draft_recipient,
                company_email_domains=[domain],
            )
        calendar = run_calendar_v05_integration_tests(
            client, profile, app_config=resolved_app_config, read_only=read_only,
            store=store, stamp=stamp,
        )
        group_reports.append({
            "id": "calendar-v05", "label": "v0.5 Calendar Coordination DT",
            "summary": calendar["summary"], "steps": calendar["steps"],
            "created_calendar_items": calendar.get("created_calendar_items") or [],
        })

    if "weekly-report-v06" in normalized_groups:
        resolved_app_config = app_config or getattr(client, "config", None)
        if resolved_app_config is None:
            domain = profile.draft_recipient.rsplit("@", 1)[1]
            resolved_app_config = AppConfig(
                ews_url="https://example.invalid/EWS/Exchange.asmx",
                username="dt", primary_email=profile.draft_recipient,
                company_email_domains=[domain],
            )
        weekly = run_weekly_report_v06_integration_tests(
            client, profile, app_config=resolved_app_config, read_only=read_only,
            store=store, stamp=stamp,
        )
        group_reports.append({
            "id": "weekly-report-v06", "label": "v0.6 Weekly Report DT",
            "summary": weekly["summary"], "steps": weekly["steps"],
            "created_drafts": weekly.get("created_drafts") or [],
        })

    for group in group_reports:
        for step in group["steps"]:
            all_steps.append({"group": group["id"], **step})
        created_drafts.extend(group.get("created_drafts") or [])
        created_calendar_items.extend(group.get("created_calendar_items") or [])

    passed = sum(step["status"] == "PASS" for step in all_steps)
    failed = sum(step["status"] == "FAIL" for step in all_steps)
    skipped = sum(step["status"] == "SKIP" for step in all_steps)
    report = {
        "version": __version__,
        "started_at": stamp,
        "read_only": read_only,
        "requested_groups": normalized_groups,
        "dt_config": {
            "person_queries": profile.person_queries,
            "senders": profile.senders,
            "draft_recipient": profile.draft_recipient,
            "subject_contains": profile.subject_contains,
            "search_limit": profile.search_limit,
        },
        "summary": {
            "status": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "groups_passed": sum(g["summary"]["failed"] == 0 for g in group_reports),
            "groups_failed": sum(g["summary"]["failed"] > 0 for g in group_reports),
        },
        "groups": group_reports,
        "steps": all_steps,
        "created_drafts": created_drafts,
        "created_calendar_items": created_calendar_items,
        "notes": [
            "DT 采用持续增长的分组结构，后续版本会继续向同一 dt-test 追加 group。",
            "Atomic Core DT 验证 EWS 原子邮件能力。",
            "v0.3 Workflow Primitives DT 验证人员解析、增强搜索、引用句柄和草稿更新。",
            "v0.4 Semantic Mail Workflow DT 验证历史往来消歧、语义邮件定位和高层草稿创建。",
            "v0.5 Calendar Coordination DT 验证忙闲、工作时间、共同空闲和 SendToNone 会议保存。",
            "v0.6 Weekly Report DT 验证五周上下文、紧凑槽位、完整 Prompt、一次性 token 和未发送 Reply All 草稿。",
            "邮件写操作只创建或修改 SaveOnly 草稿；日历 DT 只保存 SendToNone 测试会议并自动删除，不会发送邀请。",
        ],
    }
    path = _report_path(stamp, prefix="dt")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(path)
    return report
