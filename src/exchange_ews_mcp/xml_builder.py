from __future__ import annotations

import base64
from dataclasses import dataclass
from xml.etree import ElementTree as ET

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
MESSAGES_NS = "http://schemas.microsoft.com/exchange/services/2006/messages"
TYPES_NS = "http://schemas.microsoft.com/exchange/services/2006/types"

ET.register_namespace("soap", SOAP_NS)
ET.register_namespace("m", MESSAGES_NS)
ET.register_namespace("t", TYPES_NS)


@dataclass(frozen=True)
class SearchCriteria:
    subject_contains: str | None = None
    sender: str | None = None
    to_contains: str | None = None
    cc_contains: str | None = None
    participant_contains: str | None = None
    unread_only: bool | None = None
    has_attachments: bool | None = None
    conversation_id: str | None = None
    internet_message_id: str | None = None
    after: str | None = None
    before: str | None = None


def q(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _base_envelope(exchange_version: str) -> tuple[ET.Element, ET.Element]:
    envelope = ET.Element(q(SOAP_NS, "Envelope"))
    header = ET.SubElement(envelope, q(SOAP_NS, "Header"))
    ET.SubElement(
        header,
        q(TYPES_NS, "RequestServerVersion"),
        {"Version": exchange_version},
    )
    body = ET.SubElement(envelope, q(SOAP_NS, "Body"))
    return envelope, body


def _serialize(envelope: ET.Element) -> bytes:
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def _item_id(parent: ET.Element, item_id: str, change_key: str | None = None) -> ET.Element:
    attrs = {"Id": item_id}
    if change_key:
        attrs["ChangeKey"] = change_key
    return ET.SubElement(parent, q(TYPES_NS, "ItemId"), attrs)


def _reference_item_id(
    parent: ET.Element, item_id: str, change_key: str | None = None
) -> ET.Element:
    attrs = {"Id": item_id}
    if change_key:
        attrs["ChangeKey"] = change_key
    return ET.SubElement(parent, q(TYPES_NS, "ReferenceItemId"), attrs)


def build_get_inbox_request(exchange_version: str) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    get_folder = ET.SubElement(soap_body, q(MESSAGES_NS, "GetFolder"))
    folder_shape = ET.SubElement(get_folder, q(MESSAGES_NS, "FolderShape"))
    ET.SubElement(folder_shape, q(TYPES_NS, "BaseShape")).text = "IdOnly"
    additional = ET.SubElement(folder_shape, q(TYPES_NS, "AdditionalProperties"))
    ET.SubElement(additional, q(TYPES_NS, "FieldURI"), {"FieldURI": "folder:DisplayName"})
    folder_ids = ET.SubElement(get_folder, q(MESSAGES_NS, "FolderIds"))
    ET.SubElement(folder_ids, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "inbox"})
    return _serialize(envelope)


def _add_recipients(message: ET.Element, element_name: str, recipients: list[str]) -> None:
    if not recipients:
        return
    parent = ET.SubElement(message, q(TYPES_NS, element_name))
    for address in recipients:
        mailbox = ET.SubElement(parent, q(TYPES_NS, "Mailbox"))
        ET.SubElement(mailbox, q(TYPES_NS, "EmailAddress")).text = address


def build_create_draft_request(
    *,
    exchange_version: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    create_item = ET.SubElement(
        soap_body,
        q(MESSAGES_NS, "CreateItem"),
        {"MessageDisposition": "SaveOnly"},
    )
    saved_folder = ET.SubElement(create_item, q(MESSAGES_NS, "SavedItemFolderId"))
    ET.SubElement(saved_folder, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "drafts"})
    items = ET.SubElement(create_item, q(MESSAGES_NS, "Items"))
    message = ET.SubElement(items, q(TYPES_NS, "Message"))
    ET.SubElement(message, q(TYPES_NS, "Subject")).text = subject
    body = ET.SubElement(message, q(TYPES_NS, "Body"), {"BodyType": "HTML"})
    body.text = body_html
    _add_recipients(message, "ToRecipients", to)
    _add_recipients(message, "CcRecipients", cc)
    _add_recipients(message, "BccRecipients", bcc)
    return _serialize(envelope)


