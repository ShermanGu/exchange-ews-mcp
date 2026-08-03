from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

from . import __version__
from requests import Response, Session
from requests_ntlm import HttpNtlmAuth

from .config import AppConfig
from .errors import EwsError
from .input_normalization import (
    CANONICAL_MAIL_FOLDERS,
    normalize_attendee_type,
    normalize_importance,
    normalize_mail_folder,
    normalize_mail_folders,
)
from .xml_builder import (
    MESSAGES_NS,
    TYPES_NS,
    SearchCriteria,
    build_create_attachment_request,
    build_create_draft_request,
    build_create_meeting_request,
    build_delete_calendar_item_request,
    build_find_calendar_items_request,
    build_find_items_request,
    build_forward_draft_request,
    build_get_inbox_request,
    build_get_attachments_request,
    build_get_calendar_item_request,
    build_get_item_identity_request,
    build_get_user_availability_request,
    build_get_item_request,
    build_reply_draft_request,
    build_resolve_names_request,
    build_update_draft_request,
    q,
)

ALLOWED_FOLDERS = set(CANONICAL_MAIL_FOLDERS)
MAX_PAGE_SIZE = 100
RESOLVE_SCOPES = {
    "ActiveDirectory",
    "ActiveDirectoryContacts",
    "Contacts",
    "ContactsActiveDirectory",
}
DEFAULT_RESOLVE_SCOPE = "ContactsActiveDirectory"

# Outlook/OWA shows localized address-book names such as "Contacts" and
# "全球通讯簿". EWS does not accept those display labels directly; it expects
# ResolveNamesSearchScopeType enum values. These aliases are only for CLI and
# CLI compatibility only. The MCP tool always uses DEFAULT_RESOLVE_SCOPE.
RESOLVE_SCOPE_ALIASES = {
    "contacts": "Contacts",
    "contact": "Contacts",
    "个人联系人": "Contacts",
    "联系人": "Contacts",
    "global": "ActiveDirectory",
    "gal": "ActiveDirectory",
    "globaladdresslist": "ActiveDirectory",
    "全球通讯簿": "ActiveDirectory",
    "全局地址簿": "ActiveDirectory",
    "activedirectory": "ActiveDirectory",
    "directory": "ActiveDirectory",
    "both": DEFAULT_RESOLVE_SCOPE,
    "all": DEFAULT_RESOLVE_SCOPE,
    "contactsactivedirectory": DEFAULT_RESOLVE_SCOPE,
    "contactsfirst": DEFAULT_RESOLVE_SCOPE,
    "activedirectorycontacts": "ActiveDirectoryContacts",
    "directoryfirst": "ActiveDirectoryContacts",
}


def normalize_resolve_scope(value: str | None) -> str:
    """Convert a friendly/localized source name to an EWS SearchScope enum."""
    if value is None or not value.strip():
        return DEFAULT_RESOLVE_SCOPE
    raw = value.strip()
    if raw in RESOLVE_SCOPES:
        return raw
    key = re.sub(r"[\s_\-]+", "", raw).casefold()
    normalized = RESOLVE_SCOPE_ALIASES.get(key)
    if normalized is None:
        allowed = "both, contacts, global/GAL, " + ", ".join(sorted(RESOLVE_SCOPES))
        raise ValueError(f"search_scope 不支持：{value!r}。允许值：{allowed}")
    return normalized



def _merge_person_candidates(
    groups: list[tuple[str, list[dict[str, Any]]]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for source, candidates in groups:
        for raw in candidates:
            candidate = dict(raw)
            email = str(candidate.get("email") or "").strip()
            display = str(candidate.get("display_name") or "").strip()
            key = email.casefold() if email else f"name:{display.casefold()}"
            if not key or key == "name:":
                continue
            existing = by_key.get(key)
            if existing is None:
                if len(merged) >= limit:
                    continue
                candidate["source"] = source
                candidate["sources"] = [source]
                by_key[key] = candidate
                merged.append(candidate)
            else:
                sources = existing.setdefault("sources", [])
                if source not in sources:
                    sources.append(source)
                for field in (
                    "display_name", "email", "routing_type", "mailbox_type",
                    "title", "department", "company_name", "persona_id", "contact",
                ):
                    if not existing.get(field) and candidate.get(field):
                        existing[field] = candidate[field]
    return merged


@dataclass(frozen=True)
class DraftResult:
    item_id: str
    change_key: str | None
    subject: str | None = None
    to: list[str] | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None
    draft_type: str = "new"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "created",
            "folder": "drafts",
            "draft_type": self.draft_type,
            "item_id": self.item_id,
            "change_key": self.change_key,
            "subject": self.subject,
            "to": self.to or [],
            "cc": self.cc or [],
            "bcc": self.bcc or [],
        }


@dataclass(frozen=True)
class CalendarItemResult:
    item_id: str
    change_key: str | None
    subject: str
    start: str
    end: str
    required_attendees: list[str]
    optional_attendees: list[str]
    location: str | None = None
    sent: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "created",
            "folder": "calendar",
            "item_id": self.item_id,
            "change_key": self.change_key,
            "subject": self.subject,
            "start": self.start,
            "end": self.end,
            "location": self.location,
            "required_attendees": self.required_attendees,
            "optional_attendees": self.optional_attendees,
            "sent": self.sent,
        }


@dataclass(frozen=True)
class AttachmentResult:
    attachment_id: str
    root_item_id: str
    root_item_change_key: str | None
    filename: str
    size: int
    content_type: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "attached",
            "attachment_id": self.attachment_id,
            "draft_item_id": self.root_item_id,
            "draft_change_key": self.root_item_change_key,
            "filename": self.filename,
            "size": self.size,
            "content_type": self.content_type,
        }


@dataclass(frozen=True)
class AttachmentContent:
    attachment_id: str
    attachment_type: str
    filename: str | None
    content_type: str | None
    size: int | None
    is_inline: bool
    content_id: str | None
    content: bytes | None

    def metadata(self) -> dict[str, object]:
        return {
            "attachment_id": self.attachment_id,
            "type": self.attachment_type,
            "name": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "is_inline": self.is_inline,
            "content_id": self.content_id,
        }


def _validate_addresses(addresses: list[str], field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        value = raw.strip()
        _, parsed = parseaddr(value)
        if not parsed or "@" not in parsed:
            raise ValueError(f"{field_name} 中包含无效邮箱地址：{raw!r}")
        lowered = parsed.lower()
        if lowered not in seen:
            normalized.append(parsed)
            seen.add(lowered)
    return normalized


def _validate_folder(folder: str) -> str:
    """Backward-compatible wrapper for the shared Agent input normalizer."""
    return normalize_mail_folder(folder)


def _validate_page(limit: int, offset: int) -> tuple[int, int]:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit 必须在 1 到 {MAX_PAGE_SIZE} 之间。")
    if offset < 0:
        raise ValueError("offset 不能小于 0。")
    return limit, offset


def _normalize_iso_datetime(value: str | None, field_name: str) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            parsed = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} 必须是 ISO 8601 日期时间，例如 2026-07-28T00:00:00+09:00。"
        ) from exc
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(parent: ET.Element, name: str) -> str | None:
    element = parent.find(q(TYPES_NS, name))
    return element.text if element is not None else None


