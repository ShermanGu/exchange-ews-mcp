from __future__ import annotations

from email.utils import parseaddr
from typing import Any

from .config import effective_company_domains, load_config
from .credentials import get_password
from .calendar_utils import (
    apply_current_user_working_hours_override,
    decorate_availability_result,
    decorate_calendar_item,
    decorate_time_range,
    format_utc,
    parse_input_datetime,
)
from .ews import AVAILABILITY_ATTENDEE_TYPES, EwsClient
from .state_store import ReferenceStore


def configured_client() -> EwsClient:
    config = load_config()
    password = get_password(config.username)
    return EwsClient(config, password)


def configured_store() -> ReferenceStore:
    return ReferenceStore()


def _message_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item.get("item_id"),
        "change_key": item.get("change_key"),
        "subject": item.get("subject"),
        "folder": item.get("folder"),
        "conversation_id": item.get("conversation_id"),
        "internet_message_id": item.get("internet_message_id"),
    }


def _attach_mail_ref(item: dict[str, Any], store: ReferenceStore) -> dict[str, Any]:
    """Attach exactly one typed Agent reference to a mail item.

    Drafts are intentionally *not* given a parallel message_ref.  A draft can be
    read through draft_ref and edited through edit_mail_draft, but it must never
    become an accidental source for reply/forward flows that require message_ref.
    """
    result = dict(item)
    item_id = str(item.get("item_id") or "").strip()
    if not item_id:
        return result
    folder = str(item.get("folder") or "").strip().casefold()
    kind = "draft" if item.get("is_draft") is True or folder == "drafts" else "message"
    ref = store.upsert_reference(
        kind=kind,
        external_key=item_id,
        payload=_message_payload(item),
        ttl_days=30 if kind == "draft" else 7,
    )
    result[f"{kind}_ref"] = ref
    result["reference_kind"] = kind
    if kind == "draft":
        result["update_tool"] = "edit_mail_draft"
    return result