def _add_find_item_shape(find_item: ET.Element) -> None:
    item_shape = ET.SubElement(find_item, q(MESSAGES_NS, "ItemShape"))
    ET.SubElement(item_shape, q(TYPES_NS, "BaseShape")).text = "IdOnly"
    additional = ET.SubElement(item_shape, q(TYPES_NS, "AdditionalProperties"))
    fields = [
        "item:Subject",
        "item:DateTimeReceived",
        "item:DateTimeSent",
        "item:DateTimeCreated",
        "item:LastModifiedTime",
        "item:HasAttachments",
        "item:Importance",
        "item:Size",
        "item:DisplayTo",
        "item:DisplayCc",
        "item:ConversationId",
        "item:ParentFolderId",
        "message:From",
        "message:Sender",
        "message:IsRead",
        "item:IsDraft",
        "message:InternetMessageId",
    ]
    for field_uri in fields:
        ET.SubElement(additional, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})


def _contains_filter(field_uri: str, value: str) -> ET.Element:
    element = ET.Element(
        q(TYPES_NS, "Contains"),
        {"ContainmentMode": "Substring", "ContainmentComparison": "IgnoreCase"},
    )
    ET.SubElement(element, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})
    ET.SubElement(element, q(TYPES_NS, "Constant"), {"Value": value})
    return element


def _constant_filter(operator: str, field_uri: str, value: str) -> ET.Element:
    element = ET.Element(q(TYPES_NS, operator))
    ET.SubElement(element, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})
    wrapper = ET.SubElement(element, q(TYPES_NS, "FieldURIOrConstant"))
    ET.SubElement(wrapper, q(TYPES_NS, "Constant"), {"Value": value})
    return element


def _sender_smtp_filter(value: str) -> ET.Element:
    element = ET.Element(q(TYPES_NS, "IsEqualTo"))
    ET.SubElement(
        element,
        q(TYPES_NS, "ExtendedFieldURI"),
        {"PropertyTag": "0x5D01", "PropertyType": "String"},
    )
    wrapper = ET.SubElement(element, q(TYPES_NS, "FieldURIOrConstant"))
    ET.SubElement(wrapper, q(TYPES_NS, "Constant"), {"Value": value})
    return element


def _or_filter(*filters: ET.Element) -> ET.Element:
    node = ET.Element(q(TYPES_NS, "Or"))
    for item in filters:
        node.append(item)
    return node


