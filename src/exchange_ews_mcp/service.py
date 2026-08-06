from __future__ import annotations

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
from .ews import EwsClient
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


def _attach_message_ref(item: dict[str, Any], store: ReferenceStore) -> dict[str, Any]:
    result = dict(item)
    item_id = item.get("item_id")
    if item_id:
        kind = "draft" if item.get("is_draft") is True else "message"
        ref = store.upsert_reference(
            kind=kind,
            external_key=str(item_id),
            payload=_message_payload(item),
            ttl_days=30 if kind == "draft" else 7,
        )
        result[f"{kind}_ref"] = ref
        if kind == "draft":
            result["message_ref"] = store.upsert_reference(
                kind="message",
                external_key=str(item_id),
                payload=_message_payload(item),
                ttl_days=7,
            )
    return result


def _draft_result(result: Any, store: ReferenceStore) -> dict[str, Any]:
    data = result.as_dict()
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
        expected_kind = "draft" if expected_draft else None
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
    return {**result, "items": [_attach_message_ref(item, store) for item in result["items"]]}


def search_emails(*, folders: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
    client = configured_client()
    if folders:
        kwargs.pop("folder", None)
        result = client.search_emails_multi_folder(folders=folders, **kwargs)
    else:
        result = client.search_emails(**kwargs)
    store = configured_store()
    return {**result, "items": [_attach_message_ref(item, store) for item in result["items"]]}


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
    return _attach_message_ref(result, configured_store())


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
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    lookback_days: int = 365,
) -> dict[str, Any]:
    return configured_workflow().find_email(
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        after=after,
        before=before,
        limit=limit,
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
    user_input: str,
    reference_materials: list[dict[str, str]] | None = None,
    subject_contains: str = "周报",
    folder: str = "sentitems",
    folders: list[str] | None = None,
    lookback_days: int = 60,
    max_reports: int = 5,
) -> dict[str, Any]:
    return configured_workflow().get_weekly_report_context(
        user_input=user_input,
        reference_materials=reference_materials,
        subject_contains=subject_contains,
        folder=folder,
        folders=folders,
        lookback_days=lookback_days,
        max_reports=max_reports,
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


def continue_action(*, resume_token: str, selections: dict[str, str]) -> dict[str, Any]:
    store = configured_store()
    session = store.get_action_session(resume_token)
    action = str((session.get("state") or {}).get("action") or "")
    if action in {"find_meeting_times", "schedule_meeting", "schedule_meeting_send_confirmation"}:
        return configured_calendar_workflow().continue_action(
            resume_token=resume_token, selections=selections
        )
    return configured_workflow().continue_action(
        resume_token=resume_token,
        selections=selections,
    )


def update_email_draft(**kwargs: Any) -> dict[str, Any]:
    """Semantic-layer name for updating an existing draft; behavior remains SaveOnly."""
    return update_draft(**kwargs)


def _calendar_result(item: dict[str, Any], store: ReferenceStore) -> dict[str, Any]:
    result = dict(item)
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
            },
            ttl_days=30,
        )
    return result


def _resolve_calendar_item(
    *,
    item_id: str | None = None,
    calendar_ref: str | None = None,
) -> tuple[str, str | None]:
    supplied = [value for value in (item_id, calendar_ref) if value]
    if len(supplied) != 1:
        raise ValueError("item_id、calendar_ref 必须且只能提供一个。")
    if item_id:
        return item_id.strip(), None
    stored = configured_store().get_reference(str(calendar_ref), expected_kind="calendar")
    return str(stored.payload["item_id"]), stored.payload.get("change_key")


def get_user_availability(
    *,
    attendees: list[dict[str, str]],
    start: str,
    end: str,
    interval_minutes: int = 30,
) -> dict[str, Any]:
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
    resolved_id, stored_key = _resolve_calendar_item(item_id=item_id, calendar_ref=calendar_ref)
    result = configured_client().get_calendar_item(
        item_id=resolved_id,
        change_key=change_key or stored_key,
    )
    config = load_config()
    return decorate_calendar_item(
        _calendar_result(result, configured_store()), config.calendar_time_zone
    )


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
