from xml.etree import ElementTree as ET

import pytest

from exchange_ews_mcp.xml_builder import (
    MESSAGES_NS, TYPES_NS, build_create_meeting_request,
    build_find_calendar_items_request, build_get_user_availability_request,
    build_update_meeting_request, q,
)


def test_availability_request_contains_mailboxes_and_utc_window() -> None:
    xml = build_get_user_availability_request(
        exchange_version="Exchange2010_SP2",
        attendees=[
            {"email": "self@example.com", "attendee_type": "Organizer"},
            {"email": "alice@example.com", "attendee_type": "Required"},
        ],
        start="2026-08-03T00:00:00Z", end="2026-08-10T00:00:00Z",
        interval_minutes=30,
    )
    root = ET.fromstring(xml)
    assert root.find(f".//{q(MESSAGES_NS, 'GetUserAvailabilityRequest')}") is not None
    addresses = [node.text for node in root.findall(f".//{q(TYPES_NS, 'Address')}")]
    assert addresses == ["self@example.com", "alice@example.com"]
    assert root.find(f".//{q(TYPES_NS, 'RequestedView')}").text == "DetailedMerged"
    assert root.find(f".//{q(TYPES_NS, 'MergedFreeBusyIntervalInMinutes')}").text == "30"
    context = root.find(f".//{q(TYPES_NS, 'TimeZoneContext')}")
    assert context is not None
    definition = context.find(q(TYPES_NS, "TimeZoneDefinition"))
    assert definition is not None
    assert definition.attrib["Id"] == "UTC"
    request = root.find(f".//{q(MESSAGES_NS, 'GetUserAvailabilityRequest')}")
    assert request is not None
    assert request.find(q(TYPES_NS, "TimeZone")) is None


def test_calendar_view_request_uses_calendar_folder() -> None:
    xml = build_find_calendar_items_request(
        exchange_version="Exchange2010_SP2",
        start="2026-08-03T00:00:00Z", end="2026-08-10T00:00:00Z",
        max_entries=100,
    )
    root = ET.fromstring(xml)
    view = root.find(f".//{q(MESSAGES_NS, 'CalendarView')}")
    assert view is not None
    assert view.attrib["MaxEntriesReturned"] == "100"
    folder = root.find(f".//{q(TYPES_NS, 'DistinguishedFolderId')}")
    assert folder.attrib["Id"] == "calendar"


def test_create_meeting_defaults_to_send_to_none() -> None:
    xml = build_create_meeting_request(
        exchange_version="Exchange2010_SP2", subject="Test", body_html="<p>x</p>",
        start="2026-08-03T01:00:00Z", end="2026-08-03T02:00:00Z",
        required_attendees=["alice@example.com"], optional_attendees=[],
        location="Room", reminder_minutes=15, send_invitations=False,
    )
    root = ET.fromstring(xml)
    create = root.find(f".//{q(MESSAGES_NS, 'CreateItem')}")
    assert create.attrib["SendMeetingInvitations"] == "SendToNone"
    assert root.find(f".//{q(TYPES_NS, 'RequiredAttendees')}") is not None


def test_create_meeting_send_mode_is_explicit() -> None:
    xml = build_create_meeting_request(
        exchange_version="Exchange2010_SP2", subject="Test", body_html="<p>x</p>",
        start="2026-08-03T01:00:00Z", end="2026-08-03T02:00:00Z",
        required_attendees=["alice@example.com"], optional_attendees=[],
        location=None, reminder_minutes=0, send_invitations=True,
    )
    root = ET.fromstring(xml)
    create = root.find(f".//{q(MESSAGES_NS, 'CreateItem')}")
    assert create.attrib["SendMeetingInvitations"] == "SendToAllAndSaveCopy"


def test_update_meeting_defaults_to_no_attendee_notifications() -> None:
    xml = build_update_meeting_request(
        exchange_version="Exchange2010_SP2",
        item_id="CAL1",
        change_key="CK1",
        subject="Updated",
        body_html="<p>new</p>",
        start="2026-08-03T03:00:00Z",
        end="2026-08-03T04:00:00Z",
        location="Room 2",
        required_attendees=["alice@example.com"],
        optional_attendees=[],
        reminder_minutes=0,
    )
    root = ET.fromstring(xml)
    update = root.find(f".//{q(MESSAGES_NS, 'UpdateItem')}")
    assert update is not None
    assert update.attrib["ConflictResolution"] == "NeverOverwrite"
    assert update.attrib["MessageDisposition"] == "SaveOnly"
    assert update.attrib["SendMeetingInvitationsOrCancellations"] == "SendToNone"
    assert root.find(f".//{q(TYPES_NS, 'SetItemField')}/{q(TYPES_NS, 'CalendarItem')}") is not None
    field_uris = [
        node.attrib["FieldURI"]
        for node in root.findall(f".//{q(TYPES_NS, 'FieldURI')}")
    ]
    assert "item:Subject" in field_uris
    assert "calendar:Start" in field_uris
    assert "calendar:RequiredAttendees" in field_uris
    assert "calendar:OptionalAttendees" in field_uris
    assert "item:ReminderIsSet" in field_uris
    assert "item:ReminderMinutesBeforeStart" not in field_uris


def test_send_existing_meeting_uses_send_to_all_and_save_copy() -> None:
    xml = build_update_meeting_request(
        exchange_version="Exchange2010_SP2",
        item_id="CAL1",
        change_key="CK1",
        subject="Planning",
        send_invitations=True,
    )
    root = ET.fromstring(xml)
    update = root.find(f".//{q(MESSAGES_NS, 'UpdateItem')}")
    assert update is not None
    assert update.attrib["MessageDisposition"] == "SaveOnly"
    assert update.attrib["SendMeetingInvitationsOrCancellations"] == "SendToAllAndSaveCopy"


@pytest.mark.parametrize("attendee_type", ["Room", "Resource"])
def test_availability_request_rejects_room_and_resource(attendee_type: str) -> None:
    with pytest.raises(ValueError, match="会议室/资源邮箱忙闲查询未启用"):
        build_get_user_availability_request(
            exchange_version="Exchange2010_SP2",
            attendees=[{"email": "space@example.com", "attendee_type": attendee_type}],
            start="2026-08-03T00:00:00",
            end="2026-08-04T00:00:00",
            interval_minutes=30,
        )