def build_find_items_request(
    *,
    exchange_version: str,
    folder: str,
    limit: int,
    offset: int,
    criteria: SearchCriteria | None = None,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    find_item = ET.SubElement(soap_body, q(MESSAGES_NS, "FindItem"), {"Traversal": "Shallow"})
    _add_find_item_shape(find_item)
    ET.SubElement(
        find_item,
        q(MESSAGES_NS, "IndexedPageItemView"),
        {"MaxEntriesReturned": str(limit), "Offset": str(offset), "BasePoint": "Beginning"},
    )

    filters: list[ET.Element] = []
    criteria = criteria or SearchCriteria()
    if criteria.subject_contains:
        filters.append(_contains_filter("item:Subject", criteria.subject_contains))
    if criteria.sender:
        filters.append(_sender_smtp_filter(criteria.sender))
    if criteria.to_contains:
        filters.append(_contains_filter("item:DisplayTo", criteria.to_contains))
    if criteria.cc_contains:
        filters.append(_contains_filter("item:DisplayCc", criteria.cc_contains))
    if criteria.participant_contains:
        participant = criteria.participant_contains
        filters.append(
            _or_filter(
                _sender_smtp_filter(participant),
                _contains_filter("item:DisplayTo", participant),
                _contains_filter("item:DisplayCc", participant),
            )
        )
    if criteria.unread_only is True:
        filters.append(_constant_filter("IsEqualTo", "message:IsRead", "false"))
    elif criteria.unread_only is False:
        filters.append(_constant_filter("IsEqualTo", "message:IsRead", "true"))
    if criteria.has_attachments is not None:
        filters.append(
            _constant_filter(
                "IsEqualTo", "item:HasAttachments", "true" if criteria.has_attachments else "false"
            )
        )
    if criteria.conversation_id:
        filters.append(_constant_filter("IsEqualTo", "item:ConversationId", criteria.conversation_id))
    if criteria.internet_message_id:
        filters.append(
            _constant_filter("IsEqualTo", "message:InternetMessageId", criteria.internet_message_id)
        )

    date_field = (
        "item:DateTimeSent"
        if folder == "sentitems"
        else "item:LastModifiedTime"
        if folder in {"drafts", "outbox"}
        else "item:DateTimeReceived"
    )
    if criteria.after:
        filters.append(_constant_filter("IsGreaterThanOrEqualTo", date_field, criteria.after))
    if criteria.before:
        filters.append(_constant_filter("IsLessThan", date_field, criteria.before))

    if filters:
        restriction = ET.SubElement(find_item, q(MESSAGES_NS, "Restriction"))
        if len(filters) == 1:
            restriction.append(filters[0])
        else:
            conjunction = ET.SubElement(restriction, q(TYPES_NS, "And"))
            for element in filters:
                conjunction.append(element)

    sort_order = ET.SubElement(find_item, q(MESSAGES_NS, "SortOrder"))
    sort_field = date_field
    field_order = ET.SubElement(sort_order, q(TYPES_NS, "FieldOrder"), {"Order": "Descending"})
    ET.SubElement(field_order, q(TYPES_NS, "FieldURI"), {"FieldURI": sort_field})

    parent_ids = ET.SubElement(find_item, q(MESSAGES_NS, "ParentFolderIds"))
    ET.SubElement(parent_ids, q(TYPES_NS, "DistinguishedFolderId"), {"Id": folder})
    return _serialize(envelope)


def build_resolve_names_request(
    *,
    exchange_version: str,
    query: str,
    search_scope: str = "ContactsActiveDirectory",  # Contacts first, then Global Address List
    return_full_contact_data: bool = True,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    resolve = ET.SubElement(
        soap_body,
        q(MESSAGES_NS, "ResolveNames"),
        {
            "ReturnFullContactData": "true" if return_full_contact_data else "false",
            "SearchScope": search_scope,
            "ContactDataShape": "Default",
        },
    )
    if search_scope in {"Contacts", "ActiveDirectoryContacts", "ContactsActiveDirectory"}:
        parent_ids = ET.SubElement(resolve, q(MESSAGES_NS, "ParentFolderIds"))
        ET.SubElement(parent_ids, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "contacts"})
    ET.SubElement(resolve, q(MESSAGES_NS, "UnresolvedEntry")).text = query
    return _serialize(envelope)



def build_get_item_identity_request(*, exchange_version: str, item_id: str) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    get_item = ET.SubElement(soap_body, q(MESSAGES_NS, "GetItem"))
    item_shape = ET.SubElement(get_item, q(MESSAGES_NS, "ItemShape"))
    ET.SubElement(item_shape, q(TYPES_NS, "BaseShape")).text = "IdOnly"
    item_ids = ET.SubElement(get_item, q(MESSAGES_NS, "ItemIds"))
    _item_id(item_ids, item_id)
    return _serialize(envelope)


def build_get_item_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str | None = None,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    get_item = ET.SubElement(soap_body, q(MESSAGES_NS, "GetItem"))
    item_shape = ET.SubElement(get_item, q(MESSAGES_NS, "ItemShape"))
    ET.SubElement(item_shape, q(TYPES_NS, "BaseShape")).text = "AllProperties"
    ET.SubElement(item_shape, q(TYPES_NS, "BodyType")).text = "HTML"
    # UniqueBody is the Exchange-native representation of the body content that
    # is unique to this conversation item.  Request it explicitly so callers do
    # not have to infer reply-history boundaries from Outlook-generated HTML.
    additional = ET.SubElement(item_shape, q(TYPES_NS, "AdditionalProperties"))
    ET.SubElement(additional, q(TYPES_NS, "FieldURI"), {"FieldURI": "item:UniqueBody"})
    item_ids = ET.SubElement(get_item, q(MESSAGES_NS, "ItemIds"))
    _item_id(item_ids, item_id, change_key)
    return _serialize(envelope)


def build_get_attachments_request(
    *,
    exchange_version: str,
    attachment_ids: list[str],
) -> bytes:
    if not attachment_ids:
        raise ValueError("attachment_ids 不能为空。")
    envelope, soap_body = _base_envelope(exchange_version)
    get_attachment = ET.SubElement(soap_body, q(MESSAGES_NS, "GetAttachment"))
    shape = ET.SubElement(get_attachment, q(MESSAGES_NS, "AttachmentShape"))
    ET.SubElement(shape, q(TYPES_NS, "IncludeMimeContent")).text = "false"
    ids = ET.SubElement(get_attachment, q(MESSAGES_NS, "AttachmentIds"))
    for attachment_id in attachment_ids:
        value = attachment_id.strip()
        if not value:
            raise ValueError("attachment_id 不能为空。")
        ET.SubElement(ids, q(TYPES_NS, "AttachmentId"), {"Id": value})
    return _serialize(envelope)


def build_reply_draft_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str | None,
    body_html: str,
    reply_all: bool,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    create_item = ET.SubElement(
        soap_body, q(MESSAGES_NS, "CreateItem"), {"MessageDisposition": "SaveOnly"}
    )
    saved_folder = ET.SubElement(create_item, q(MESSAGES_NS, "SavedItemFolderId"))
    ET.SubElement(saved_folder, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "drafts"})
    items = ET.SubElement(create_item, q(MESSAGES_NS, "Items"))
    response_type = "ReplyAllToItem" if reply_all else "ReplyToItem"
    response = ET.SubElement(items, q(TYPES_NS, response_type))
    _reference_item_id(response, item_id, change_key)
    body = ET.SubElement(response, q(TYPES_NS, "NewBodyContent"), {"BodyType": "HTML"})
    body.text = body_html
    return _serialize(envelope)