def _draft_result(result: Any, store: ReferenceStore) -> dict[str, Any]:
    data = result.as_dict()
    data["reference_kind"] = "draft"
    data["update_tool"] = "edit_mail_draft"
    data["draft_ref"] = store.upsert_reference(
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


def _resolve_item(
    *,
    item_id: str | None = None,
    message_ref: str | None = None,
    draft_ref: str | None = None,
    expected_draft: bool = False,
) -> tuple[str, str | None]:
    supplied = [value for value in (item_id, message_ref, draft_ref) if value]
    if len(supplied) != 1:
        raise ValueError("item_id、message_ref、draft_ref 必须且只能提供一个。")
    if item_id:
        return item_id.strip(), None
    store = configured_store()
    if draft_ref:
        stored = store.get_reference(draft_ref, expected_kind="draft")
    else:
        # A value supplied through message_ref must actually be a message ref.
        # Do not accept draft/calendar/person references merely because they share
        # the same opaque string shape. expected_draft is retained for low-level
        # callers that intentionally route a message_ref-shaped parameter to a
        # draft-only operation.
        expected_kind = "draft" if expected_draft else "message"
        stored = store.get_reference(str(message_ref), expected_kind=expected_kind)
    return str(stored.payload["item_id"]), stored.payload.get("change_key")


def get_current_user() -> dict[str, Any]:
    config = load_config()
    result = configured_client().get_current_user()
    result["company_email_domains"] = effective_company_domains(config)
    email = result.get("primary_email")
    if email:
        result["person_ref"] = configured_store().upsert_reference(
            kind="person",
            external_key=str(email).casefold(),
            payload={
                "display_name": result.get("display_name"),
                "email": email,
                "source": result.get("source"),
            },
            ttl_days=30,
        )
    return result


def resolve_names(*, query: str, limit: int = 20) -> dict[str, Any]:
    result = configured_client().resolve_names(query=query, limit=limit)
    store = configured_store()
    candidates: list[dict[str, Any]] = []
    for candidate in result["candidates"]:
        item = dict(candidate)
        email = candidate.get("email")
        if email:
            item["person_ref"] = store.upsert_reference(
                kind="person",
                external_key=str(email).casefold(),
                payload={
                    "display_name": candidate.get("display_name"),
                    "email": email,
                    "routing_type": candidate.get("routing_type"),
                    "mailbox_type": candidate.get("mailbox_type"),
                },
                ttl_days=30,
            )
        candidates.append(item)
    return {**result, "candidates": candidates}


def create_draft(
    *,
    to: list[str],
    subject: str,
    body_html: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict[str, Any]:
    result = configured_client().create_draft(
        to=to, cc=cc, bcc=bcc, subject=subject, body_html=body_html
    )
    return _draft_result(result, configured_store())


def list_emails(**kwargs: Any) -> dict[str, Any]:
    result = configured_client().list_emails(**kwargs)
    store = configured_store()
    return {**result, "items": [_attach_mail_ref(item, store) for item in result["items"]]}


def search_emails(*, folders: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    client = configured_client()
    if folders:
        kwargs.pop("folder", None)
        result = client.search_emails_multi_folder(folders=folders, **kwargs)
    else:
        result = client.search_emails(**kwargs)
    store = configured_store()
    return {**result, "items": [_attach_mail_ref(item, store) for item in result["items"]]}


def get_email(
    *,
    item_id: str | None = None,
    message_ref: str | None = None,
    draft_ref: str | None = None,
    change_key: str | None = None,
    max_body_chars: int = 50000,
) -> dict[str, Any]:
    resolved_id, stored_key = _resolve_item(
        item_id=item_id, message_ref=message_ref, draft_ref=draft_ref
    )
    result = configured_client().get_email(
        item_id=resolved_id,
        change_key=change_key or stored_key,
        max_body_chars=max_body_chars,
    )
    return _attach_mail_ref(result, configured_store())


def reply_as_draft(
    *,
    body_html: str,
    reply_all: bool = False,
    item_id: str | None = None,
    message_ref: str | None = None,
    change_key: str | None = None,
) -> dict[str, Any]:
    resolved_id, stored_key = _resolve_item(item_id=item_id, message_ref=message_ref)
    result = configured_client().reply_as_draft(
        item_id=resolved_id,
        body_html=body_html,
        reply_all=reply_all,
        change_key=change_key or stored_key,
    )
    return _draft_result(result, configured_store())


def forward_as_draft(
    *,
    to: list[str],
    body_html: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    item_id: str | None = None,
    message_ref: str | None = None,
    change_key: str | None = None,
) -> dict[str, Any]:
    resolved_id, stored_key = _resolve_item(item_id=item_id, message_ref=message_ref)
    result = configured_client().forward_as_draft(
        item_id=resolved_id,
        to=to,
        cc=cc,
        bcc=bcc,
        body_html=body_html,
        change_key=change_key or stored_key,
    )
    return _draft_result(result, configured_store())


def update_draft(
    *,
    item_id: str | None = None,
    draft_ref: str | None = None,
    change_key: str | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
) -> dict[str, Any]:
    resolved_id, stored_key = _resolve_item(
        item_id=item_id, draft_ref=draft_ref, expected_draft=True
    )
    result = configured_client().update_draft(
        item_id=resolved_id,
        change_key=change_key or stored_key,
        subject=subject,
        body_html=body_html,
        to=to,
        cc=cc,
        bcc=bcc,
        importance=importance,
    )
    return _draft_result(result, configured_store())


def add_attachment_to_draft(
    *,
    file_path: str,
    item_id: str | None = None,
    draft_ref: str | None = None,
    change_key: str | None = None,
    attachment_name: str | None = None,
) -> dict[str, Any]:
    resolved_id, stored_key = _resolve_item(
        item_id=item_id, draft_ref=draft_ref, expected_draft=True
    )
    result = configured_client().add_attachment_to_draft(
        item_id=resolved_id,
        file_path=file_path,
        change_key=change_key or stored_key,
        attachment_name=attachment_name,
    )
    data = result.as_dict()
    data["draft_ref"] = configured_store().upsert_reference(
        kind="draft",
        external_key=result.root_item_id,
        payload={
            "item_id": result.root_item_id,
            "change_key": result.root_item_change_key,
            "folder": "drafts",
        },
        ttl_days=30,
    )
    return data


def configured_workflow():
    from .workflow import SemanticMailWorkflow

    return SemanticMailWorkflow(configured_client(), configured_store(), load_config())


def resolve_people(
    *,
    query: str,
    limit: int = 100,
    lookback_days: int = 365,
    auto_select: bool = True,
) -> dict[str, Any]:
    return configured_workflow().resolve_people(
        query=query,
        limit=limit,
        lookback_days=lookback_days,
        auto_select=auto_select,
    )


def compose_email(
    *,
    to_queries: list[str],
    subject: str,
    body_html: str,
    cc_queries: list[str] | None = None,
    bcc_queries: list[str] | None = None,
    attachments: list[str] | None = None,
    lookback_days: int = 365,
) -> dict[str, Any]:
    return configured_workflow().compose_email(
        to_queries=to_queries,
        cc_queries=cc_queries,
        bcc_queries=bcc_queries,
        subject=subject,
        body_html=body_html,
        attachments=attachments,
        lookback_days=lookback_days,
    )


def find_email(
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
    lookback_days: int = 365,
) -> dict[str, Any]:
    return configured_workflow().find_email(
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        unread_only=unread_only,
        has_attachments=has_attachments,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
        lookback_days=lookback_days,
    )


def reply_to_email(
    *,
    body_html: str,
    reply_all: bool = False,
    message_ref: str | None = None,
    folders: list[str] | None = None,
    sender_query: str | None = None,
    participant_query: str | None = None,
    subject_contains: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    lookback_days: int = 365,
) -> dict[str, Any]:
    return configured_workflow().reply_to_email(
        body_html=body_html,
        reply_all=reply_all,
        message_ref=message_ref,
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        after=after,
        before=before,
        limit=limit,
        lookback_days=lookback_days,
    )


def get_weekly_report_context(
    *,
    request: str,
    reference_materials: list[dict[str, str]] | None = None,
    subject_contains: str = "周报",
    folder: str = "sentitems",
    folders: list[str] | None = None,
    lookback_days: int = 60,
    max_reports: int = 3,
) -> dict[str, Any]:
    return configured_workflow().get_weekly_report_context(
        request=request,
        reference_materials=reference_materials,
        subject_contains=subject_contains,
        folder=folder,
        folders=folders,
        lookback_days=lookback_days,
        max_reports=max_reports,
    )


def weekly_report(
    *,
    request: str,
    reference_materials: list[dict[str, str]] | None = None,
    subject_contains: str = "周报",
    folder: str = "sentitems",
    folders: list[str] | None = None,
    lookback_days: int = 60,
) -> dict[str, Any]:
    """Agent-facing weekly-report entry point; Exchange read-only in step one."""
    return get_weekly_report_context(
        request=request,
        reference_materials=reference_materials,
        subject_contains=subject_contains,
        folder=folder,
        folders=folders,
        lookback_days=lookback_days,
        max_reports=3,
    )


def update_weekly_report(
    *,
    weekly_flow_token: str,
    changes: list[dict[str, Any]],
    subject: str | None = None,
) -> dict[str, Any]:
    return configured_workflow().update_weekly_report(
        weekly_flow_token=weekly_flow_token,
        changes=changes,
        subject=subject,
    )


def forward_email(
    *,
    to_queries: list[str],
    body_html: str,
    cc_queries: list[str] | None = None,
    bcc_queries: list[str] | None = None,
    message_ref: str | None = None,
    folders: list[str] | None = None,
    sender_query: str | None = None,
    participant_query: str | None = None,
    subject_contains: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    lookback_days: int = 365,
) -> dict[str, Any]:
    return configured_workflow().forward_email(
        to_queries=to_queries,
        cc_queries=cc_queries,
        bcc_queries=bcc_queries,
        body_html=body_html,
        message_ref=message_ref,
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        after=after,
        before=before,
        limit=limit,
        lookback_days=lookback_days,
    )


_MAIL_RESUMABLE_ACTIONS = frozenset({
    "compose_email",
    "find_email",
    "reply_to_email",
    "forward_email",
    "weekly_report_update",
})
_CALENDAR_RESUMABLE_ACTIONS = frozenset({
    "find_meeting_times",
    "schedule_meeting",
    "schedule_meeting_send_confirmation",
})


def continue_action(*, resume_token: str, selections: dict[str, Any]) -> dict[str, Any]:
    """Route a resume token only to the workflow that originally created it."""
    store = configured_store()
    session = store.get_action_session(resume_token)
    action = str((session.get("state") or {}).get("action") or "").strip()
    if action in _CALENDAR_RESUMABLE_ACTIONS:
        return configured_calendar_workflow().continue_action(
            resume_token=resume_token, selections=selections
        )
    if action in _MAIL_RESUMABLE_ACTIONS:
        return configured_workflow().continue_action(
            resume_token=resume_token,
            selections=selections,
        )
    raise ValueError(
        f"resume_token 对应未知或不支持的恢复任务类型：{action!r}。"
        "请重新执行产生该确认请求的工具。"
    )


def search_mail(
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
    lookback_days: int = 365,
) -> dict[str, Any]:
    """Unified semantic mail discovery for the compact Agent surface."""
    return find_email(
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        unread_only=unread_only,
        has_attachments=has_attachments,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
        lookback_days=lookback_days,
    )


def read_mail(
    *,
    message_ref: str | None = None,
    draft_ref: str | None = None,
    max_body_chars: int = 50000,
) -> dict[str, Any]:
    """Read a message or draft through an opaque Agent-facing reference."""
    return get_email(
        message_ref=message_ref,
        draft_ref=draft_ref,
        max_body_chars=max_body_chars,
    )


def save_mail_draft(
    *,
    mode: str,
    body_html: str,
    source_message_ref: str | None = None,
    to_queries: list[str] | None = None,
    cc_queries: list[str] | None = None,
    bcc_queries: list[str] | None = None,
    subject: str | None = None,
    attachments: list[str] | None = None,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """Create, reply to, or forward an email as an unsent draft."""
    operation = str(mode or "").strip().casefold()
    allowed = {"compose", "reply", "reply_all", "forward"}
    if operation not in allowed:
        raise ValueError("mode 必须是 compose、reply、reply_all 或 forward。")
    if not body_html.strip():
        raise ValueError("body_html 不能为空。")

    recipients_supplied = any(value for value in (to_queries, cc_queries, bcc_queries))
    if operation == "compose":
        if source_message_ref:
            raise ValueError("compose 模式不能提供 source_message_ref。")
        if not to_queries:
            raise ValueError("compose 模式必须提供至少一个 to_queries 收件人。")
        if not str(subject or "").strip():
            raise ValueError("compose 模式必须提供非空 subject。")
        result = compose_email(
            to_queries=to_queries,
            cc_queries=cc_queries,
            bcc_queries=bcc_queries,
            subject=str(subject),
            body_html=body_html,
            attachments=attachments,
            lookback_days=lookback_days,
        )
    else:
        if not source_message_ref:
            raise ValueError(f"{operation} 模式必须提供 source_message_ref。")
        if subject is not None:
            raise ValueError(f"{operation} 模式不接受 subject；创建草稿后使用 edit_mail_draft 修改。")
        if attachments:
            raise ValueError(f"{operation} 模式不直接添加附件；创建草稿后使用 edit_mail_draft。")
        if operation in {"reply", "reply_all"}:
            if recipients_supplied:
                raise ValueError(f"{operation} 模式不接受收件人参数。")
            result = reply_to_email(
                message_ref=source_message_ref,
                body_html=body_html,
                reply_all=operation == "reply_all",
                lookback_days=lookback_days,
            )
        else:
            if not to_queries:
                raise ValueError("forward 模式必须提供至少一个 to_queries 收件人。")
            result = forward_email(
                message_ref=source_message_ref,
                to_queries=to_queries,
                cc_queries=cc_queries,
                bcc_queries=bcc_queries,
                body_html=body_html,
                lookback_days=lookback_days,
            )
    return {**result, "mail_draft_mode": operation, "sent": False}

def _validate_agent_draft_ref(draft_ref: str) -> dict[str, Any] | None:
    """Validate reference kind before file preflight or any Exchange write."""
    normalized = str(draft_ref or "").strip()
    if not normalized:
        raise ValueError("draft_ref 不能为空。")
    stored = configured_store().get_reference(normalized)
    if stored.kind == "calendar":
        return {
            "status": "wrong_reference_type",
            "reference_kind": "calendar",
            "calendar_ref": normalized,
            "recommended_tool": "save_meeting",
            "message": (
                "该引用属于日历项目，未执行邮件草稿更新。"
                "请使用 save_meeting(calendar_ref=...) 修改会议。"
            ),
        }
    if stored.kind != "draft":
        raise ValueError(
            f"引用 {normalized} 的类型是 {stored.kind}，不是 draft。"
        )
    return None


def edit_mail_draft(
    *,
    draft_ref: str,
    subject: str | None = None,
    body_html: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Update one draft and optionally append allow-listed local files."""
    wrong_reference = _validate_agent_draft_ref(draft_ref)
    if wrong_reference is not None:
        return {**wrong_reference, "sent": False}

    update_requested = any(
        value is not None for value in (subject, body_html, to, cc, bcc, importance)
    )
    attachment_values = list(attachments or [])
    if not update_requested and not attachment_values:
        raise ValueError("至少需要提供一个草稿字段或附件。")
    if len(attachment_values) > 20:
        raise ValueError("attachments 最多允许 20 个文件。")

    # Validate every path before the first Exchange write so a bad later path
    # cannot leave the draft partially modified.
    validator = configured_client()
    canonical_attachments = [
        validator.validate_attachment_path(path) for path in attachment_values
    ]

    draft_update: dict[str, Any] | None = None
    if update_requested:
        draft_update = update_email_draft(
            draft_ref=draft_ref,
            subject=subject,
            body_html=body_html,
            to=to,
            cc=cc,
            bcc=bcc,
            importance=importance,
        )
        if draft_update.get("status") == "wrong_reference_type":
            return draft_update

    attachment_results: list[dict[str, Any]] = []
    current_ref = draft_ref
    for path in canonical_attachments:
        attached = add_attachment_to_draft(draft_ref=current_ref, file_path=path)
        current_ref = str(attached.get("draft_ref") or current_ref)
        attachment_results.append(attached)

    return {
        "status": "draft_updated",
        "draft_ref": current_ref,
        "draft": draft_update,
        "attachments": attachment_results,
        "sent": False,
    }


def update_email_draft(**kwargs: Any) -> dict[str, Any]:
    """Update an email draft only; calendar references are routed back safely."""
    draft_ref = str(kwargs.get("draft_ref") or "").strip()
    if draft_ref:
        wrong_reference = _validate_agent_draft_ref(draft_ref)
        if wrong_reference is not None:
            return wrong_reference
    return update_draft(**kwargs)


def _calendar_attendee_values(item: dict[str, Any], field: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for attendee in item.get(field) or []:
        if isinstance(attendee, dict):
            email = str(attendee.get("email") or "").strip()
        else:
            email = str(attendee or "").strip()
        lowered = email.casefold()
        if email and lowered not in seen:
            values.append(email)
            seen.add(lowered)
    return values


def _calendar_result(item: dict[str, Any], store: ReferenceStore) -> dict[str, Any]:
    result = dict(item)
    required = _calendar_attendee_values(item, "required_attendees")
    optional = _calendar_attendee_values(item, "optional_attendees")
    meeting_evidence = item.get("is_meeting") is True or bool(required or optional)
    result["reference_kind"] = "calendar"
    if meeting_evidence:
        result["update_tool"] = "save_meeting"
        result["send_tool"] = "send_meeting_invitation"
    item_id = str(item.get("item_id") or "").strip()
    if item_id:
        result["calendar_ref"] = store.upsert_reference(
            kind="calendar",
            external_key=item_id,
            payload={
                "item_id": item_id,
                "change_key": item.get("change_key"),
                "subject": item.get("subject"),
                "start": item.get("start"),
                "end": item.get("end"),
                "folder": "calendar",
                "item_kind": "meeting" if meeting_evidence else "calendar_item",
                "is_meeting": item.get("is_meeting"),
                "meeting_request_was_sent": item.get("meeting_request_was_sent"),
                "required_attendees": required,
                "optional_attendees": optional,
            },
            ttl_days=30,
        )
    return result


def _resolve_calendar_item(
    *,
    item_id: str | None = None,
    calendar_ref: str | None = None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    supplied = [value for value in (item_id, calendar_ref) if value]
    if len(supplied) != 1:
        raise ValueError("item_id、calendar_ref 必须且只能提供一个。")
    if item_id:
        return item_id.strip(), None, None
    stored = configured_store().get_reference(str(calendar_ref), expected_kind="calendar")
    return (
        str(stored.payload["item_id"]),
        stored.payload.get("change_key"),
        dict(stored.payload),
    )


def get_user_availability(
    *,
    attendees: list[dict[str, str]],
    start: str,
    end: str,
    interval_minutes: int = 30,
) -> dict[str, Any]:
    if not attendees:
        raise ValueError("attendees 不能为空。")
    for index, raw in enumerate(attendees):
        if not isinstance(raw, dict):
            raise ValueError(f"attendees[{index}] 必须是包含 email 的对象。")
        attendee_type = str(raw.get("attendee_type") or "Required").strip()
        if attendee_type not in AVAILABILITY_ATTENDEE_TYPES:
            allowed = ", ".join(sorted(AVAILABILITY_ATTENDEE_TYPES))
            raise ValueError(
                f"不支持的 attendee_type：{attendee_type!r}。仅支持 {allowed}；"
                "会议室/资源邮箱忙闲查询未启用。"
            )
    config = load_config()
    start_dt = parse_input_datetime(start, config.calendar_time_zone, "start")
    end_dt = parse_input_datetime(end, config.calendar_time_zone, "end")
    if end_dt <= start_dt:
        raise ValueError("end 必须晚于 start。")
    result = configured_client().get_user_availability(
        attendees=attendees,
        start=format_utc(start_dt),
        end=format_utc(end_dt),
        interval_minutes=interval_minutes,
    )
    result = apply_current_user_working_hours_override(
        result,
        current_user_email=config.primary_email,
        zone_name=config.calendar_time_zone,
        workday_start=config.calendar_workday_start,
        workday_end=config.calendar_workday_end,
        workdays=config.calendar_workdays or [0, 1, 2, 3, 4],
    )
    return decorate_availability_result(result, config.calendar_time_zone)


def list_calendar_events(*, start: str, end: str, limit: int = 100) -> dict[str, Any]:
    config = load_config()
    start_dt = parse_input_datetime(start, config.calendar_time_zone, "start")
    end_dt = parse_input_datetime(end, config.calendar_time_zone, "end")
    if end_dt <= start_dt:
        raise ValueError("end 必须晚于 start。")
    result = configured_client().list_calendar_events(
        start=format_utc(start_dt), end=format_utc(end_dt), limit=limit
    )
    store = configured_store()
    decorated = decorate_time_range(
        result, start_key="start", end_key="end", zone_name=config.calendar_time_zone
    )
    decorated["items"] = [
        decorate_calendar_item(_calendar_result(item, store), config.calendar_time_zone)
        for item in result["items"]
    ]
    return decorated


def get_calendar_item(
    *,
    item_id: str | None = None,
    calendar_ref: str | None = None,
    change_key: str | None = None,
) -> dict[str, Any]:
    resolved_id, _, _ = _resolve_calendar_item(item_id=item_id, calendar_ref=calendar_ref)
    result = configured_client().get_calendar_item(
        item_id=resolved_id,
        # ItemId alone always resolves the current server-side ChangeKey.  This
        # keeps calendar_ref usable after a user edits the item in Outlook.
        change_key=change_key,
    )
    config = load_config()
    return decorate_calendar_item(
        _calendar_result(result, configured_store()), config.calendar_time_zone
    )


def _attendee_emails(item: dict[str, Any], field: str) -> list[str]:
    result: list[str] = []
    for attendee in item.get(field) or []:
        if isinstance(attendee, dict):
            email = str(attendee.get("email") or "").strip()
        else:
            email = str(attendee or "").strip()
        if email:
            result.append(email)
    return result


def _current_unsent_meeting(
    *,
    item_id: str,
    reference_payload: dict[str, Any] | None = None,
) -> tuple[EwsClient, dict[str, Any]]:
    client = configured_client()
    current = client.get_calendar_item(item_id=item_id)
    if current.get("is_cancelled") is True:
        raise ValueError("目标会议已取消，不能继续修改或发送。")

    reference_payload = reference_payload or {}
    current_required = _attendee_emails(current, "required_attendees")
    current_optional = _attendee_emails(current, "optional_attendees")
    hinted_required = _calendar_attendee_values(reference_payload, "required_attendees")
    hinted_optional = _calendar_attendee_values(reference_payload, "optional_attendees")
    known_mcp_meeting = (
        reference_payload.get("item_kind") == "meeting"
        or reference_payload.get("is_meeting") is True
    )
    has_attendee_evidence = bool(
        current_required or current_optional or hinted_required or hinted_optional
    )

    # Some on-premises Exchange builds report IsMeeting=false for a CalendarItem
    # saved with SendToNone even though attendee collections are still present.
    # Microsoft defines a calendar item with attendees as a meeting, so do not
    # reject a valid unsent meeting based on that single inconsistent flag.
    if current.get("is_meeting") is not True and not (
        has_attendee_evidence or known_mcp_meeting
    ):
        raise ValueError("目标日历项目不是会议，且未发现任何参会人。")

    if not current_required and hinted_required:
        current["required_attendees"] = [{"email": value} for value in hinted_required]
    if not current_optional and hinted_optional:
        current["optional_attendees"] = [{"email": value} for value in hinted_optional]
    current["is_meeting"] = True

    if current.get("meeting_request_was_sent") is True:
        raise ValueError("该会议邀请已经发送；当前工具只处理尚未发送的会议。")
    if not current.get("change_key"):
        raise ValueError("Exchange 未返回会议当前 ChangeKey。")
    return client, current


def update_meeting(
    *,
    item_id: str | None = None,
    calendar_ref: str | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    required_attendees: list[str] | None = None,
    optional_attendees: list[str] | None = None,
    reminder_minutes: int | None = None,
) -> dict[str, Any]:
    """Update an existing unsent meeting without notifying attendees."""

    if all(
        value is None
        for value in (
            subject,
            body_html,
            start,
            end,
            location,
            required_attendees,
            optional_attendees,
            reminder_minutes,
        )
    ):
        raise ValueError("至少需要提供一个待更新字段。")
    resolved_id, _, reference_payload = _resolve_calendar_item(
        item_id=item_id, calendar_ref=calendar_ref
    )
    client, current = _current_unsent_meeting(
        item_id=resolved_id, reference_payload=reference_payload
    )

    config = load_config()
    start_value = end_value = None
    if bool(start) != bool(end):
        raise ValueError("start 和 end 必须同时提供。")
    if start is not None and end is not None:
        start_dt = parse_input_datetime(start, config.calendar_time_zone, "start")
        end_dt = parse_input_datetime(end, config.calendar_time_zone, "end")
        if end_dt <= start_dt:
            raise ValueError("end 必须晚于 start。")
        start_value, end_value = format_utc(start_dt), format_utc(end_dt)

    effective_required = (
        required_attendees
        if required_attendees is not None
        else _attendee_emails(current, "required_attendees")
    )
    effective_optional = (
        optional_attendees
        if optional_attendees is not None
        else _attendee_emails(current, "optional_attendees")
    )
    if not effective_required and not effective_optional:
        raise ValueError("会议至少需要一个 required 或 optional attendee。")

    updated = client.update_meeting(
        item_id=resolved_id,
        change_key=str(current["change_key"]),
        subject=subject,
        body_html=body_html,
        start=start_value,
        end=end_value,
        location=location,
        required_attendees=required_attendees,
        optional_attendees=optional_attendees,
        reminder_minutes=reminder_minutes,
        send_invitations=False,
    )
    refreshed = client.get_calendar_item(item_id=str(updated["item_id"]))
    refreshed["sent"] = False
    return {
        "status": "meeting_updated_not_sent",
        "calendar_item": decorate_calendar_item(
            _calendar_result(refreshed, configured_store()), config.calendar_time_zone
        ),
        "sent": False,
    }


def send_meeting_invitation(
    *,
    item_id: str | None = None,
    calendar_ref: str | None = None,
    confirm_send: bool = False,
) -> dict[str, Any]:
    """Send invitations for one existing unsent meeting after explicit confirmation."""

    if not confirm_send:
        raise ValueError("发送会议邀请前必须显式设置 confirm_send=true。")
    resolved_id, _, reference_payload = _resolve_calendar_item(
        item_id=item_id, calendar_ref=calendar_ref
    )
    client, current = _current_unsent_meeting(
        item_id=resolved_id, reference_payload=reference_payload
    )
    required = _attendee_emails(current, "required_attendees")
    optional = _attendee_emails(current, "optional_attendees")
    if not required and not optional:
        raise ValueError("会议没有参会人，无法发送邀请。")
    subject = str(current.get("subject") or "").strip()
    if not subject:
        raise ValueError("会议主题为空，无法发送邀请。")

    sent_result = client.send_meeting_invitation(
        item_id=resolved_id,
        change_key=str(current["change_key"]),
        subject=subject,
    )
    refreshed = client.get_calendar_item(item_id=str(sent_result["item_id"]))
    refreshed["meeting_request_was_sent"] = True
    refreshed["sent"] = True
    return {
        "status": "meeting_invitation_sent",
        "calendar_item": decorate_calendar_item(
            _calendar_result(refreshed, configured_store()), load_config().calendar_time_zone
        ),
        "sent": True,
    }


def create_meeting(
    *,
    subject: str,
    body_html: str,
    start: str,
    end: str,
    required_attendees: list[str],
    optional_attendees: list[str] | None = None,
    location: str | None = None,
    reminder_minutes: int = 15,
    send_invitations: bool = False,
    confirm_send: bool = False,
) -> dict[str, Any]:
    if send_invitations and not confirm_send:
        raise ValueError(
            "send_invitations=true 会向参会人发送邀请；请同时显式设置 confirm_send=true。"
        )
    config = load_config()
    start_dt = parse_input_datetime(start, config.calendar_time_zone, "start")
    end_dt = parse_input_datetime(end, config.calendar_time_zone, "end")
    if end_dt <= start_dt:
        raise ValueError("end 必须晚于 start。")
    result = configured_client().create_meeting(
        subject=subject,
        body_html=body_html,
        start=format_utc(start_dt),
        end=format_utc(end_dt),
        required_attendees=required_attendees,
        optional_attendees=optional_attendees,
        location=location,
        reminder_minutes=reminder_minutes,
        send_invitations=send_invitations,
    )
    return decorate_calendar_item(
        _calendar_result(result.as_dict(), configured_store()), config.calendar_time_zone
    )


def configured_calendar_workflow():
    from .calendar_workflow import CalendarWorkflow
    from .workflow import SemanticMailWorkflow

    client = configured_client()
    store = configured_store()
    config = load_config()
    return CalendarWorkflow(client, store, config, SemanticMailWorkflow(client, store, config))


def find_meeting_times(**kwargs: Any) -> dict[str, Any]:
    return configured_calendar_workflow().find_meeting_times(**kwargs)


def schedule_meeting(**kwargs: Any) -> dict[str, Any]:
    return configured_calendar_workflow().schedule_meeting(**kwargs)


def read_calendar(
    *,
    start: str | None = None,
    end: str | None = None,
    calendar_ref: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List a time window or read one item through a calendar reference."""
    if calendar_ref:
        if start is not None or end is not None:
            raise ValueError("提供 calendar_ref 时不能同时提供 start/end。")
        return {"read_mode": "item", **get_calendar_item(calendar_ref=calendar_ref)}
    if start is None or end is None:
        raise ValueError("未提供 calendar_ref 时必须同时提供 start 和 end。")
    return {
        "read_mode": "window",
        **list_calendar_events(start=start, end=end, limit=limit),
    }


def _full_email(value: str) -> str | None:
    raw = str(value or "").strip()
    _, parsed = parseaddr(raw)
    return parsed if parsed and "@" in parsed and parsed == raw else None


def save_meeting(
    *,
    calendar_ref: str | None = None,
    attendee_queries: list[str] | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    optional_attendees: list[str] | None = None,
    check_availability: bool = True,
    reminder_minutes: int | None = None,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """Create or update a meeting while always keeping invitations unsent."""
    attendees = list(attendee_queries or [])
    optional = list(optional_attendees or [])
    if calendar_ref:
        if attendees:
            invalid = [value for value in attendees if _full_email(value) is None]
            if invalid:
                raise ValueError(
                    "更新会议参会人时 attendee_queries 只接受完整邮箱："
                    + ", ".join(invalid)
                )
        invalid_optional = [value for value in optional if _full_email(value) is None]
        if invalid_optional:
            raise ValueError(
                "更新会议时 optional_attendees 只接受完整邮箱："
                + ", ".join(invalid_optional)
            )
        return update_meeting(
            calendar_ref=calendar_ref,
            subject=subject,
            body_html=body_html,
            start=start,
            end=end,
            location=location,
            required_attendees=attendees if attendee_queries is not None else None,
            optional_attendees=optional if optional_attendees is not None else None,
            reminder_minutes=reminder_minutes,
        )

    if not attendees:
        raise ValueError("创建会议时必须提供至少一个 attendee_queries 参会人。")
    if not str(subject or "").strip():
        raise ValueError("创建会议时必须提供非空 subject。")
    if not str(body_html or "").strip():
        raise ValueError("创建会议时必须提供非空 body_html。")
    if start is None or end is None:
        raise ValueError(
            "创建会议时必须提供确定的 start/end；如需查找时间，请先调用 find_meeting_times。"
        )
    return schedule_meeting(
        attendee_queries=attendees,
        subject=str(subject),
        body_html=str(body_html),
        start=start,
        end=end,
        location=location,
        optional_attendees=optional,
        include_self_in_availability=True,
        respect_attendee_working_hours=True,
        check_availability=check_availability,
        send_invitations=False,
        confirm_send=False,
        reminder_minutes=15 if reminder_minutes is None else reminder_minutes,
        lookback_days=lookback_days,
    )
