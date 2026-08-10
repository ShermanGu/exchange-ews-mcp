from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
import hashlib
from typing import Any

from .config import AppConfig, effective_company_domains
from .ews import EwsClient, normalize_mail_folders
from .state_store import ReferenceStore
from .weekly_report import (
    apply_editable_text_slot_changes,
    build_weekly_report_agent_prompt,
    compact_editable_text_slots_for_agent,
    editable_slot_manifest_sha256,
    extract_editable_text_slots,
    html_structure_sha256,
    referenced_content_ids,
    split_weekly_report_sections,
)


DEFAULT_HISTORY_DAYS = 365
MAX_HISTORY_MESSAGES = 100
WEEKLY_FLOW_TTL_MINUTES = 30


def _valid_email(value: str) -> str | None:
    _, parsed = parseaddr(value.strip())
    return parsed if parsed and "@" in parsed else None


def _email_parts(email: str | None) -> tuple[str | None, str | None]:
    if not email or "@" not in email:
        return None, None
    local, domain = email.rsplit("@", 1)
    return local.casefold(), domain.casefold()


def _stamp(item: dict[str, Any]) -> str:
    return str(
        item.get("received_at")
        or item.get("sent_at")
        or item.get("last_modified_at")
        or item.get("created_at")
        or ""
    )


def _after_days(days: int) -> str:
    value = datetime.now(timezone.utc) - timedelta(days=days)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