def build_forward_draft_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str | None,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    body_html: str,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    create_item = ET.SubElement(
        soap_body, q(MESSAGES_NS, "CreateItem"), {"MessageDisposition": "SaveOnly"}
    )
    saved_folder = ET.SubElement(create_item, q(MESSAGES_NS, "SavedItemFolderId"))
    ET.SubElement(saved_folder, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "drafts"})
    items = ET.SubElement(create_item, q(MESSAGES_NS, "Items"))
    forward = ET.SubElement(items, q(TYPES_NS, "ForwardItem"))
    _add_recipients(forward, "ToRecipients", to)
    _add_recipients(forward, "CcRecipients", cc)
    _add_recipients(forward, "BccRecipients", bcc)
    _reference_item_id(forward, item_id, change_key)
    body = ET.SubElement(forward, q(TYPES_NS, "NewBodyContent"), {"BodyType": "HTML"})
    body.text = body_html
    return _serialize(envelope)


def _set_field(updates: ET.Element, field_uri: str, element_name: str, value: str, *, body_type: str | None = None) -> None:
    set_field = ET.SubElement(updates, q(TYPES_NS, "SetItemField"))
    ET.SubElement(set_field, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})
    message = ET.SubElement(set_field, q(TYPES_NS, "Message"))
    attrs = {"BodyType": body_type} if body_type else {}
    ET.SubElement(message, q(TYPES_NS, element_name), attrs).text = value