def _id_attr(parent: ET.Element, name: str) -> str | None:
    element = parent.find(q(TYPES_NS, name))
    return element.attrib.get("Id") if element is not None else None


def _bool_text(parent: ET.Element, name: str) -> bool | None:
    value = _text(parent, name)
    if value is None:
        return None
    return value.lower() == "true"


def _int_text(parent: ET.Element, name: str) -> int | None:
    value = _text(parent, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _mailbox(parent: ET.Element, name: str) -> dict[str, str | None] | None:
    container = parent.find(q(TYPES_NS, name))
    if container is None:
        return None
    mailbox = container.find(q(TYPES_NS, "Mailbox"))
    if mailbox is None:
        mailbox = container
    display_name = _text(mailbox, "Name")
    address = _text(mailbox, "EmailAddress")
    if display_name is None and address is None:
        return None
    return {
        "name": display_name,
        "email": address,
        "routing_type": _text(mailbox, "RoutingType"),
        "mailbox_type": _text(mailbox, "MailboxType"),
    }


def _mailboxes(parent: ET.Element, name: str) -> list[dict[str, str | None]]:
    container = parent.find(q(TYPES_NS, name))
    if container is None:
        return []
    result: list[dict[str, str | None]] = []
    for mailbox in container.findall(q(TYPES_NS, "Mailbox")):
        result.append(
            {
                "name": _text(mailbox, "Name"),
                "email": _text(mailbox, "EmailAddress"),
                "routing_type": _text(mailbox, "RoutingType"),
                "mailbox_type": _text(mailbox, "MailboxType"),
            }
        )
    return result


def _parse_message_summary(message: ET.Element) -> dict[str, Any]:
    item_id = message.find(q(TYPES_NS, "ItemId"))
    return {
        "item_id": item_id.attrib.get("Id") if item_id is not None else None,
        "change_key": item_id.attrib.get("ChangeKey") if item_id is not None else None,
        "subject": _text(message, "Subject") or "",
        "from": _mailbox(message, "From"),
        "sender": _mailbox(message, "Sender"),
        "display_to": _text(message, "DisplayTo") or "",
        "display_cc": _text(message, "DisplayCc") or "",
        "conversation_id": _id_attr(message, "ConversationId"),
        "parent_folder_id": _id_attr(message, "ParentFolderId"),
        "received_at": _text(message, "DateTimeReceived"),
        "sent_at": _text(message, "DateTimeSent"),
        "created_at": _text(message, "DateTimeCreated"),
        "last_modified_at": _text(message, "LastModifiedTime"),
        "is_read": _bool_text(message, "IsRead"),
        "is_draft": _bool_text(message, "IsDraft"),
        "has_attachments": _bool_text(message, "HasAttachments") or False,
        "importance": _text(message, "Importance"),
        "size": _int_text(message, "Size"),
        "internet_message_id": _text(message, "InternetMessageId"),
    }


def _parse_attachment_metadata(message: ET.Element) -> list[dict[str, Any]]:
    container = message.find(q(TYPES_NS, "Attachments"))
    if container is None:
        return []
    result: list[dict[str, Any]] = []
    for attachment in list(container):
        attachment_id = attachment.find(q(TYPES_NS, "AttachmentId"))
        result.append(
            {
                "type": attachment.tag.rsplit("}", 1)[-1],
                "attachment_id": attachment_id.attrib.get("Id") if attachment_id is not None else None,
                "name": _text(attachment, "Name"),
                "content_type": _text(attachment, "ContentType"),
                "size": _int_text(attachment, "Size"),
                "is_inline": _bool_text(attachment, "IsInline"),
                "content_id": _text(attachment, "ContentId"),
            }
        )
    return result


def _parse_attendees(parent: ET.Element, name: str) -> list[dict[str, Any]]:
    container = parent.find(q(TYPES_NS, name))
    if container is None:
        return []
    result: list[dict[str, Any]] = []
    for attendee in container.findall(q(TYPES_NS, "Attendee")):
        mailbox = attendee.find(q(TYPES_NS, "Mailbox"))
        if mailbox is None:
            continue
        result.append({
            "name": _text(mailbox, "Name"),
            "email": _text(mailbox, "EmailAddress"),
            "routing_type": _text(mailbox, "RoutingType"),
            "response_type": _text(attendee, "ResponseType"),
            "last_response_time": _text(attendee, "LastResponseTime"),
        })
    return result


def _parse_calendar_item(item: ET.Element, *, include_body: bool = False) -> dict[str, Any]:
    item_id = item.find(q(TYPES_NS, "ItemId"))
    organizer = _mailbox(item, "Organizer")
    result: dict[str, Any] = {
        "item_id": item_id.attrib.get("Id") if item_id is not None else None,
        "change_key": item_id.attrib.get("ChangeKey") if item_id is not None else None,
        "subject": _text(item, "Subject") or "",
        "start": _text(item, "Start"),
        "end": _text(item, "End"),
        "location": _text(item, "Location"),
        "is_meeting": _bool_text(item, "IsMeeting"),
        "is_all_day_event": _bool_text(item, "IsAllDayEvent"),
        "legacy_free_busy_status": _text(item, "LegacyFreeBusyStatus"),
        "organizer": organizer,
        "required_attendees": _parse_attendees(item, "RequiredAttendees"),
        "optional_attendees": _parse_attendees(item, "OptionalAttendees"),
        "created_at": _text(item, "DateTimeCreated"),
        "last_modified_at": _text(item, "LastModifiedTime"),
    }
    if include_body:
        body = item.find(q(TYPES_NS, "Body"))
        result["body_type"] = body.attrib.get("BodyType") if body is not None else None
        result["body_html"] = body.text if body is not None and body.text else ""
    return result


def _parse_timezone_transition(parent: ET.Element, name: str) -> dict[str, Any] | None:
    """Parse one EWS legacy time-zone transition, hiding zero placeholders.

    Some Exchange servers serialize a no-DST zone with StandardTime and
    DaylightTime elements whose month/day order are both zero.  Those values are
    sentinels, not real Sunday-at-midnight transitions, so expose them as null.
    """
    element = parent.find(q(TYPES_NS, name))
    if element is None:
        return None
    month = _int_text(element, "Month")
    day_order = _int_text(element, "DayOrder")
    day_of_week = _text(element, "DayOfWeek")
    transition_time = _text(element, "Time")
    valid_days = {
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    }
    if not (1 <= month <= 12 and day_order != 0 and day_of_week in valid_days and transition_time):
        return None
    return {
        "bias_minutes": _int_text(element, "Bias"),
        "time": transition_time,
        "day_order": day_order,
        "month": month,
        "day_of_week": day_of_week,
    }


def _format_utc_offset_from_ews_bias(bias_minutes: int) -> str:
    # EWS Bias means UTC = local + bias, so the conventional UTC offset has
    # the opposite sign.  Example: Bias=-480 means UTC+08:00.
    offset_minutes = -int(bias_minutes)
    sign = "+" if offset_minutes >= 0 else "-"
    absolute = abs(offset_minutes)
    hours, minutes = divmod(absolute, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _format_minutes_as_hhmm(value: int) -> str:
    minutes = int(value)
    if minutes == 1440:
        return "24:00"
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}"


def _parse_working_hours(free_busy_view: ET.Element) -> dict[str, Any] | None:
    working = free_busy_view.find(q(TYPES_NS, "WorkingHours"))
    if working is None:
        return None
    zone = working.find(q(TYPES_NS, "TimeZone"))
    periods: list[dict[str, Any]] = []
    array = working.find(q(TYPES_NS, "WorkingPeriodArray"))
    if array is not None:
        for period in array.findall(q(TYPES_NS, "WorkingPeriod")):
            start_minutes = _int_text(period, "StartTimeInMinutes")
            end_minutes = _int_text(period, "EndTimeInMinutes")
            periods.append({
                "days": str(_text(period, "DayOfWeek") or "").split(),
                "start_minutes": start_minutes,
                "end_minutes": end_minutes,
                "start": _format_minutes_as_hhmm(start_minutes),
                "end": _format_minutes_as_hhmm(end_minutes),
            })

    parsed_zone: dict[str, Any] | None = None
    if zone is not None:
        base_bias = _int_text(zone, "Bias")
        standard = _parse_timezone_transition(zone, "StandardTime")
        daylight = _parse_timezone_transition(zone, "DaylightTime")
        standard_extra = int((standard or {}).get("bias_minutes") or 0)
        daylight_extra = int((daylight or {}).get("bias_minutes") or 0)
        observes_dst = bool(
            standard
            and daylight
            and (base_bias + standard_extra) != (base_bias + daylight_extra)
        )
        standard_offset = _format_utc_offset_from_ews_bias(base_bias + standard_extra)
        parsed_zone = {
            "bias_minutes": base_bias,
            "utc_offset": standard_offset,
            "observes_daylight_saving": observes_dst,
            "standard_transition": standard if observes_dst else None,
            "daylight_transition": daylight if observes_dst else None,
            "standard_utc_offset": standard_offset,
            "daylight_utc_offset": (
                _format_utc_offset_from_ews_bias(base_bias + daylight_extra)
                if observes_dst else None
            ),
        }

    return {
        "time_zone": parsed_zone,
        "working_periods": periods,
    }


def _body_preview(body_html: str, limit: int = 300) -> str:
    value = re.sub(r"<[^>]+>", " ", body_html)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _sort_stamp(item: dict[str, Any]) -> str:
    return str(
        item.get("received_at")
        or item.get("sent_at")
        or item.get("last_modified_at")
        or item.get("created_at")
        or ""
    )


class EwsClient:
    def __init__(
        self,
        config: AppConfig,
        password: str,
        *,
        session: Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.auth = HttpNtlmAuth(config.username, password)
        self.session.headers.update(
            {
                "Content-Type": "text/xml; charset=utf-8",
                "Accept": "text/xml",
                "User-Agent": f"exchange-ews-mcp/{__version__}",
            }
        )

    def _post(self, payload: bytes) -> Response:
        try:
            response = self.session.post(
                self.config.ews_url,
                data=payload,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
        except requests.RequestException as exc:
            raise EwsError(f"连接 EWS 失败：{exc}") from exc

        if response.status_code == 401:
            raise EwsError("EWS 返回 401：用户名、密码或 NTLM 账号格式不正确。")
        if response.status_code == 403:
            raise EwsError("EWS 返回 403：账号可能没有执行此操作的权限。")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text[:1000].strip()
            code_value: str | None = None
            try:
                fault_root = ET.fromstring(response.content)
                fault_string = fault_root.find(".//faultstring")
                response_code = fault_root.find(f".//{q(MESSAGES_NS, 'ResponseCode')}")
                message_text = fault_root.find(f".//{q(MESSAGES_NS, 'MessageText')}")
                code_value = (
                    response_code.text.strip()
                    if response_code is not None and response_code.text
                    else None
                )
                parts = [
                    node.text.strip()
                    for node in (fault_string, response_code, message_text)
                    if node is not None and node.text and node.text.strip()
                ]
                if parts:
                    detail = " | ".join(dict.fromkeys(parts))
            except ET.ParseError:
                pass
            raise EwsError(
                f"EWS HTTP 错误 {response.status_code}: {detail or '无响应正文'}",
                response_code=code_value,
            ) from exc
        return response

    @staticmethod
    def _parse_xml(response: Response) -> ET.Element:
        try:
            return ET.fromstring(response.content)
        except ET.ParseError as exc:
            preview = response.text[:500]
            raise EwsError(f"EWS 返回了无法解析的 XML：{preview}") from exc

    @staticmethod
    def _response_code(root: ET.Element) -> str | None:
        response_code = root.find(f".//{q(MESSAGES_NS, 'ResponseCode')}")
        return response_code.text if response_code is not None else None

    @classmethod
    def _raise_for_ews_error(
        cls, root: ET.Element, *, allowed_codes: set[str] | None = None
    ) -> None:
        code = cls._response_code(root)
        if code == "NoError" or (allowed_codes and code in allowed_codes):
            return
        message_text = root.find(f".//{q(MESSAGES_NS, 'MessageText')}")
        detail = message_text.text if message_text is not None else "未知 EWS 错误"
        raise EwsError(
            f"EWS 操作失败：{code or 'Unknown'} - {detail}", response_code=code
        )

    @staticmethod
    def _draft_from_response(root: ET.Element, *, draft_type: str) -> DraftResult:
        item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
        if item is None or not item.attrib.get("Id"):
            raise EwsError("EWS 返回成功，但响应中缺少草稿 ItemId。")
        return DraftResult(
            item_id=item.attrib["Id"],
            change_key=item.attrib.get("ChangeKey"),
            draft_type=draft_type,
        )

    def test_connection(self) -> str:
        payload = build_get_inbox_request(self.config.exchange_version)
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        display_name = root.find(f".//{q(TYPES_NS, 'DisplayName')}")
        return display_name.text if display_name is not None else "Inbox"

    def _resolve_names_legacy(
        self,
        *,
        query: str,
        search_scope: str,
        limit: int,
    ) -> dict[str, Any]:
        payload = build_resolve_names_request(
            exchange_version=self.config.exchange_version,
            query=query,
            search_scope=search_scope,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root, allowed_codes={"ErrorNameResolutionNoResults"})
        if self._response_code(root) == "ErrorNameResolutionNoResults":
            return {
                "query": query,
                "search_scope": search_scope,
                "returned": 0,
                "includes_last_item": True,
                "candidates": [],
            }

        resolution_set = root.find(f".//{q(MESSAGES_NS, 'ResolutionSet')}")
        if resolution_set is None:
            resolution_set = root.find(f".//{q(TYPES_NS, 'ResolutionSet')}")
        candidates: list[dict[str, Any]] = []
        if resolution_set is not None:
            for resolution in list(resolution_set)[:limit]:
                mailbox = resolution.find(q(TYPES_NS, "Mailbox"))
                if mailbox is None:
                    continue
                contact = resolution.find(q(TYPES_NS, "Contact"))
                candidate: dict[str, Any] = {
                    "display_name": _text(mailbox, "Name"),
                    "email": _text(mailbox, "EmailAddress"),
                    "routing_type": _text(mailbox, "RoutingType"),
                    "mailbox_type": _text(mailbox, "MailboxType"),
                    "contact": None,
                }
                if contact is not None:
                    candidate["contact"] = {
                        "display_name": _text(contact, "DisplayName"),
                        "given_name": _text(contact, "GivenName"),
                        "surname": _text(contact, "Surname"),
                        "company_name": _text(contact, "CompanyName"),
                        "department": _text(contact, "Department"),
                        "job_title": _text(contact, "JobTitle"),
                    }
                candidates.append(candidate)
        includes_last = True
        if resolution_set is not None:
            includes_last = (
                resolution_set.attrib.get("IncludesLastItemInRange", "true").lower()
                == "true"
            )
        return {
            "query": query,
            "search_scope": search_scope,
            "returned": len(candidates),
            "includes_last_item": includes_last,
            "candidates": candidates,
        }

    def resolve_names(
        self,
        *,
        query: str,
        search_scope: str = DEFAULT_RESOLVE_SCOPE,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Resolve romanized person names or complete SMTP addresses from Contacts and GAL.

        This deployment intentionally does not query Chinese display names because the
        target Exchange directory only resolves romanized aliases reliably. Non-ASCII
        queries are rejected before any EWS request and returned as a structured result
        for the main Agent to handle.
        """
        normalized = query.strip()
        if not normalized:
            raise ValueError("query 不能为空。")
        if not normalized.isascii():
            return {
                "query": normalized,
                "status": "romanized_query_required",
                "requires_romanized_query": True,
                "message": (
                    "当前 Exchange 通讯簿仅支持使用姓名拼音或完整邮箱进行人员查询。"
                    "请不要输入中文姓名。"
                ),
                "search_scope": normalize_resolve_scope(search_scope),
                "strategy": "romanized_resolvenames_only",
                "returned": 0,
                "includes_last_item": True,
                "candidates": [],
                "warnings": [],
            }

        scope = normalize_resolve_scope(search_scope)
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间。")

        wants_contacts = scope in {
            "Contacts",
            "ContactsActiveDirectory",
            "ActiveDirectoryContacts",
        }
        wants_directory = scope in {
            "ActiveDirectory",
            "ContactsActiveDirectory",
            "ActiveDirectoryContacts",
        }
        contact_groups: list[tuple[str, list[dict[str, Any]]]] = []
        directory_groups: list[tuple[str, list[dict[str, Any]]]] = []
        warnings: list[dict[str, str | None]] = []
        includes_last = True

        if wants_contacts:
            try:
                contacts = self._resolve_names_legacy(
                    query=normalized,
                    search_scope="Contacts",
                    limit=limit,
                )
                contact_groups.append(("contacts_resolvenames", contacts["candidates"]))
                includes_last = includes_last and bool(contacts["includes_last_item"])
            except EwsError as exc:
                warnings.append({
                    "source": "contacts_resolvenames",
                    "response_code": exc.response_code,
                    "message": str(exc),
                })

        if wants_directory:
            try:
                directory = self._resolve_names_legacy(
                    query=normalized,
                    search_scope="ActiveDirectory",
                    limit=limit,
                )
                directory_groups.append(("directory_resolvenames", directory["candidates"]))
                includes_last = includes_last and bool(directory["includes_last_item"])
            except EwsError as exc:
                warnings.append({
                    "source": "directory_resolvenames",
                    "response_code": exc.response_code,
                    "message": str(exc),
                })

        ordered_groups = (
            directory_groups + contact_groups
            if scope == "ActiveDirectoryContacts"
            else contact_groups + directory_groups
        )
        candidates = _merge_person_candidates(ordered_groups, limit=limit)
        return {
            "query": normalized,
            "status": "resolved" if candidates else "not_found",
            "requires_romanized_query": False,
            "message": None,
            "search_scope": scope,
            "strategy": "contacts_resolvenames+directory_resolvenames",
            "returned": len(candidates),
            "includes_last_item": includes_last,
            "candidates": candidates,
            "warnings": warnings,
        }

    def get_current_user(self) -> dict[str, Any]:
        explicit_email = (self.config.primary_email or "").strip()
        explicit_name = (self.config.display_name or "").strip() or None
        if explicit_email:
            selected = None
            directory_warning = None
            try:
                resolved = self.resolve_names(query=explicit_email)
                exact = [
                    item
                    for item in resolved["candidates"]
                    if str(item.get("email") or "").casefold() == explicit_email.casefold()
                ]
                selected = exact[0] if exact else None
            except EwsError as exc:
                # Explicit configuration is authoritative; directory enrichment is optional.
                directory_warning = str(exc)
            return {
                "status": "resolved",
                "source": "configured",
                "display_name": explicit_name or (selected or {}).get("display_name"),
                "primary_email": explicit_email,
                "aliases": [],
                "directory_candidate": selected,
                "directory_warning": directory_warning,
            }

        username = self.config.username.strip()
        query = username
        if "\\" in query:
            query = query.rsplit("\\", 1)[-1]
        if "/" in query:
            query = query.rsplit("/", 1)[-1]
        resolved = self.resolve_names(query=query)
        candidates = resolved["candidates"]
        query_fold = query.casefold()

        def score(item: dict[str, Any]) -> int:
            email = str(item.get("email") or "")
            name = str(item.get("display_name") or "")
            local = email.split("@", 1)[0] if "@" in email else email
            value = 0
            if email.casefold() == username.casefold():
                value += 100
            if email.casefold() == query_fold:
                value += 90
            if local.casefold() == query_fold:
                value += 80
            if name.casefold() == query_fold:
                value += 70
            if query_fold and query_fold in email.casefold():
                value += 20
            return value

        ranked = sorted(candidates, key=score, reverse=True)
        best_score = score(ranked[0]) if ranked else 0
        best = [item for item in ranked if score(item) == best_score and best_score > 0]
        if len(best) == 1:
            selected = best[0]
            return {
                "status": "resolved",
                "source": "username_resolve",
                "display_name": selected.get("display_name"),
                "primary_email": selected.get("email"),
                "aliases": [],
                "query": query,
                "directory_candidate": selected,
            }
        return {
            "status": "ambiguous" if candidates else "not_found",
            "source": "username_resolve",
            "query": query,
            "display_name": None,
            "primary_email": None,
            "aliases": [],
            "candidates": ranked[:10],
            "guidance": "运行 set-current-user 显式保存当前用户主邮箱。",
        }

    def create_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body_html: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> DraftResult:
        to_list = _validate_addresses(to, "to")
        cc_list = _validate_addresses(cc or [], "cc")
        bcc_list = _validate_addresses(bcc or [], "bcc")
        if not to_list and not cc_list and not bcc_list:
            raise ValueError("至少需要一个 To、CC 或 BCC 收件人。")
        if not subject.strip():
            raise ValueError("邮件主题不能为空。")
        if not body_html.strip():
            raise ValueError("HTML 正文不能为空。")

        payload = build_create_draft_request(
            exchange_version=self.config.exchange_version,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            subject=subject.strip(),
            body_html=body_html,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
        if item is None or not item.attrib.get("Id"):
            raise EwsError("EWS 返回成功，但响应中缺少草稿 ItemId。")
        return DraftResult(
            item_id=item.attrib["Id"],
            change_key=item.attrib.get("ChangeKey"),
            subject=subject.strip(),
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            draft_type="new",
        )

    def list_emails(
        self,
        *,
        folder: str = "inbox",
        limit: int = 20,
        offset: int = 0,
        unread_only: bool | None = None,
    ) -> dict[str, Any]:
        return self.search_emails(
            folder=folder,
            limit=limit,
            offset=offset,
            unread_only=unread_only,
        )

    def search_emails(
        self,
        *,
        folder: str = "inbox",
        subject_contains: str | None = None,
        sender: str | None = None,
        to_contains: str | None = None,
        cc_contains: str | None = None,
        participant_contains: str | None = None,
        unread_only: bool | None = None,
        has_attachments: bool | None = None,
        conversation_id: str | None = None,
        internet_message_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        folder = _validate_folder(folder)
        limit, offset = _validate_page(limit, offset)
        sender_value: str | None = None
        if sender:
            sender_values = _validate_addresses([sender], "sender")
            sender_value = sender_values[0]
        criteria = SearchCriteria(
            subject_contains=subject_contains.strip() if subject_contains else None,
            sender=sender_value,
            to_contains=to_contains.strip() if to_contains else None,
            cc_contains=cc_contains.strip() if cc_contains else None,
            participant_contains=participant_contains.strip() if participant_contains else None,
            unread_only=unread_only,
            has_attachments=has_attachments,
            conversation_id=conversation_id.strip() if conversation_id else None,
            internet_message_id=internet_message_id.strip() if internet_message_id else None,
            after=_normalize_iso_datetime(after, "after"),
            before=_normalize_iso_datetime(before, "before"),
        )
        payload = build_find_items_request(
            exchange_version=self.config.exchange_version,
            folder=folder,
            limit=limit,
            offset=offset,
            criteria=criteria,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        root_folder = root.find(f".//{q(MESSAGES_NS, 'RootFolder')}")
        if root_folder is None:
            raise EwsError("EWS 搜索响应中缺少 RootFolder。")
        items_container = root_folder.find(q(TYPES_NS, "Items"))
        items = (
            []
            if items_container is None
            else [
                {**_parse_message_summary(item), "folder": folder}
                for item in list(items_container)
                if item.tag.rsplit("}", 1)[-1]
                in {
                    "Message",
                    "MeetingMessage",
                    "MeetingRequest",
                    "MeetingResponse",
                    "MeetingCancellation",
                }
            ]
        )
        includes_last = (
            root_folder.attrib.get("IncludesLastItemInRange", "true").lower() == "true"
        )
        next_offset_raw = root_folder.attrib.get("IndexedPagingOffset")
        next_offset = None
        if not includes_last:
            try:
                next_offset = (
                    int(next_offset_raw)
                    if next_offset_raw is not None
                    else offset + len(items)
                )
            except ValueError:
                next_offset = offset + len(items)
        try:
            total_items = int(root_folder.attrib.get("TotalItemsInView", str(len(items))))
        except ValueError:
            total_items = len(items)
        return {
            "folder": folder,
            "folders": [folder],
            "offset": offset,
            "limit": limit,
            "returned": len(items),
            "total_items_in_view": total_items,
            "includes_last_item": includes_last,
            "next_offset": next_offset,
            "items": items,
        }

    def search_emails_multi_folder(
        self,
        *,
        folders: list[str],
        limit: int = 20,
        offset: int = 0,
        **criteria: Any,
    ) -> dict[str, Any]:
        normalized = normalize_mail_folders(folders)
        limit, offset = _validate_page(limit, offset)
        if limit + offset > MAX_PAGE_SIZE:
            raise ValueError("多文件夹搜索要求 limit + offset 不超过 100。")
        fetch_size = limit + offset
        all_items: list[dict[str, Any]] = []
        per_folder: list[dict[str, Any]] = []
        for folder in normalized:
            page = self.search_emails(
                folder=folder,
                limit=fetch_size,
                offset=0,
                **criteria,
            )
            all_items.extend(page["items"])
            per_folder.append(
                {
                    "folder": folder,
                    "returned": page["returned"],
                    "total_items_in_view": page["total_items_in_view"],
                }
            )
        all_items.sort(key=_sort_stamp, reverse=True)
        selected = all_items[offset : offset + limit]
        return {
            "folder": None,
            "folders": normalized,
            "offset": offset,
            "limit": limit,
            "returned": len(selected),
            "total_items_in_view": len(all_items),
            "includes_last_item": offset + limit >= len(all_items),
            "next_offset": offset + limit if offset + limit < len(all_items) else None,
            "per_folder": per_folder,
            "items": selected,
        }

    def get_item_identity(self, *, item_id: str) -> dict[str, str | None]:
        normalized = item_id.strip()
        if not normalized:
            raise ValueError("item_id 不能为空。")
        payload = build_get_item_identity_request(
            exchange_version=self.config.exchange_version,
            item_id=normalized,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
        if item is None or not item.attrib.get("Id"):
            raise EwsError("EWS GetItem 成功，但未返回 ItemId。")
        return {"item_id": item.attrib["Id"], "change_key": item.attrib.get("ChangeKey")}

    def _current_reference(
        self, *, item_id: str, supplied_change_key: str | None = None
    ) -> tuple[str, str]:
        identity = self.get_item_identity(item_id=item_id)
        current_id = str(identity["item_id"])
        current_key = identity.get("change_key") or supplied_change_key
        if not current_key:
            raise EwsError("EWS 未返回当前 ChangeKey，无法执行草稿操作。")
        return current_id, str(current_key)

    def get_email(
        self,
        *,
        item_id: str,
        change_key: str | None = None,
        max_body_chars: int = 50000,
    ) -> dict[str, Any]:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        if not 1000 <= max_body_chars <= 500000:
            raise ValueError("max_body_chars 必须在 1000 到 500000 之间。")
        payload = build_get_item_request(
            exchange_version=self.config.exchange_version,
            item_id=item_id.strip(),
            change_key=change_key,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        items = root.find(f".//{q(MESSAGES_NS, 'Items')}")
        if items is None or not list(items):
            raise EwsError("EWS GetItem 响应中没有邮件项目。")
        message = list(items)[0]
        result = _parse_message_summary(message)
        result.update(
            {
                "to": _mailboxes(message, "ToRecipients"),
                "cc": _mailboxes(message, "CcRecipients"),
                "bcc": _mailboxes(message, "BccRecipients"),
                "reply_to": _mailboxes(message, "ReplyTo"),
                "categories": [
                    element.text or ""
                    for element in message.findall(
                        f"{q(TYPES_NS, 'Categories')}/{q(TYPES_NS, 'String')}"
                    )
                ],
                "in_reply_to": _text(message, "InReplyTo"),
                "attachments": _parse_attachment_metadata(message),
            }
        )
        body = message.find(q(TYPES_NS, "Body"))
        body_html = body.text if body is not None and body.text is not None else ""
        body_server_truncated = (
            str(body.attrib.get("IsTruncated") or "").casefold() == "true"
            if body is not None else False
        )
        body_local_truncated = len(body_html) > max_body_chars
        result["body_type"] = body.attrib.get("BodyType") if body is not None else None
        result["body_server_truncated"] = body_server_truncated
        result["body_local_truncated"] = body_local_truncated
        result["body_truncated"] = body_server_truncated or body_local_truncated
        result["body_html"] = body_html[:max_body_chars]
        result["body_preview"] = _body_preview(body_html)

        unique_body = message.find(q(TYPES_NS, "UniqueBody"))
        unique_html = (
            unique_body.text
            if unique_body is not None and unique_body.text is not None
            else ""
        )
        unique_server_truncated = (
            str(unique_body.attrib.get("IsTruncated") or "").casefold() == "true"
            if unique_body is not None else False
        )
        unique_local_truncated = len(unique_html) > max_body_chars
        result["unique_body_type"] = (
            unique_body.attrib.get("BodyType") if unique_body is not None else None
        )
        result["unique_body_server_truncated"] = unique_server_truncated
        result["unique_body_local_truncated"] = unique_local_truncated
        result["unique_body_truncated"] = unique_server_truncated or unique_local_truncated
        result["unique_body_html"] = unique_html[:max_body_chars]
        return result

    def get_attachments(self, *, attachment_ids: list[str]) -> list[AttachmentContent]:
        normalized = [value.strip() for value in attachment_ids if value and value.strip()]
        if not normalized:
            return []
        if len(normalized) > 100:
            raise ValueError("单次最多读取 100 个附件。")
        payload = build_get_attachments_request(
            exchange_version=self.config.exchange_version,
            attachment_ids=normalized,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        response_messages = root.findall(f".//{q(MESSAGES_NS, 'GetAttachmentResponseMessage')}")
        if not response_messages:
            raise EwsError("EWS GetAttachment 响应中缺少 ResponseMessage。")
        results: list[AttachmentContent] = []
        for response_message in response_messages:
            code_node = response_message.find(q(MESSAGES_NS, "ResponseCode"))
            code = code_node.text if code_node is not None else None
            if code != "NoError":
                text_node = response_message.find(q(MESSAGES_NS, "MessageText"))
                detail = text_node.text if text_node is not None else "未知附件错误"
                raise EwsError(
                    f"EWS GetAttachment 失败：{code or 'Unknown'} - {detail}",
                    response_code=code,
                )
            container = response_message.find(q(MESSAGES_NS, "Attachments"))
            if container is None:
                continue
            for attachment in list(container):
                attachment_id_node = attachment.find(q(TYPES_NS, "AttachmentId"))
                attachment_id = (
                    attachment_id_node.attrib.get("Id")
                    if attachment_id_node is not None
                    else None
                )
                if not attachment_id:
                    continue
                attachment_type = attachment.tag.rsplit("}", 1)[-1]
                content_bytes: bytes | None = None
                content_text = _text(attachment, "Content")
                if content_text:
                    try:
                        content_bytes = base64.b64decode(content_text, validate=True)
                    except (ValueError, TypeError) as exc:
                        raise EwsError(
                            f"附件 {attachment_id} 的 Base64 内容无效。"
                        ) from exc
                results.append(
                    AttachmentContent(
                        attachment_id=attachment_id,
                        attachment_type=attachment_type,
                        filename=_text(attachment, "Name"),
                        content_type=_text(attachment, "ContentType"),
                        size=_int_text(attachment, "Size"),
                        is_inline=bool(_bool_text(attachment, "IsInline")),
                        content_id=_text(attachment, "ContentId"),
                        content=content_bytes,
                    )
                )
        by_id = {item.attachment_id: item for item in results}
        missing = [item for item in normalized if item not in by_id]
        if missing:
            raise EwsError(f"EWS 未返回以下附件：{', '.join(missing)}")
        return [by_id[item] for item in normalized]

    def reply_as_draft(
        self,
        *,
        item_id: str,
        body_html: str,
        reply_all: bool = False,
        change_key: str | None = None,
    ) -> DraftResult:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        if not body_html.strip():
            raise ValueError("HTML 正文不能为空。")
        current_item_id, current_change_key = self._current_reference(
            item_id=item_id.strip(), supplied_change_key=change_key
        )
        payload = build_reply_draft_request(
            exchange_version=self.config.exchange_version,
            item_id=current_item_id,
            change_key=current_change_key,
            body_html=body_html,
            reply_all=reply_all,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        return self._draft_from_response(
            root, draft_type="reply_all" if reply_all else "reply"
        )

    def forward_as_draft(
        self,
        *,
        item_id: str,
        to: list[str],
        body_html: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        change_key: str | None = None,
    ) -> DraftResult:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        to_list = _validate_addresses(to, "to")
        cc_list = _validate_addresses(cc or [], "cc")
        bcc_list = _validate_addresses(bcc or [], "bcc")
        if not to_list and not cc_list and not bcc_list:
            raise ValueError("至少需要一个 To、CC 或 BCC 收件人。")
        if not body_html.strip():
            raise ValueError("HTML 正文不能为空。")
        current_item_id, current_change_key = self._current_reference(
            item_id=item_id.strip(), supplied_change_key=change_key
        )
        payload = build_forward_draft_request(
            exchange_version=self.config.exchange_version,
            item_id=current_item_id,
            change_key=current_change_key,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            body_html=body_html,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        result = self._draft_from_response(root, draft_type="forward")
        return DraftResult(
            item_id=result.item_id,
            change_key=result.change_key,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            draft_type="forward",
        )

    def update_draft(
        self,
        *,
        item_id: str,
        change_key: str | None = None,
        subject: str | None = None,
        body_html: str | None = None,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        importance: str | None = None,
    ) -> DraftResult:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        if all(value is None for value in (subject, body_html, to, cc, bcc, importance)):
            raise ValueError("至少需要提供一个待更新字段。")
        current = self.get_email(item_id=item_id, change_key=change_key, max_body_chars=1000)
        if current.get("is_draft") is not True:
            raise ValueError("目标邮件不是草稿，拒绝更新。")
        current_id = str(current.get("item_id") or item_id).strip()
        current_key = current.get("change_key") or change_key
        if not current_key:
            raise EwsError("EWS 未返回草稿当前 ChangeKey。")

        subject_value = None
        if subject is not None:
            subject_value = subject.strip()
            if not subject_value:
                raise ValueError("subject 不能设置为空字符串。")
        body_value = None
        if body_html is not None:
            body_value = body_html.strip()
            if not body_value:
                raise ValueError("body_html 不能设置为空字符串。")
        to_list = _validate_addresses(to, "to") if to is not None else None
        cc_list = _validate_addresses(cc, "cc") if cc is not None else None
        bcc_list = _validate_addresses(bcc, "bcc") if bcc is not None else None
        importance_value = normalize_importance(importance)

        payload = build_update_draft_request(
            exchange_version=self.config.exchange_version,
            item_id=current_id,
            change_key=str(current_key),
            subject=subject_value,
            body_html=body_value,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            importance=importance_value,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
        if item is None or not item.attrib.get("Id"):
            identity = self.get_item_identity(item_id=current_id)
            updated_id = str(identity["item_id"])
            updated_key = identity.get("change_key")
        else:
            updated_id = item.attrib["Id"]
            updated_key = item.attrib.get("ChangeKey")
        return DraftResult(
            item_id=updated_id,
            change_key=updated_key,
            subject=subject_value,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            draft_type="updated",
        )

    def attachment_roots(self) -> list[Path]:
        return self._attachment_roots()

    def _attachment_roots(self) -> list[Path]:
        configured = self.config.attachment_roots
        if configured:
            roots = [Path(root).expanduser().resolve() for root in configured]
        else:
            home = Path.home()
            roots = [home / "Desktop", home / "Documents", home / "Downloads"]
            roots = [root.resolve() for root in roots]
        return roots

    def _resolve_attachment_path(self, file_path: str) -> Path:
        path = Path(file_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"附件路径不是普通文件：{path}")
        roots = self._attachment_roots()
        if not any(path == root or root in path.parents for root in roots):
            allowed = ", ".join(str(root) for root in roots)
            raise ValueError(
                f"附件路径不在允许目录中：{path}。允许目录：{allowed}。"
                "可运行 set-attachment-roots 修改。"
            )
        size = path.stat().st_size
        if size > self.config.max_attachment_bytes:
            raise ValueError(
                f"附件大小 {size} 字节，超过限制 {self.config.max_attachment_bytes} 字节。"
            )
        return path

    def validate_attachment_path(self, file_path: str) -> str:
        """Validate an attachment before any Exchange write and return its canonical path."""
        return str(self._resolve_attachment_path(file_path))

    def validate_attachment_content(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        is_inline: bool = False,
        content_id: str | None = None,
    ) -> dict[str, Any]:
        safe_name = filename.strip()
        if not safe_name or any(char in safe_name for char in "\\/\0"):
            raise ValueError("filename 不是有效文件名。")
        if len(content) > self.config.max_attachment_bytes:
            raise ValueError(
                f"附件大小 {len(content)} 字节，超过限制 {self.config.max_attachment_bytes} 字节。"
            )
        normalized_content_id = (content_id or "").strip().strip("<>") or None
        if is_inline and not normalized_content_id:
            raise ValueError("内联附件必须提供 content_id。")
        return {
            "filename": safe_name,
            "content": content,
            "content_type": content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            "is_inline": bool(is_inline),
            "content_id": normalized_content_id,
        }


    def add_attachment_content_to_draft(
        self,
        *,
        item_id: str,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        change_key: str | None = None,
        is_inline: bool = False,
        content_id: str | None = None,
        verify_draft: bool = True,
    ) -> AttachmentResult:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        validated = self.validate_attachment_content(
            filename=filename,
            content=content,
            content_type=content_type,
            is_inline=is_inline,
            content_id=content_id,
        )
        safe_name = str(validated["filename"])
        content = bytes(validated["content"])
        resolved_type = str(validated["content_type"])
        is_inline = bool(validated["is_inline"])
        content_id = validated.get("content_id")
        current_change_key = change_key
        if verify_draft:
            draft = self.get_email(item_id=item_id, change_key=change_key, max_body_chars=1000)
            if draft.get("is_draft") is not True:
                raise ValueError("目标邮件不是草稿，拒绝添加附件。")
            current_change_key = draft.get("change_key") or change_key
        payload = build_create_attachment_request(
            exchange_version=self.config.exchange_version,
            item_id=item_id.strip(),
            change_key=current_change_key if isinstance(current_change_key, str) else None,
            filename=safe_name,
            content_type=resolved_type,
            content=content,
            is_inline=is_inline,
            content_id=(content_id or "").strip() or None,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        attachment_id = root.find(f".//{q(TYPES_NS, 'AttachmentId')}")
        if attachment_id is None or not attachment_id.attrib.get("Id"):
            raise EwsError("EWS 返回成功，但响应中缺少 AttachmentId。")
        return AttachmentResult(
            attachment_id=attachment_id.attrib["Id"],
            root_item_id=attachment_id.attrib.get("RootItemId", item_id.strip()),
            root_item_change_key=attachment_id.attrib.get("RootItemChangeKey"),
            filename=safe_name,
            size=len(content),
            content_type=resolved_type,
        )


    def add_attachment_to_draft(
        self,
        *,
        item_id: str,
        file_path: str,
        change_key: str | None = None,
        attachment_name: str | None = None,
    ) -> AttachmentResult:
        path = self._resolve_attachment_path(file_path)
        filename = (attachment_name or path.name).strip()
        return self.add_attachment_content_to_draft(
            item_id=item_id,
            change_key=change_key,
            filename=filename,
            content=path.read_bytes(),
            content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
            verify_draft=True,
        )

    def get_user_availability(
        self,
        *,
        attendees: list[dict[str, str]],
        start: str,
        end: str,
        interval_minutes: int = 30,
    ) -> dict[str, Any]:
        if not attendees:
            raise ValueError("attendees 不能为空。")
        if len(attendees) > 100:
            raise ValueError("GetUserAvailability 单次最多支持 100 个身份。")
        normalized_attendees: list[dict[str, str]] = []
        for raw in attendees:
            email = _validate_addresses([str(raw.get("email") or "")], "attendees")[0]
            attendee_type = normalize_attendee_type(
                raw.get("attendee_type") or raw.get("type") or raw.get("role")
            )
            normalized_attendees.append({"email": email, "attendee_type": attendee_type})
        start_value = _normalize_iso_datetime(start, "start")
        end_value = _normalize_iso_datetime(end, "end")
        if not start_value or not end_value:
            raise ValueError("start 和 end 不能为空。")
        start_dt = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        if end_dt <= start_dt:
            raise ValueError("end 必须晚于 start。")
        if end_dt - start_dt > timedelta(days=42):
            raise ValueError("GetUserAvailability 查询窗口不能超过 42 天。")
        if not 5 <= interval_minutes <= 1440:
            raise ValueError("interval_minutes 必须在 5 到 1440 之间。")
        # Exchange 2010+ availability is scoped through a TimeZoneContext header
        # using the server-known Windows time-zone identifier ``UTC``.  Keep the
        # wire values as offset-free UTC wall-clock values, as required by EWS.
        wire_start = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        wire_end = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        payload = build_get_user_availability_request(
            exchange_version=self.config.exchange_version,
            attendees=normalized_attendees,
            start=wire_start,
            end=wire_end,
            interval_minutes=interval_minutes,
            time_zone_id="UTC",
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        responses = root.findall(f".//{q(MESSAGES_NS, 'FreeBusyResponse')}")
        if not responses:
            self._raise_for_ews_error(root)
            raise EwsError("GetUserAvailability 响应中缺少 FreeBusyResponse。")
        result_attendees: list[dict[str, Any]] = []
        for index, requested in enumerate(normalized_attendees):
            response_node = responses[index] if index < len(responses) else None
            if response_node is None:
                result_attendees.append(
                    {
                        **requested,
                        "status": "error",
                        "response_code": "MissingResponse",
                        "message": "服务器未返回该参会人的忙闲数据。",
                        "events": [],
                        "working_hours": None,
                    }
                )
                continue
            code_node = response_node.find(f".//{q(MESSAGES_NS, 'ResponseCode')}")
            code = code_node.text if code_node is not None else None
            message_node = response_node.find(f".//{q(MESSAGES_NS, 'MessageText')}")
            message_text = message_node.text if message_node is not None else None
            free_busy = response_node.find(q(MESSAGES_NS, "FreeBusyView"))
            if free_busy is None:
                free_busy = response_node.find(q(TYPES_NS, "FreeBusyView"))
            events: list[dict[str, Any]] = []
            if free_busy is not None:
                event_array = free_busy.find(q(TYPES_NS, "CalendarEventArray"))
                if event_array is not None:
                    for event in event_array.findall(q(TYPES_NS, "CalendarEvent")):
                        details = event.find(q(TYPES_NS, "CalendarEventDetails"))
                        events.append(
                            {
                                # With TimeZoneContext=UTC, some on-prem Exchange builds
                                # return CalendarEvent times without a trailing Z/offset.
                                # Normalize those wire values as UTC before they reach the
                                # presentation layer, which intentionally requires canonical
                                # timezone-aware timestamps.
                                "start": _normalize_iso_datetime(
                                    _text(event, "StartTime"), "CalendarEvent.StartTime"
                                ),
                                "end": _normalize_iso_datetime(
                                    _text(event, "EndTime"), "CalendarEvent.EndTime"
                                ),
                                "busy_type": _text(event, "BusyType"),
                                "subject": _text(details, "Subject") if details is not None else None,
                                "location": _text(details, "Location") if details is not None else None,
                                "is_meeting": _bool_text(details, "IsMeeting") if details is not None else None,
                                "is_recurring": _bool_text(details, "IsRecurring") if details is not None else None,
                                "is_private": _bool_text(details, "IsPrivate") if details is not None else None,
                            }
                        )
            result_attendees.append(
                {
                    **requested,
                    "status": "success" if code == "NoError" else "error",
                    "response_code": code,
                    "message": message_text,
                    "free_busy_view_type": _text(free_busy, "FreeBusyViewType") if free_busy is not None else None,
                    "merged_free_busy": _text(free_busy, "MergedFreeBusy") if free_busy is not None else None,
                    "events": events,
                    "working_hours": _parse_working_hours(free_busy) if free_busy is not None else None,
                }
            )
        return {
            "status": "success" if all(item["status"] == "success" for item in result_attendees) else "partial_error",
            "start": start_value,
            "end": end_value,
            "interval_minutes": interval_minutes,
            "returned": len(result_attendees),
            "attendees": result_attendees,
            "ews_time_zone_id": "UTC",
            "time_zone_transport": "timezone_context",
        }

    def list_calendar_events(
        self,
        *,
        start: str,
        end: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        start_value = _normalize_iso_datetime(start, "start")
        end_value = _normalize_iso_datetime(end, "end")
        if not start_value or not end_value:
            raise ValueError("start 和 end 不能为空。")
        start_dt = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        if end_dt <= start_dt:
            raise ValueError("end 必须晚于 start。")
        if not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间。")
        payload = build_find_calendar_items_request(
            exchange_version=self.config.exchange_version,
            start=start_value,
            end=end_value,
            max_entries=limit,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        root_folder = root.find(f".//{q(MESSAGES_NS, 'RootFolder')}")
        if root_folder is None:
            raise EwsError("日历查询响应中缺少 RootFolder。")
        items_container = root_folder.find(q(TYPES_NS, "Items"))
        items = [] if items_container is None else [
            _parse_calendar_item(item)
            for item in list(items_container)
            if item.tag.rsplit("}", 1)[-1] == "CalendarItem"
        ]
        return {
            "status": "success",
            "folder": "calendar",
            "start": start_value,
            "end": end_value,
            "returned": len(items),
            "items": items,
        }

    def get_calendar_item(
        self,
        *,
        item_id: str,
        change_key: str | None = None,
    ) -> dict[str, Any]:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        payload = build_get_calendar_item_request(
            exchange_version=self.config.exchange_version,
            item_id=item_id.strip(),
            change_key=change_key,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        items = root.find(f".//{q(MESSAGES_NS, 'Items')}")
        if items is None or not list(items):
            raise EwsError("GetItem 响应中没有日历项目。")
        item = next((node for node in list(items) if node.tag.rsplit("}", 1)[-1] == "CalendarItem"), None)
        if item is None:
            raise EwsError("目标 ItemId 不是 CalendarItem。")
        return _parse_calendar_item(item, include_body=True)

    def create_meeting(
        self,
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
    ) -> CalendarItemResult:
        subject_value = subject.strip()
        body_value = body_html.strip()
        if not subject_value:
            raise ValueError("subject 不能为空。")
        if not body_value:
            raise ValueError("body_html 不能为空。")
        required = _validate_addresses(required_attendees, "required_attendees")
        optional = _validate_addresses(optional_attendees or [], "optional_attendees")
        if not required and not optional:
            raise ValueError("会议至少需要一个 required 或 optional attendee。")
        start_value = _normalize_iso_datetime(start, "start")
        end_value = _normalize_iso_datetime(end, "end")
        if not start_value or not end_value:
            raise ValueError("start 和 end 不能为空。")
        start_dt = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        if end_dt <= start_dt:
            raise ValueError("end 必须晚于 start。")
        if not 0 <= reminder_minutes <= 40320:
            raise ValueError("reminder_minutes 必须在 0 到 40320 之间。")
        payload = build_create_meeting_request(
            exchange_version=self.config.exchange_version,
            subject=subject_value,
            body_html=body_value,
            start=start_value,
            end=end_value,
            required_attendees=required,
            optional_attendees=optional,
            location=location,
            reminder_minutes=reminder_minutes,
            send_invitations=send_invitations,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        item = root.find(f".//{q(TYPES_NS, 'CalendarItem')}/{q(TYPES_NS, 'ItemId')}")
        if item is None:
            item = root.find(f".//{q(TYPES_NS, 'ItemId')}")
        if item is None or not item.attrib.get("Id"):
            raise EwsError("EWS 创建会议成功，但响应中缺少 CalendarItem ItemId。")
        return CalendarItemResult(
            item_id=item.attrib["Id"],
            change_key=item.attrib.get("ChangeKey"),
            subject=subject_value,
            start=start_value,
            end=end_value,
            required_attendees=required,
            optional_attendees=optional,
            location=location.strip() if location and location.strip() else None,
            sent=send_invitations,
        )

    def delete_calendar_item(
        self,
        *,
        item_id: str,
        change_key: str | None = None,
    ) -> dict[str, Any]:
        if not item_id.strip():
            raise ValueError("item_id 不能为空。")
        payload = build_delete_calendar_item_request(
            exchange_version=self.config.exchange_version,
            item_id=item_id.strip(),
            change_key=change_key,
        )
        response = self._post(payload)
        root = self._parse_xml(response)
        self._raise_for_ews_error(root)
        return {"status": "deleted", "item_id": item_id.strip()}