class SemanticMailWorkflow:
    """Deterministic semantic mail workflow built on top of EWS core primitives."""

    def __init__(self, client: EwsClient, store: ReferenceStore, config: AppConfig) -> None:
        self.client = client
        self.store = store
        self.config = config
        self.company_domains = set(effective_company_domains(config))

    def _person_ref(self, candidate: dict[str, Any]) -> str | None:
        email = str(candidate.get("email") or "").strip()
        if not email:
            return None
        return self.store.upsert_reference(
            kind="person",
            external_key=email.casefold(),
            payload={
                "display_name": candidate.get("display_name"),
                "email": email,
                "routing_type": candidate.get("routing_type"),
                "mailbox_type": candidate.get("mailbox_type"),
            },
            ttl_days=30,
        )

    def _message_ref(self, item: dict[str, Any]) -> str | None:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            return None
        return self.store.upsert_reference(
            kind="message",
            external_key=item_id,
            payload={
                "item_id": item_id,
                "change_key": item.get("change_key"),
                "subject": item.get("subject"),
                "folder": item.get("folder"),
                "conversation_id": item.get("conversation_id"),
                "internet_message_id": item.get("internet_message_id"),
            },
            ttl_days=7,
        )

    def _draft_dict(self, result: Any) -> dict[str, Any]:
        data = result.as_dict()
        data["reference_kind"] = "draft"
        data["update_tool"] = "edit_mail_draft"
        data["draft_ref"] = self.store.upsert_reference(
            kind="draft",
            external_key=result.item_id,
            payload={
                "item_id": result.item_id,
                "change_key": result.change_key,
                "subject": result.subject,
                "folder": "drafts",
                "draft_type": result.draft_type,
            },
            ttl_days=30,
        )
        return data

    def _communication_stats(
        self,
        email: str,
        display_name: str | None,
        *,
        lookback_days: int,
    ) -> dict[str, Any]:
        after = _after_days(lookback_days)
        collected: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        def collect(page: dict[str, Any], direction: str | None = None) -> None:
            for index, item in enumerate(page.get("items") or []):
                item_direction = direction
                if item_direction is None:
                    item_direction = (
                        "sent_to_candidate"
                        if item.get("folder") == "sentitems"
                        else "received_from_candidate"
                    )
                key = str(item.get("item_id") or f"{item_direction}:{index}:{_stamp(item)}")
                existing = collected.setdefault(key, {**item, "directions": []})
                if item_direction not in existing["directions"]:
                    existing["directions"].append(item_direction)

        try:
            collect(
                self.client.search_emails_multi_folder(
                    folders=["inbox", "sentitems"],
                    participant_contains=email,
                    after=after,
                    limit=MAX_HISTORY_MESSAGES,
                    offset=0,
                )
            )
        except Exception as exc:
            warnings.append(f"cross-folder email lookup: {exc}")

        has_sent_match = any(
            "sent_to_candidate" in item["directions"] for item in collected.values()
        )
        if not has_sent_match and display_name and display_name.strip():
            try:
                collect(
                    self.client.search_emails(
                        folder="sentitems",
                        participant_contains=display_name.strip(),
                        after=after,
                        limit=MAX_HISTORY_MESSAGES,
                        offset=0,
                    ),
                    "sent_to_candidate",
                )
            except Exception as exc:
                warnings.append(f"sent display-name lookup {display_name!r}: {exc}")

        items = list(collected.values())
        received_count = sum("received_from_candidate" in item["directions"] for item in items)
        sent_count = sum("sent_to_candidate" in item["directions"] for item in items)
        return {
            "communication_count": len(items),
            "received_from_count": received_count,
            "sent_to_count": sent_count,
            "last_communication_at": max((_stamp(item) for item in items), default=None),
            "communication_lookup_warning": "; ".join(warnings) if warnings else None,
        }

    def resolve_people(
        self,
        *,
        query: str,
        limit: int = 100,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
        auto_select: bool = True,
    ) -> dict[str, Any]:
        """Resolve a romanized name stem or one complete SMTP address.

        A query without ``@`` is always treated as a romanized *person name*, not as
        an exact mailbox local-part. All returned company addresses whose local-part
        starts with that name are peers and must be compared by communication history.
        A query containing ``@`` is treated as one exact SMTP address.
        """
        normalized = query.strip()
        if not normalized:
            raise ValueError("query 不能为空。")
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")
        if not 1 <= lookback_days <= 3650:
            raise ValueError("lookback_days 必须在 1 到 3650 之间。")

        raw = self.client.resolve_names(query=normalized, limit=limit)
        if raw.get("status") == "romanized_query_required":
            return {
                **raw,
                "query_mode": "romanized_name",
                "selection_status": "needs_romanized_query",
                "selected": None,
                "default_rule_applied": None,
                "ambiguity_reason": None,
                "user_notice": raw.get("message"),
            }

        query_email = _valid_email(normalized) if "@" in normalized else None
        query_mode = "exact_email" if query_email else "romanized_name"
        query_name = normalized.casefold()
        unfiltered = list(raw.get("candidates") or [])
        matched: list[dict[str, Any]] = []

        for raw_candidate in unfiltered:
            candidate = dict(raw_candidate)
            email = str(candidate.get("email") or "").strip()
            local, domain = _email_parts(email)

            if query_mode == "exact_email":
                if not query_email or not email or email.casefold() != query_email.casefold():
                    continue
                match_reason = "exact_email"
            else:
                # A bare romanized name is a name stem, never an exact local-part.
                # Keep every matching employee across every configured company domain.
                if not local or not local.startswith(query_name):
                    continue
                if self.company_domains and (not domain or domain not in self.company_domains):
                    continue
                match_reason = "romanized_name_stem"

            candidate["person_ref"] = self._person_ref(candidate)
            candidate["email_local_part"] = local
            candidate["email_domain"] = domain
            candidate["is_company_address"] = bool(domain and domain in self.company_domains)
            candidate["match_reason"] = match_reason
            candidate["exact_email_match"] = bool(
                query_email and email.casefold() == query_email.casefold()
            )
            candidate["romanized_name_stem_match"] = bool(
                query_mode == "romanized_name" and local and local.startswith(query_name)
            )

            if email:
                candidate.update(
                    self._communication_stats(
                        email,
                        str(candidate.get("display_name") or "") or None,
                        lookback_days=lookback_days,
                    )
                )
            else:
                candidate.update(
                    {
                        "communication_count": 0,
                        "received_from_count": 0,
                        "sent_to_count": 0,
                        "last_communication_at": None,
                        "communication_lookup_warning": None,
                    }
                )
            matched.append(candidate)

        matched.sort(
            key=lambda item: (
                int(item.get("communication_count") or 0),
                str(item.get("last_communication_at") or ""),
                str(item.get("email_local_part") or ""),
                str(item.get("email_domain") or ""),
            ),
            reverse=True,
        )

        selected: dict[str, Any] | None = None
        rule: str | None = None
        notice: str | None = None
        ambiguity_reason: str | None = None
        correspondents = [
            item for item in matched if int(item.get("communication_count") or 0) > 0
        ]

        if auto_select and matched:
            if query_mode == "exact_email" and len(matched) == 1:
                selected, rule = matched[0], "exact_email"
            elif query_mode == "romanized_name" and len(matched) == 1:
                selected, rule = matched[0], "single_candidate"
            elif query_mode == "romanized_name" and len(correspondents) == 1:
                selected, rule = correspondents[0], "unique_prior_correspondent"
                notice = (
                    f"查询 {normalized!r} 返回 {len(matched)} 个同级候选；只有 "
                    f"{selected.get('display_name') or selected.get('email')} 与当前用户存在邮件来往，"
                    "已按默认规则自动选择。"
                )

        if selected is not None:
            selection_status = (
                "auto_selected" if rule == "unique_prior_correspondent" else "resolved"
            )
        elif matched:
            selection_status = "needs_confirmation"
            if query_mode == "romanized_name":
                if len(correspondents) >= 2:
                    ambiguity_reason = "multiple_prior_correspondents"
                    notice = (
                        f"查询 {normalized!r} 返回 {len(matched)} 个同级候选，其中 "
                        f"{len(correspondents)} 个与你有邮件来往，需要你确认具体收件人。"
                    )
                elif len(correspondents) == 0 and len(matched) > 1:
                    ambiguity_reason = "no_prior_correspondent"
                    notice = (
                        f"查询 {normalized!r} 返回 {len(matched)} 个同级候选，"
                        "但都没有查到历史邮件来往，需要你确认具体收件人。"
                    )
            elif query_mode == "exact_email":
                ambiguity_reason = "duplicate_exact_email"
        else:
            selection_status = "not_found"

        return {
            **raw,
            "status": "resolved" if matched else "not_found",
            "query_mode": query_mode,
            "company_email_domains": sorted(self.company_domains),
            "lookback_days": lookback_days,
            "unfiltered_candidate_count": len(unfiltered),
            "returned": len(matched),
            "candidates": matched,
            "selected": selected,
            "selection_status": selection_status,
            "default_rule_applied": rule,
            "ambiguity_reason": ambiguity_reason,
            "prior_correspondent_count": len(correspondents),
            "user_notice": notice,
        }

    def _resolve_recipient_slot(
        self,
        query: str,
        *,
        lookback_days: int,
    ) -> dict[str, Any]:
        direct = _valid_email(query)
        result = self.resolve_people(query=query, lookback_days=lookback_days)
        selected = result.get("selected")
        if selected:
            return {
                "query": query,
                "status": "resolved",
                "email": selected.get("email"),
                "person_ref": selected.get("person_ref"),
                "selected": selected,
                "default_rule_applied": result.get("default_rule_applied"),
                "user_notice": result.get("user_notice"),
                "candidates": result.get("candidates") or [],
            }
        if direct and result.get("selection_status") == "not_found":
            _, domain = _email_parts(direct)
            return {
                "query": query,
                "status": "resolved",
                "email": direct,
                "person_ref": None,
                "selected": {
                    "display_name": None,
                    "email": direct,
                    "is_company_address": bool(domain and domain in self.company_domains),
                    "resolution_source": "direct_email",
                },
                "default_rule_applied": "direct_email",
                "user_notice": None,
                "candidates": [],
            }
        return {
            "query": query,
            "status": result.get("selection_status"),
            "email": None,
            "person_ref": None,
            "selected": None,
            "default_rule_applied": None,
            "user_notice": result.get("user_notice"),
            "candidates": result.get("candidates") or [],
            "message": result.get("message"),
        }

    def _resolve_slots(
        self,
        *,
        to_queries: list[str],
        cc_queries: list[str],
        bcc_queries: list[str],
        lookback_days: int,
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {"to": [], "cc": [], "bcc": []}
        for field, queries in (("to", to_queries), ("cc", cc_queries), ("bcc", bcc_queries)):
            for query in queries:
                result[field].append(
                    self._resolve_recipient_slot(query.strip(), lookback_days=lookback_days)
                )
        return result

    @staticmethod
    def _slot_addresses(slots: dict[str, list[dict[str, Any]]], field: str) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for slot in slots[field]:
            email = str(slot.get("email") or "").strip()
            key = email.casefold()
            if email and key not in seen:
                values.append(email)
                seen.add(key)
        return values

    @staticmethod
    def _pending_slots(slots: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for field, values in slots.items():
            for index, slot in enumerate(values):
                if slot.get("status") != "resolved":
                    pending.append({"field": field, "index": index, **slot})
        return pending

    def _execute_compose(self, payload: dict[str, Any], slots: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        to = self._slot_addresses(slots, "to")
        cc = self._slot_addresses(slots, "cc")
        bcc = self._slot_addresses(slots, "bcc")
        if not to and not cc and not bcc:
            raise ValueError("至少需要一个已解析的 To、CC 或 BCC 收件人。")
        # Validate all attachment paths before creating the draft.  This avoids
        # leaving a partially-created draft when a later attachment is outside the
        # allow-list, missing, or too large.  Test doubles from older releases may
        # not implement the preflight helper, so preserve compatibility there.
        attachment_paths: list[str] = []
        validate_attachment = getattr(self.client, "validate_attachment_path", None)
        for path_value in payload.get("attachments") or []:
            raw_path = str(path_value)
            attachment_paths.append(
                str(validate_attachment(raw_path)) if callable(validate_attachment) else raw_path
            )

        draft = self.client.create_draft(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=str(payload["subject"]),
            body_html=str(payload["body_html"]),
        )
        data = self._draft_dict(draft)
        attachments: list[dict[str, Any]] = []
        current_item_id = draft.item_id
        current_change_key = draft.change_key
        for path_value in attachment_paths:
            attached = self.client.add_attachment_to_draft(
                item_id=current_item_id,
                change_key=current_change_key,
                file_path=path_value,
            )
            current_item_id = attached.root_item_id
            current_change_key = attached.root_item_change_key
            attachments.append(attached.as_dict())
        if attachments:
            data["change_key"] = current_change_key
            data["draft_ref"] = self.store.upsert_reference(
                kind="draft",
                external_key=current_item_id,
                payload={
                    "item_id": current_item_id,
                    "change_key": current_change_key,
                    "subject": payload["subject"],
                    "folder": "drafts",
                    "draft_type": "new",
                },
                ttl_days=30,
            )
        notices = [
            str(slot["user_notice"])
            for values in slots.values()
            for slot in values
            if slot.get("user_notice")
        ]
        return {
            "status": "draft_created",
            "draft": data,
            "recipient_resolution": slots,
            "default_rule_notices": notices,
            "attachments": attachments,
            "sent": False,
        }

    def compose_email(
        self,
        *,
        to_queries: list[str],
        subject: str,
        body_html: str,
        cc_queries: list[str] | None = None,
        bcc_queries: list[str] | None = None,
        attachments: list[str] | None = None,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        if not subject.strip():
            raise ValueError("subject 不能为空。")
        if not body_html.strip():
            raise ValueError("body_html 不能为空。")
        if not to_queries and not cc_queries and not bcc_queries:
            raise ValueError("至少提供一个收件人查询。")
        payload = {
            "subject": subject.strip(),
            "body_html": body_html,
            "attachments": [str(Path(item)) for item in (attachments or [])],
            "lookback_days": lookback_days,
        }
        slots = self._resolve_slots(
            to_queries=to_queries,
            cc_queries=cc_queries or [],
            bcc_queries=bcc_queries or [],
            lookback_days=lookback_days,
        )
        pending = self._pending_slots(slots)
        if pending:
            token = self.store.create_action_session(
                {
                    "action": "compose_email",
                    "payload": payload,
                    "recipient_slots": slots,
                },
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "pending": pending,
                "resolved_recipients": slots,
                "sent": False,
            }
        return self._execute_compose(payload, slots)

    def find_email(
        self,
        *,
        folders: list[str] | None = None,
        sender_query: str | None = None,
        participant_query: str | None = None,
        subject_contains: str | None = None,
        unread_only: bool | None = None,
        has_attachments: bool | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
        offset: int = 0,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        criteria: dict[str, Any] = {
            "subject_contains": subject_contains,
            "unread_only": unread_only,
            "has_attachments": has_attachments,
            "after": after,
            "before": before,
            "limit": limit,
            "offset": offset,
        }
        person_resolution: dict[str, Any] = {}
        for field, query in (("sender", sender_query), ("participant_contains", participant_query)):
            if not query:
                continue
            resolved = self._resolve_recipient_slot(query, lookback_days=lookback_days)
            person_resolution[field] = resolved
            if resolved.get("status") != "resolved":
                token = self.store.create_action_session(
                    {
                        "action": "find_email",
                        "payload": {
                            "folders": folders or ["inbox", "sentitems"],
                            "sender_query": sender_query,
                            "participant_query": participant_query,
                            "subject_contains": subject_contains,
                            "unread_only": unread_only,
                            "has_attachments": has_attachments,
                            "after": after,
                            "before": before,
                            "limit": limit,
                            "offset": offset,
                            "lookback_days": lookback_days,
                        },
                        "person_resolution": person_resolution,
                    },
                    ttl_hours=24,
                )
                return {
                    "status": "needs_confirmation",
                    "resume_token": token,
                    "pending": [{"field": field, **resolved}],
                    "person_resolution": person_resolution,
                }
            criteria[field] = resolved["email"]

        page = self.client.search_emails_multi_folder(
            folders=folders or ["inbox", "sentitems"],
            **criteria,
        )
        items: list[dict[str, Any]] = []
        for raw in page.get("items") or []:
            item = dict(raw)
            item["message_ref"] = self._message_ref(item)
            items.append(item)
        status = "resolved" if len(items) == 1 else "multiple_matches" if items else "not_found"
        return {
            **page,
            "status": status,
            "person_resolution": person_resolution,
            "items": items,
            "selected_message": items[0] if len(items) == 1 else None,
        }

    def _resolve_message_for_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        message_ref = payload.get("message_ref")
        if message_ref:
            stored = self.store.get_reference(str(message_ref), expected_kind="message")
            return {
                "status": "resolved",
                "message_ref": message_ref,
                "item_id": stored.payload["item_id"],
                "change_key": stored.payload.get("change_key"),
            }
        found = self.find_email(
            folders=payload.get("folders"),
            sender_query=payload.get("sender_query"),
            participant_query=payload.get("participant_query"),
            subject_contains=payload.get("subject_contains"),
            after=payload.get("after"),
            before=payload.get("before"),
            limit=int(payload.get("limit") or 20),
            lookback_days=int(payload.get("lookback_days") or DEFAULT_HISTORY_DAYS),
        )
        if found.get("status") == "resolved":
            item = found["selected_message"]
            return {
                "status": "resolved",
                "message_ref": item["message_ref"],
                "item_id": item["item_id"],
                "change_key": item.get("change_key"),
                "find_result": found,
            }
        return found

    def reply_to_email(self, *, body_html: str, reply_all: bool = False, **message_query: Any) -> dict[str, Any]:
        if not body_html.strip():
            raise ValueError("body_html 不能为空。")
        payload = {**message_query, "body_html": body_html, "reply_all": reply_all}
        resolved = self._resolve_message_for_action(payload)
        if resolved.get("status") != "resolved":
            if resolved.get("status") == "not_found":
                return {"status": "not_found", "message_resolution": resolved, "sent": False}
            token = self.store.create_action_session(
                {"action": "reply_to_email", "payload": payload, "message_resolution": resolved},
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "message_resolution": resolved,
                "sent": False,
            }
        draft = self.client.reply_as_draft(
            item_id=str(resolved["item_id"]),
            change_key=resolved.get("change_key"),
            body_html=body_html,
            reply_all=reply_all,
        )
        return {"status": "draft_created", "draft": self._draft_dict(draft), "source_message": resolved, "sent": False}

    def get_weekly_report_context(
        self,
        *,
        user_input: str,
        reference_materials: list[dict[str, str]] | None = None,
        subject_contains: str = "周报",
        folder: str = "sentitems",
        folders: list[str] | None = None,
        lookback_days: int = 60,
        max_reports: int = 5,
    ) -> dict[str, Any]:
        """Start one short-lived weekly-report workflow and return plain-text slots.

        This operation is the mandatory first step for every weekly-report
        update. It is read-only for Exchange, but creates a random one-shot
        ``weekly_flow_token`` in local state. A newer context request for the
        same source message supersedes an older unused token.
        """
        user_value = user_input.strip()
        if not user_value:
            raise ValueError("user_input 不能为空。")
        subject_value = subject_contains.strip()
        if not subject_value:
            raise ValueError("subject_contains 不能为空。")
        if not 1 <= lookback_days <= 3650:
            raise ValueError("lookback_days 必须在 1 到 3650 之间。")
        if not 1 <= max_reports <= 5:
            raise ValueError("max_reports 必须在 1 到 5 之间。")

        normalized_materials: list[dict[str, str]] = []
        for index, raw in enumerate(reference_materials or []):
            if not isinstance(raw, dict):
                raise ValueError(f"reference_materials[{index}] 必须是对象。")
            name = str(raw.get("name") or f"材料 {index + 1}").strip()
            content = str(raw.get("content") or "")
            if not content.strip():
                raise ValueError(f"reference_materials[{index}].content 不能为空。")
            normalized_materials.append({"name": name, "content": content})

        search_folders = normalize_mail_folders(
            folder=folder,
            folders=folders,
            default_folder="sentitems",
        )
        page = self.client.search_emails_multi_folder(
            folders=search_folders,
            subject_contains=subject_value,
            after=_after_days(lookback_days),
            limit=1,
            offset=0,
        )
        items = list(page.get("items") or [])
        if not items:
            return {
                "status": "not_found",
                "subject_contains": subject_value,
                "search_folders": search_folders,
                "lookback_days": lookback_days,
                "draft_created": False,
                "sent": False,
            }

        latest_summary = dict(items[0])
        latest_item_id = str(latest_summary.get("item_id") or "").strip()
        if not latest_item_id:
            raise ValueError("最新周报搜索结果缺少 item_id。")
        latest = self.client.get_email(
            item_id=latest_item_id,
            change_key=latest_summary.get("change_key"),
            max_body_chars=None,
        )
        if latest.get("body_truncated"):
            raise ValueError("Exchange 将最新周报 Body 标记为截断，无法提取完整历史。")
        effective_body_type = str(latest.get("body_type") or "HTML").strip().upper()
        if effective_body_type != "HTML":
            raise ValueError(
                "当前周报工作流只接受 EWS 返回的 HTML 正文；"
                f"本次 BodyType={effective_body_type!r}。纯文本正文需要独立的历史分割规则。"
            )
        latest_full_body = str(latest.get("body_html") or "")
        if not latest_full_body.strip():
            raise ValueError("最新周报 Body 为空。")

        reports = split_weekly_report_sections(
            latest_full_body,
            max_reports=max_reports,
        )
        latest_template = reports[0].html
        editable_slots = extract_editable_text_slots(latest_template)
        if not editable_slots:
            raise ValueError("最新周报模板中没有提取到可编辑文本槽位。")
        agent_slots = compact_editable_text_slots_for_agent(
            editable_slots, template_html=latest_template
        )
        source_body_hash = hashlib.sha256(latest_full_body.encode("utf-8")).hexdigest()
        structure_hash = html_structure_sha256(latest_template)
        slot_manifest_hash = editable_slot_manifest_sha256(editable_slots)
        source_message = {**latest_summary, **latest}
        source_message_ref = self._message_ref(source_message)
        source_item_id = str(latest.get("item_id") or latest_item_id)

        flow_token = self.store.create_scoped_action_session(
            {
                "source_item_id": source_item_id,
                "source_change_key": latest.get("change_key") or latest_summary.get("change_key"),
                "source_subject": latest.get("subject") or latest_summary.get("subject"),
                "source_message_ref": source_message_ref,
                "source_conversation_id": latest.get("conversation_id") or latest_summary.get("conversation_id"),
                "source_body_sha256": source_body_hash,
                "search_folders": search_folders,
                "subject_contains": subject_value,
                "lookback_days": lookback_days,
                "template_html": latest_template,
                "template_structure_sha256": structure_hash,
                "template_slot_manifest_sha256": slot_manifest_hash,
                "template_slot_count": len(editable_slots),
                "report_count": len(reports),
            },
            action="weekly_report_update",
            scope_key=source_item_id,
            ttl_minutes=WEEKLY_FLOW_TTL_MINUTES,
            status="context_ready",
            token_prefix="weeklyflow_",
        )
        flow_session = self.store.get_action_session(flow_token)
        agent_prompt = build_weekly_report_agent_prompt(
            user_input=user_value,
            reports=reports,
            editable_slots=agent_slots,
            reference_materials=normalized_materials,
            embed_slots=False,
        )
        return {
            "status": "context_ready",
            "weekly_flow_token": flow_token,
            "weekly_flow_expires_at": flow_session["expires_at"],
            "weekly_flow_ttl_minutes": WEEKLY_FLOW_TTL_MINUTES,
            "source_subject": latest.get("subject") or latest_summary.get("subject"),
            "effective_body_type": effective_body_type,
            "search_folders": search_folders,
            "report_count": len(reports),
            "editable_slot_count": len(editable_slots),
            "editable_slots": agent_slots,
            "agent_prompt": agent_prompt,
            "response_profile": "compact_slots_v1",
            "draft_created": False,
            "sent": False,
        }

    def update_weekly_report(
        self,
        *,
        weekly_flow_token: str,
        changes: list[dict[str, Any]],
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Consume one context-ready token and create one Reply All draft.

        The token is random, short-lived and one-shot. Claiming it atomically
        moves the workflow from ``context_ready`` to ``applying``; duplicate,
        concurrent, superseded, expired or already-completed calls are rejected
        before any EWS write.
        """
        token_value = weekly_flow_token.strip()
        if not token_value.startswith("weeklyflow_"):
            raise ValueError(
                "weekly_flow_token 无效。必须先调用 get_weekly_report_context，"
                "并使用本次返回的 weeklyflow_ 令牌。"
            )
        try:
            flow = self.store.claim_action_session(
                token_value,
                expected_status="context_ready",
                next_status="applying",
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(
                "weekly_flow_token 无效、已过期、已使用或已被新的上下文取代。"
                "必须先调用 get_weekly_report_context，或重新调用该工具后再更新。"
            ) from exc

        payload = dict(flow.get("state") or {})
        if payload.get("action") != "weekly_report_update":
            self.store.update_action_session(
                token_value,
                status="failed",
                state={**payload, "error": "token action mismatch"},
            )
            raise ValueError("weekly_flow_token 不属于周报更新流程。")

        draft_created = False
        draft_item_id: str | None = None
        draft_change_key: str | None = None
        try:
            source_item_id = str(payload.get("source_item_id") or "").strip()
            if not source_item_id:
                raise ValueError("weekly_flow_token 中缺少 source_item_id。")
            template_html = str(payload.get("template_html") or "")
            if not template_html.strip():
                raise ValueError("weekly_flow_token 中缺少最新周报 HTML 模板。")

            current_slots = extract_editable_text_slots(template_html)
            current_slot_manifest = editable_slot_manifest_sha256(current_slots)
            expected_slot_manifest = str(payload.get("template_slot_manifest_sha256") or "")
            if not expected_slot_manifest or current_slot_manifest != expected_slot_manifest:
                raise ValueError("周报文本槽位清单与上下文不一致；请重新获取周报上下文。")

            latest_page = self.client.search_emails_multi_folder(
                folders=list(payload.get("search_folders") or ["sentitems"]),
                subject_contains=str(payload.get("subject_contains") or "周报"),
                after=_after_days(int(payload.get("lookback_days") or 60)),
                limit=1,
                offset=0,
            )
            latest_items = list(latest_page.get("items") or [])
            current_latest_id = str((latest_items[0] if latest_items else {}).get("item_id") or "").strip()
            if not current_latest_id or current_latest_id != source_item_id:
                self.store.update_action_session(
                    token_value,
                    status="context_stale",
                    state={**payload, "stale_reason": "newer_weekly_report"},
                )
                return {
                    "status": "context_stale",
                    "weekly_flow_token": token_value,
                    "weekly_flow_status": "context_stale",
                    "message": "当前最新周报已变化；请重新调用 get_weekly_report_context。",
                    "draft_created": False,
                    "sent": False,
                }

            source = self.client.get_email(
                item_id=source_item_id,
                change_key=payload.get("source_change_key"),
                max_body_chars=None,
            )
            if source.get("body_truncated"):
                raise ValueError("Exchange 将最新周报 Body 标记为截断，拒绝创建回复草稿。")
            source_full_body = str(source.get("body_html") or "")
            current_hash = hashlib.sha256(source_full_body.encode("utf-8")).hexdigest()
            expected_hash = str(payload.get("source_body_sha256") or "")
            if not expected_hash or current_hash != expected_hash:
                self.store.update_action_session(
                    token_value,
                    status="context_stale",
                    state={**payload, "stale_reason": "source_body_changed"},
                )
                return {
                    "status": "context_stale",
                    "weekly_flow_token": token_value,
                    "weekly_flow_status": "context_stale",
                    "message": "最新周报在获取上下文后已变化；请重新调用 get_weekly_report_context。",
                    "draft_created": False,
                    "sent": False,
                }

            if not changes and subject is None:
                raise ValueError("changes 与 subject 不能同时为空。")

            slot_update = apply_editable_text_slot_changes(template_html, changes)
            generated_html = slot_update.html
            validation = slot_update.html_validation
            expected_structure = str(payload.get("template_structure_sha256") or "")
            if not expected_structure or validation.structure_sha256 != expected_structure:
                raise ValueError("HTML 完整性校验失败：结构签名与上下文不一致。")

            subject_value = None
            if subject is not None:
                subject_value = subject.strip()
                if not subject_value:
                    raise ValueError("subject 不能设置为空字符串。")

            draft = self.client.reply_as_draft(
                item_id=str(source.get("item_id") or source_item_id),
                change_key=source.get("change_key") or payload.get("source_change_key"),
                body_html=generated_html,
                reply_all=True,
            )
            draft_created = True
            draft_item_id = draft.item_id
            draft_change_key = draft.change_key

            inline_content_ids = referenced_content_ids(generated_html)
            source_attachments = list(source.get("attachments") or [])
            attachment_by_cid = {
                str(item.get("content_id") or "").strip().strip("<>").casefold(): item
                for item in source_attachments
                if item.get("content_id")
            }
            copied_inline: list[dict[str, Any]] = []
            preserved_inline: list[str] = []
            attachment_warnings: list[str] = []

            draft_email = self.client.get_email(
                item_id=draft_item_id,
                change_key=draft_change_key,
                max_body_chars=None,
            )
            draft_change_key = draft_email.get("change_key") or draft_change_key
            existing_draft_cids = {
                str(item.get("content_id") or "").strip().strip("<>").casefold()
                for item in (draft_email.get("attachments") or [])
                if item.get("content_id")
            }
            for content_id in inline_content_ids:
                if content_id.casefold() in existing_draft_cids:
                    preserved_inline.append(content_id)
                    continue
                metadata = attachment_by_cid.get(content_id.casefold())
                if metadata is None:
                    attachment_warnings.append(
                        f"正文引用 cid:{content_id}，但源邮件附件元数据中未找到对应 ContentId。"
                    )
                    continue
                attachment_id = str(metadata.get("attachment_id") or "").strip()
                if not attachment_id:
                    attachment_warnings.append(f"cid:{content_id} 对应附件缺少 attachment_id。")
                    continue
                try:
                    attachment = self.client.get_file_attachment(attachment_id=attachment_id)
                    attached = self.client.add_file_attachment_bytes_to_draft(
                        item_id=draft_item_id,
                        change_key=draft_change_key,
                        filename=str(attachment.get("filename") or metadata.get("name") or "inline.bin"),
                        content_type=str(attachment.get("content_type") or metadata.get("content_type") or "application/octet-stream"),
                        content=attachment["content"],
                        is_inline=True,
                        content_id=content_id,
                    )
                    draft_change_key = attached.root_item_change_key or draft_change_key
                    copied_inline.append(
                        {
                            "content_id": content_id,
                            "filename": attached.filename,
                            "size": attached.size,
                        }
                    )
                except Exception as exc:
                    attachment_warnings.append(f"复制 cid:{content_id} 失败：{exc}")

            final_subject = draft_email.get("subject") or draft.subject
            if subject_value is not None:
                updated = self.client.update_draft(
                    item_id=draft_item_id,
                    change_key=draft_change_key,
                    subject=subject_value,
                )
                draft_change_key = updated.change_key or draft_change_key
                final_subject = subject_value

            draft_ref = self.store.upsert_reference(
                kind="draft",
                external_key=draft_item_id,
                payload={
                    "item_id": draft_item_id,
                    "change_key": draft_change_key,
                    "subject": final_subject,
                    "folder": "drafts",
                    "draft_type": "reply_all",
                },
                ttl_days=30,
            )
            self.store.update_action_session(
                token_value,
                status="completed",
                state={
                    **payload,
                    "draft_item_id": draft_item_id,
                    "draft_ref": draft_ref,
                    "completed_subject": final_subject,
                },
            )
            return {
                "status": "draft_created",
                "weekly_flow_token": token_value,
                "weekly_flow_status": "completed",
                "draft_ref": draft_ref,
                "draft": {
                    **draft.as_dict(),
                    "item_id": draft_item_id,
                    "change_key": draft_change_key,
                    "subject": final_subject,
                    "draft_ref": draft_ref,
                },
                "source_message_ref": payload.get("source_message_ref"),
                "slot_update": slot_update.as_dict(),
                "html_validation": validation.as_dict(),
                "reply_all": True,
                "body_update_after_reply": False,
                "inline_content_ids": inline_content_ids,
                "copied_inline_attachments": copied_inline,
                "preserved_inline_content_ids": preserved_inline,
                "attachment_warnings": attachment_warnings,
                "sent": False,
            }
        except Exception as exc:
            failure_status = "completed_with_error" if draft_created else "failed"
            failure_state = {
                **payload,
                "error": str(exc),
                "draft_item_id": draft_item_id,
                "draft_change_key": draft_change_key,
            }
            try:
                self.store.update_action_session(
                    token_value,
                    status=failure_status,
                    state=failure_state,
                )
            except Exception:
                pass
            raise

    def forward_email(
        self,
        *,
        to_queries: list[str],
        body_html: str,
        cc_queries: list[str] | None = None,
        bcc_queries: list[str] | None = None,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
        **message_query: Any,
    ) -> dict[str, Any]:
        if not body_html.strip():
            raise ValueError("body_html 不能为空。")
        slots = self._resolve_slots(
            to_queries=to_queries,
            cc_queries=cc_queries or [],
            bcc_queries=bcc_queries or [],
            lookback_days=lookback_days,
        )
        payload = {
            **message_query,
            "to_queries": to_queries,
            "cc_queries": cc_queries or [],
            "bcc_queries": bcc_queries or [],
            "body_html": body_html,
            "lookback_days": lookback_days,
        }
        pending = self._pending_slots(slots)
        if pending:
            token = self.store.create_action_session(
                {"action": "forward_email", "payload": payload, "recipient_slots": slots},
                ttl_hours=24,
            )
            return {"status": "needs_confirmation", "resume_token": token, "pending": pending, "sent": False}
        resolved_message = self._resolve_message_for_action(payload)
        if resolved_message.get("status") != "resolved":
            if resolved_message.get("status") == "not_found":
                return {"status": "not_found", "message_resolution": resolved_message, "sent": False}
            token = self.store.create_action_session(
                {
                    "action": "forward_email",
                    "payload": payload,
                    "recipient_slots": slots,
                    "message_resolution": resolved_message,
                },
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "message_resolution": resolved_message,
                "sent": False,
            }
        draft = self.client.forward_as_draft(
            item_id=str(resolved_message["item_id"]),
            change_key=resolved_message.get("change_key"),
            to=self._slot_addresses(slots, "to"),
            cc=self._slot_addresses(slots, "cc"),
            bcc=self._slot_addresses(slots, "bcc"),
            body_html=body_html,
        )
        notices = [str(slot["user_notice"]) for values in slots.values() for slot in values if slot.get("user_notice")]
        return {
            "status": "draft_created",
            "draft": self._draft_dict(draft),
            "source_message": resolved_message,
            "recipient_resolution": slots,
            "default_rule_notices": notices,
            "sent": False,
        }

    def _apply_people_selections(
        self,
        slots: dict[str, list[dict[str, Any]]],
        selections: dict[str, str],
    ) -> dict[str, list[dict[str, Any]]]:
        for field, values in slots.items():
            for slot in values:
                if slot.get("status") == "resolved":
                    continue
                query = str(slot.get("query") or "")
                selected_value = selections.get(query) or selections.get(f"{field}:{query}")
                if not selected_value:
                    continue
                chosen = None
                for candidate in slot.get("candidates") or []:
                    if selected_value in {candidate.get("person_ref"), candidate.get("email")}:
                        chosen = candidate
                        break
                if chosen is None:
                    raise ValueError(f"选择 {selected_value!r} 不属于查询 {query!r} 的候选。")
                slot.update(
                    {
                        "status": "resolved",
                        "email": chosen.get("email"),
                        "person_ref": chosen.get("person_ref"),
                        "selected": chosen,
                        "default_rule_applied": "user_confirmation",
                        "user_notice": None,
                    }
                )
        return slots

    def continue_action(self, *, resume_token: str, selections: dict[str, str]) -> dict[str, Any]:
        session = self.store.get_action_session(resume_token)
        if session["status"] not in {"pending", "needs_confirmation"}:
            raise ValueError(f"任务状态为 {session['status']}，不能继续。")
        state = session["state"]
        action = state.get("action")
        payload = state.get("payload") or {}

        if action == "compose_email":
            slots = self._apply_people_selections(state["recipient_slots"], selections)
            pending = self._pending_slots(slots)
            if pending:
                self.store.update_action_session(resume_token, state={**state, "recipient_slots": slots})
                return {"status": "needs_confirmation", "resume_token": resume_token, "pending": pending}
            result = self._execute_compose(payload, slots)
        elif action == "find_email":
            # Re-run with the selected SMTP address replacing the ambiguous person query.
            pending = state.get("person_resolution") or {}
            sender_query = payload.get("sender_query")
            participant_query = payload.get("participant_query")
            for field, slot in pending.items():
                query = str(slot.get("query") or "")
                selected_value = selections.get(query) or selections.get(field)
                if not selected_value:
                    continue
                chosen = next(
                    (
                        candidate
                        for candidate in slot.get("candidates") or []
                        if selected_value in {candidate.get("person_ref"), candidate.get("email")}
                    ),
                    None,
                )
                if chosen is None:
                    raise ValueError(f"人员选择无效：{selected_value!r}")
                if field == "sender":
                    sender_query = chosen.get("email")
                else:
                    participant_query = chosen.get("email")
            result = self.find_email(
                folders=payload.get("folders"),
                sender_query=sender_query,
                participant_query=participant_query,
                subject_contains=payload.get("subject_contains"),
                unread_only=payload.get("unread_only"),
                has_attachments=payload.get("has_attachments"),
                after=payload.get("after"),
                before=payload.get("before"),
                limit=int(payload.get("limit") or 20),
                offset=int(payload.get("offset") or 0),
                lookback_days=int(payload.get("lookback_days") or DEFAULT_HISTORY_DAYS),
            )
        elif action in {"reply_to_email", "forward_email"}:
            message_resolution = state.get("message_resolution") or {}

            # The source-mail search itself may be blocked by an ambiguous sender/participant.
            # Apply that user selection before asking for a message_ref.
            person_resolution = message_resolution.get("person_resolution") or {}
            for field, slot in person_resolution.items():
                if slot.get("status") == "resolved":
                    continue
                query = str(slot.get("query") or "")
                selected_value = selections.get(query) or selections.get(field)
                if not selected_value:
                    continue
                chosen = next(
                    (candidate for candidate in slot.get("candidates") or []
                     if selected_value in {candidate.get("person_ref"), candidate.get("email")}),
                    None,
                )
                if chosen is None:
                    raise ValueError(f"人员选择无效：{selected_value!r}")
                if field == "sender":
                    payload["sender_query"] = chosen.get("email")
                else:
                    payload["participant_query"] = chosen.get("email")

            message_ref = selections.get("message") or selections.get("message_ref")
            pending_messages = list(message_resolution.get("items") or [])
            if not message_ref and pending_messages:
                return {
                    "status": "needs_confirmation",
                    "resume_token": resume_token,
                    "pending_messages": pending_messages,
                }
            if message_ref and pending_messages:
                allowed_refs = {
                    str(item.get("message_ref"))
                    for item in pending_messages
                    if item.get("message_ref")
                }
                if str(message_ref) not in allowed_refs:
                    raise ValueError("所选 message_ref 不属于本次待确认的邮件候选。")
            if message_ref:
                payload["message_ref"] = message_ref
            if action == "reply_to_email":
                result = self.reply_to_email(**payload)
            else:
                slots = self._apply_people_selections(state.get("recipient_slots") or {}, selections)
                pending = self._pending_slots(slots)
                if pending:
                    return {"status": "needs_confirmation", "resume_token": resume_token, "pending": pending}
                # Use selected recipient emails directly so the second pass is deterministic.
                result = self.forward_email(
                    to_queries=self._slot_addresses(slots, "to"),
                    cc_queries=self._slot_addresses(slots, "cc"),
                    bcc_queries=self._slot_addresses(slots, "bcc"),
                    body_html=payload["body_html"],
                    lookback_days=int(payload.get("lookback_days") or DEFAULT_HISTORY_DAYS),
                    message_ref=payload.get("message_ref"),
                    folders=payload.get("folders"),
                    sender_query=payload.get("sender_query"),
                    participant_query=payload.get("participant_query"),
                    subject_contains=payload.get("subject_contains"),
                    after=payload.get("after"),
                    before=payload.get("before"),
                    limit=int(payload.get("limit") or 20),
                )
        else:
            raise ValueError(f"不支持的恢复任务类型：{action!r}")

        self.store.update_action_session(resume_token, status="completed", state={**state, "result": result})
        return {**result, "resumed_from": resume_token}