def _set_or_delete_recipients(
    updates: ET.Element,
    field_uri: str,
    element_name: str,
    recipients: list[str],
) -> None:
    if not recipients:
        delete_field = ET.SubElement(updates, q(TYPES_NS, "DeleteItemField"))
        ET.SubElement(delete_field, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})
        return
    set_field = ET.SubElement(updates, q(TYPES_NS, "SetItemField"))
    ET.SubElement(set_field, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})
    message = ET.SubElement(set_field, q(TYPES_NS, "Message"))
    _add_recipients(message, element_name, recipients)


def build_update_draft_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str,
    subject: str | None = None,
    body_html: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    update_item = ET.SubElement(
        soap_body,
        q(MESSAGES_NS, "UpdateItem"),
        {"ConflictResolution": "AutoResolve", "MessageDisposition": "SaveOnly"},
    )
    item_changes = ET.SubElement(update_item, q(MESSAGES_NS, "ItemChanges"))
    item_change = ET.SubElement(item_changes, q(TYPES_NS, "ItemChange"))
    _item_id(item_change, item_id, change_key)
    updates = ET.SubElement(item_change, q(TYPES_NS, "Updates"))
    if subject is not None:
        _set_field(updates, "item:Subject", "Subject", subject)
    if body_html is not None:
        _set_field(updates, "item:Body", "Body", body_html, body_type="HTML")
    if to is not None:
        _set_or_delete_recipients(updates, "message:ToRecipients", "ToRecipients", to)
    if cc is not None:
        _set_or_delete_recipients(updates, "message:CcRecipients", "CcRecipients", cc)
    if bcc is not None:
        _set_or_delete_recipients(updates, "message:BccRecipients", "BccRecipients", bcc)
    if importance is not None:
        _set_field(updates, "item:Importance", "Importance", importance)
    return _serialize(envelope)


def build_create_attachment_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str | None,
    filename: str,
    content_type: str,
    content: bytes,
    is_inline: bool = False,
    content_id: str | None = None,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    create_attachment = ET.SubElement(soap_body, q(MESSAGES_NS, "CreateAttachment"))
    parent = ET.SubElement(create_attachment, q(MESSAGES_NS, "ParentItemId"))
    parent.attrib["Id"] = item_id
    if change_key:
        parent.attrib["ChangeKey"] = change_key
    attachments = ET.SubElement(create_attachment, q(MESSAGES_NS, "Attachments"))
    file_attachment = ET.SubElement(attachments, q(TYPES_NS, "FileAttachment"))
    ET.SubElement(file_attachment, q(TYPES_NS, "Name")).text = filename
    ET.SubElement(file_attachment, q(TYPES_NS, "ContentType")).text = content_type
    if content_id:
        ET.SubElement(file_attachment, q(TYPES_NS, "ContentId")).text = content_id
    ET.SubElement(file_attachment, q(TYPES_NS, "IsInline")).text = "true" if is_inline else "false"
    ET.SubElement(file_attachment, q(TYPES_NS, "Content")).text = base64.b64encode(content).decode("ascii")
    return _serialize(envelope)


def _add_availability_timezone_context(
    envelope: ET.Element, *, time_zone_id: str = "UTC"
) -> None:
    """Add the Exchange 2010+ session time-zone header used by availability.

    ``GetUserAvailability`` has a legacy body-level ``TimeZone`` structure.
    A fake no-DST definition with coincident standard/daylight transitions is
    rejected by some on-premises Exchange builds.  Exchange 2010 and later
    can instead resolve a server-known Windows time-zone identifier from the
    SOAP ``TimeZoneContext`` header.  Availability requests are deliberately
    scoped to UTC; local IANA zones are applied only by the workflow layer.
    """
    header = envelope.find(q(SOAP_NS, "Header"))
    if header is None:
        raise ValueError("SOAP Header 缺失。")
    context = ET.SubElement(header, q(TYPES_NS, "TimeZoneContext"))
    ET.SubElement(
        context, q(TYPES_NS, "TimeZoneDefinition"), {"Id": time_zone_id}
    )


