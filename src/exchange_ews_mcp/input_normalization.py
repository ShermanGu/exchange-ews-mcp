from __future__ import annotations

import re
from typing import Any

CANONICAL_MAIL_FOLDERS = (
    "inbox",
    "drafts",
    "sentitems",
    "deleteditems",
    "junkemail",
    "outbox",
)


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空。")
    return re.sub(r"[\s_\-]+", "", value.strip()).casefold()


_FOLDER_ALIASES = {
    # Inbox
    "inbox": "inbox",
    "inboxes": "inbox",
    "received": "inbox",
    "receiveditems": "inbox",
    "收件箱": "inbox",
    "收件": "inbox",
    # Drafts
    "draft": "drafts",
    "drafts": "drafts",
    "draftitems": "drafts",
    "草稿": "drafts",
    "草稿箱": "drafts",
    # Sent Items
    "sent": "sentitems",
    "sentitem": "sentitems",
    "sentitems": "sentitems",
    "sentmail": "sentitems",
    "sentmails": "sentitems",
    "已发送": "sentitems",
    "已发送邮件": "sentitems",
    # Deleted Items
    "deleted": "deleteditems",
    "deleteditem": "deleteditems",
    "deleteditems": "deleteditems",
    "trash": "deleteditems",
    "trashbin": "deleteditems",
    "recyclebin": "deleteditems",
    "已删除": "deleteditems",
    "已删除邮件": "deleteditems",
    "回收站": "deleteditems",
    # Junk Email
    "junk": "junkemail",
    "junkemail": "junkemail",
    "junkmail": "junkemail",
    "spam": "junkemail",
    "垃圾邮件": "junkemail",
    # Outbox
    "outbox": "outbox",
    "pendingmail": "outbox",
    "待发送": "outbox",
    "发件箱": "outbox",
}


def normalize_mail_folder(value: str) -> str:
    """Normalize Outlook display labels and friendly aliases to EWS folder IDs."""
    key = _token(value, "folder")
    normalized = _FOLDER_ALIASES.get(key)
    if normalized is None:
        allowed = ", ".join(CANONICAL_MAIL_FOLDERS)
        raise ValueError(
            f"不支持的邮箱文件夹 {value!r}。标准值：{allowed}。"
            "也接受 Outlook 显示名，例如 Sent Items、Deleted Items、Junk Email。"
        )
    return normalized


def normalize_mail_folders(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        value = normalize_mail_folder(raw)
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("folders 至少需要一个文件夹。")
    return normalized


_TEMPLATE_MODE_ALIASES = {
    "clone": "clone",
    "copy": "clone",
    "exactcopy": "clone",
    "exactclone": "clone",
    "克隆": "clone",
    "复制": "clone",
    "replace": "replace_content",
    "replacebody": "replace_content",
    "replacecontent": "replace_content",
    "contentreplace": "replace_content",
    "替换内容": "replace_content",
    "替换正文": "replace_content",
    "renderedhtml": "rendered_html",
    "fullhtml": "rendered_html",
    "completehtml": "rendered_html",
    "完整html": "rendered_html",
    "渲染html": "rendered_html",
}


def normalize_template_mode(value: str) -> str:
    key = _token(value, "mode")
    normalized = _TEMPLATE_MODE_ALIASES.get(key)
    if normalized is None:
        raise ValueError(
            "mode 只支持标准值 clone、replace_content、rendered_html；"
            "也接受 Clone、Replace Content、Rendered HTML 等友好写法。"
        )
    return normalized


_IMPORTANCE_ALIASES = {
    "low": "Low",
    "lowimportance": "Low",
    "normal": "Normal",
    "normalimportance": "Normal",
    "medium": "Normal",
    "high": "High",
    "highimportance": "High",
    "低": "Low",
    "普通": "Normal",
    "正常": "Normal",
    "高": "High",
}


def normalize_importance(value: str | None) -> str | None:
    if value is None:
        return None
    key = _token(value, "importance")
    normalized = _IMPORTANCE_ALIASES.get(key)
    if normalized is None:
        raise ValueError("importance 只支持 Low、Normal 或 High。")
    return normalized


_ATTENDEE_TYPE_ALIASES = {
    "organizer": "Organizer",
    "organiser": "Organizer",
    "meetingorganizer": "Organizer",
    "组织者": "Organizer",
    "required": "Required",
    "requiredattendee": "Required",
    "mandatory": "Required",
    "必需": "Required",
    "必选": "Required",
    "optional": "Optional",
    "optionalattendee": "Optional",
    "可选": "Optional",
    "room": "Room",
    "meetingroom": "Room",
    "会议室": "Room",
    "resource": "Resource",
    "equipment": "Resource",
    "资源": "Resource",
    "设备": "Resource",
}


def normalize_attendee_type(value: str | None) -> str:
    raw = value if value is not None and str(value).strip() else "Required"
    key = _token(str(raw), "attendee_type")
    normalized = _ATTENDEE_TYPE_ALIASES.get(key)
    if normalized is None:
        raise ValueError(
            f"不支持的 attendee_type：{value!r}。标准值："
            "Organizer、Required、Optional、Room、Resource。"
        )
    return normalized


def normalize_availability_attendees(attendees: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, raw in enumerate(attendees):
        if not isinstance(raw, dict):
            raise ValueError(f"attendees[{index}] 必须是对象。")
        email = str(raw.get("email") or "").strip()
        role = raw.get("attendee_type")
        if role is None:
            role = raw.get("type")
        if role is None:
            role = raw.get("role")
        normalized.append({"email": email, "attendee_type": normalize_attendee_type(role)})
    return normalized