def build_get_user_availability_request(
    *,
    exchange_version: str,
    attendees: list[dict[str, str]],
    start: str,
    end: str,
    interval_minutes: int,
    requested_view: str = "DetailedMerged",
    time_zone_id: str = "UTC",
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    _add_availability_timezone_context(envelope, time_zone_id=time_zone_id)
    request = ET.SubElement(soap_body, q(MESSAGES_NS, "GetUserAvailabilityRequest"))
    mailbox_array = ET.SubElement(request, q(MESSAGES_NS, "MailboxDataArray"))
    for attendee in attendees:
        mailbox_data = ET.SubElement(mailbox_array, q(TYPES_NS, "MailboxData"))
        email = ET.SubElement(mailbox_data, q(TYPES_NS, "Email"))
        ET.SubElement(email, q(TYPES_NS, "Address")).text = attendee["email"]
        ET.SubElement(email, q(TYPES_NS, "RoutingType")).text = "SMTP"
        ET.SubElement(mailbox_data, q(TYPES_NS, "AttendeeType")).text = attendee.get(
            "attendee_type", "Required"
        )
        ET.SubElement(mailbox_data, q(TYPES_NS, "ExcludeConflicts")).text = "false"
    options = ET.SubElement(request, q(TYPES_NS, "FreeBusyViewOptions"))
    window = ET.SubElement(options, q(TYPES_NS, "TimeWindow"))
    ET.SubElement(window, q(TYPES_NS, "StartTime")).text = start
    ET.SubElement(window, q(TYPES_NS, "EndTime")).text = end
    ET.SubElement(options, q(TYPES_NS, "MergedFreeBusyIntervalInMinutes")).text = str(
        interval_minutes
    )
    ET.SubElement(options, q(TYPES_NS, "RequestedView")).text = requested_view
    return _serialize(envelope)


def _add_calendar_item_shape(parent: ET.Element, *, all_properties: bool = False) -> None:
    shape = ET.SubElement(parent, q(MESSAGES_NS, "ItemShape"))
    ET.SubElement(shape, q(TYPES_NS, "BaseShape")).text = (
        "AllProperties" if all_properties else "IdOnly"
    )
    if all_properties:
        ET.SubElement(shape, q(TYPES_NS, "BodyType")).text = "HTML"
        return
    additional = ET.SubElement(shape, q(TYPES_NS, "AdditionalProperties"))
    for field_uri in (
        "item:Subject",
        "calendar:Start",
        "calendar:End",
        "calendar:Location",
        "calendar:IsMeeting",
        "calendar:IsAllDayEvent",
        "calendar:LegacyFreeBusyStatus",
        "calendar:Organizer",
        "calendar:RequiredAttendees",
        "calendar:OptionalAttendees",
        "item:DateTimeCreated",
        "item:LastModifiedTime",
    ):
        ET.SubElement(additional, q(TYPES_NS, "FieldURI"), {"FieldURI": field_uri})


def build_find_calendar_items_request(
    *,
    exchange_version: str,
    start: str,
    end: str,
    max_entries: int,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    find_item = ET.SubElement(soap_body, q(MESSAGES_NS, "FindItem"), {"Traversal": "Shallow"})
    _add_calendar_item_shape(find_item)
    ET.SubElement(
        find_item,
        q(MESSAGES_NS, "CalendarView"),
        {"StartDate": start, "EndDate": end, "MaxEntriesReturned": str(max_entries)},
    )
    parent_ids = ET.SubElement(find_item, q(MESSAGES_NS, "ParentFolderIds"))
    ET.SubElement(parent_ids, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "calendar"})
    return _serialize(envelope)


def build_get_calendar_item_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str | None = None,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    get_item = ET.SubElement(soap_body, q(MESSAGES_NS, "GetItem"))
    _add_calendar_item_shape(get_item, all_properties=True)
    item_ids = ET.SubElement(get_item, q(MESSAGES_NS, "ItemIds"))
    _item_id(item_ids, item_id, change_key)
    return _serialize(envelope)


def _add_attendees(calendar_item: ET.Element, element_name: str, attendees: list[str]) -> None:
    if not attendees:
        return
    parent = ET.SubElement(calendar_item, q(TYPES_NS, element_name))
    for address in attendees:
        attendee = ET.SubElement(parent, q(TYPES_NS, "Attendee"))
        mailbox = ET.SubElement(attendee, q(TYPES_NS, "Mailbox"))
        ET.SubElement(mailbox, q(TYPES_NS, "EmailAddress")).text = address


def build_create_meeting_request(
    *,
    exchange_version: str,
    subject: str,
    body_html: str,
    start: str,
    end: str,
    required_attendees: list[str],
    optional_attendees: list[str],
    location: str | None,
    reminder_minutes: int,
    send_invitations: bool,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    create_item = ET.SubElement(
        soap_body,
        q(MESSAGES_NS, "CreateItem"),
        {
            "SendMeetingInvitations": (
                "SendToAllAndSaveCopy" if send_invitations else "SendToNone"
            )
        },
    )
    saved_folder = ET.SubElement(create_item, q(MESSAGES_NS, "SavedItemFolderId"))
    ET.SubElement(saved_folder, q(TYPES_NS, "DistinguishedFolderId"), {"Id": "calendar"})
    items = ET.SubElement(create_item, q(MESSAGES_NS, "Items"))
    calendar_item = ET.SubElement(items, q(TYPES_NS, "CalendarItem"))
    ET.SubElement(calendar_item, q(TYPES_NS, "Subject")).text = subject
    body = ET.SubElement(calendar_item, q(TYPES_NS, "Body"), {"BodyType": "HTML"})
    body.text = body_html
    ET.SubElement(calendar_item, q(TYPES_NS, "ReminderIsSet")).text = (
        "true" if reminder_minutes > 0 else "false"
    )
    if reminder_minutes > 0:
        ET.SubElement(calendar_item, q(TYPES_NS, "ReminderMinutesBeforeStart")).text = str(
            reminder_minutes
        )
    ET.SubElement(calendar_item, q(TYPES_NS, "Start")).text = start
    ET.SubElement(calendar_item, q(TYPES_NS, "End")).text = end
    ET.SubElement(calendar_item, q(TYPES_NS, "IsAllDayEvent")).text = "false"
    ET.SubElement(calendar_item, q(TYPES_NS, "LegacyFreeBusyStatus")).text = "Busy"
    if location and location.strip():
        ET.SubElement(calendar_item, q(TYPES_NS, "Location")).text = location.strip()
    _add_attendees(calendar_item, "RequiredAttendees", required_attendees)
    _add_attendees(calendar_item, "OptionalAttendees", optional_attendees)
    return _serialize(envelope)


def build_delete_calendar_item_request(
    *,
    exchange_version: str,
    item_id: str,
    change_key: str | None,
) -> bytes:
    envelope, soap_body = _base_envelope(exchange_version)
    delete_item = ET.SubElement(
        soap_body,
        q(MESSAGES_NS, "DeleteItem"),
        {
            "DeleteType": "HardDelete",
            "SendMeetingCancellations": "SendToNone",
            "AffectedTaskOccurrences": "AllOccurrences",
        },
    )
    item_ids = ET.SubElement(delete_item, q(MESSAGES_NS, "ItemIds"))
    _item_id(item_ids, item_id, change_key)
    return _serialize(envelope)
