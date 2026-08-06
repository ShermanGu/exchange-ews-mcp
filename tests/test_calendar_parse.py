from requests import Response

from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import EwsClient


class FakeSession:
    def __init__(self, bodies: list[bytes]) -> None:
        self.bodies = bodies
        self.auth = None
        self.headers = {}
        self.payloads = []

    def post(self, *args, **kwargs):
        self.payloads.append(kwargs["data"])
        response = Response()
        response.status_code = 200
        response._content = self.bodies.pop(0)
        response.encoding = "utf-8"
        return response


def config() -> AppConfig:
    return AppConfig(ews_url="https://mail.example.com/EWS/Exchange.asmx", username="D\\u")


AVAILABILITY = b'''<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"><soap:Body>
 <m:GetUserAvailabilityResponse><m:FreeBusyResponseArray><m:FreeBusyResponse>
 <m:ResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode></m:ResponseMessage>
 <m:FreeBusyView><t:FreeBusyViewType>DetailedMerged</t:FreeBusyViewType>
 <t:CalendarEventArray><t:CalendarEvent><t:StartTime>2026-08-03T02:00:00Z</t:StartTime>
 <t:EndTime>2026-08-03T03:00:00Z</t:EndTime><t:BusyType>Busy</t:BusyType>
 <t:CalendarEventDetails><t:Subject>Meeting</t:Subject><t:Location>Room</t:Location>
 <t:IsMeeting>true</t:IsMeeting><t:IsRecurring>false</t:IsRecurring><t:IsPrivate>false</t:IsPrivate>
 </t:CalendarEventDetails></t:CalendarEvent></t:CalendarEventArray>
 <t:WorkingHours><t:TimeZone><t:Bias>-480</t:Bias></t:TimeZone>
 <t:WorkingPeriodArray><t:WorkingPeriod><t:DayOfWeek>Monday Tuesday Wednesday Thursday Friday</t:DayOfWeek>
 <t:StartTimeInMinutes>540</t:StartTimeInMinutes><t:EndTimeInMinutes>1080</t:EndTimeInMinutes>
 </t:WorkingPeriod></t:WorkingPeriodArray></t:WorkingHours></m:FreeBusyView>
 </m:FreeBusyResponse></m:FreeBusyResponseArray></m:GetUserAvailabilityResponse>
 </soap:Body></soap:Envelope>'''

CALENDAR_LIST = b'''<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"><soap:Body>
 <m:FindItemResponse><m:ResponseMessages><m:FindItemResponseMessage ResponseClass="Success">
 <m:ResponseCode>NoError</m:ResponseCode><m:RootFolder><t:Items><t:CalendarItem>
 <t:ItemId Id="CAL1" ChangeKey="CK1"/><t:Subject>Planning</t:Subject>
 <t:Start>2026-08-03T02:00:00Z</t:Start><t:End>2026-08-03T03:00:00Z</t:End>
 <t:IsMeeting>true</t:IsMeeting><t:IsAllDayEvent>false</t:IsAllDayEvent>
 </t:CalendarItem></t:Items></m:RootFolder></m:FindItemResponseMessage></m:ResponseMessages>
 </m:FindItemResponse></soap:Body></soap:Envelope>'''

CREATE = b'''<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"><soap:Body>
 <m:CreateItemResponse><m:ResponseMessages><m:CreateItemResponseMessage ResponseClass="Success">
 <m:ResponseCode>NoError</m:ResponseCode><m:Items><t:CalendarItem><t:ItemId Id="NEWCAL" ChangeKey="NEWCK"/>
 </t:CalendarItem></m:Items></m:CreateItemResponseMessage></m:ResponseMessages></m:CreateItemResponse>
 </soap:Body></soap:Envelope>'''


def test_parse_availability_and_working_hours() -> None:
    client = EwsClient(config(), "secret", session=FakeSession([AVAILABILITY]))
    result = client.get_user_availability(
        attendees=[{"email": "self@example.com", "attendee_type": "Organizer"}],
        start="2026-08-03T00:00:00Z", end="2026-08-04T00:00:00Z",
    )
    person = result["attendees"][0]
    assert person["response_code"] == "NoError"
    assert person["events"][0]["subject"] == "Meeting"
    working = person["working_hours"]
    assert working["working_periods"][0]["start_minutes"] == 540
    assert working["working_periods"][0]["start"] == "09:00"
    assert working["working_periods"][0]["end"] == "18:00"
    assert working["time_zone"]["bias_minutes"] == -480
    assert working["time_zone"]["utc_offset"] == "+08:00"
    assert working["time_zone"]["observes_daylight_saving"] is False
    assert working["time_zone"]["standard_transition"] is None
    assert working["time_zone"]["daylight_transition"] is None
    assert result["ews_time_zone_id"] == "UTC"
    assert result["time_zone_transport"] == "timezone_context"


def test_parse_calendar_list_and_create() -> None:
    client = EwsClient(config(), "secret", session=FakeSession([CALENDAR_LIST, CREATE]))
    listed = client.list_calendar_events(start="2026-08-03T00:00:00Z", end="2026-08-04T00:00:00Z")
    assert listed["items"][0]["item_id"] == "CAL1"
    created = client.create_meeting(
        subject="Test", body_html="<p>x</p>", start="2026-08-03T02:00:00Z",
        end="2026-08-03T03:00:00Z", required_attendees=["self@example.com"],
    )
    assert created.item_id == "NEWCAL"
    assert created.sent is False


AVAILABILITY_WITH_OFFSET_FREE_EVENTS = AVAILABILITY.replace(
    b"2026-08-03T02:00:00Z", b"2026-08-03T02:00:00"
).replace(
    b"2026-08-03T03:00:00Z", b"2026-08-03T03:00:00"
)


def test_availability_offset_free_event_times_are_normalized_to_utc() -> None:
    client = EwsClient(
        config(), "secret", session=FakeSession([AVAILABILITY_WITH_OFFSET_FREE_EVENTS])
    )
    result = client.get_user_availability(
        attendees=[{"email": "self@example.com", "attendee_type": "Organizer"}],
        start="2026-08-03T00:00:00Z", end="2026-08-04T00:00:00Z",
    )
    event = result["attendees"][0]["events"][0]
    assert event["start"] == "2026-08-03T02:00:00Z"
    assert event["end"] == "2026-08-03T03:00:00Z"


AVAILABILITY_WITH_ZERO_DST_PLACEHOLDERS = AVAILABILITY.replace(
    b"<t:TimeZone><t:Bias>-480</t:Bias></t:TimeZone>",
    b"""<t:TimeZone><t:Bias>-480</t:Bias>
    <t:StandardTime><t:Bias>0</t:Bias><t:Time>00:00:00</t:Time>
    <t:DayOrder>0</t:DayOrder><t:Month>0</t:Month><t:DayOfWeek>Sunday</t:DayOfWeek></t:StandardTime>
    <t:DaylightTime><t:Bias>0</t:Bias><t:Time>00:00:00</t:Time>
    <t:DayOrder>0</t:DayOrder><t:Month>0</t:Month><t:DayOfWeek>Sunday</t:DayOfWeek></t:DaylightTime>
    </t:TimeZone>"""
)


def test_zero_dst_placeholders_are_presented_as_no_daylight_saving() -> None:
    client = EwsClient(
        config(), "secret", session=FakeSession([AVAILABILITY_WITH_ZERO_DST_PLACEHOLDERS])
    )
    result = client.get_user_availability(
        attendees=[{"email": "self@example.com", "attendee_type": "Organizer"}],
        start="2026-08-03T00:00:00Z", end="2026-08-04T00:00:00Z",
    )
    zone = result["attendees"][0]["working_hours"]["time_zone"]
    assert zone == {
        "bias_minutes": -480,
        "utc_offset": "+08:00",
        "observes_daylight_saving": False,
        "standard_transition": None,
        "daylight_transition": None,
        "standard_utc_offset": "+08:00",
        "daylight_utc_offset": None,
    }


AVAILABILITY_WITH_DST = AVAILABILITY.replace(
    b"<t:TimeZone><t:Bias>-480</t:Bias></t:TimeZone>",
    b"""<t:TimeZone><t:Bias>480</t:Bias>
    <t:StandardTime><t:Bias>0</t:Bias><t:Time>02:00:00</t:Time>
    <t:DayOrder>1</t:DayOrder><t:Month>11</t:Month><t:DayOfWeek>Sunday</t:DayOfWeek></t:StandardTime>
    <t:DaylightTime><t:Bias>-60</t:Bias><t:Time>02:00:00</t:Time>
    <t:DayOrder>2</t:DayOrder><t:Month>3</t:Month><t:DayOfWeek>Sunday</t:DayOfWeek></t:DaylightTime>
    </t:TimeZone>"""
)


def test_valid_dst_rules_are_preserved_in_normalized_shape() -> None:
    client = EwsClient(config(), "secret", session=FakeSession([AVAILABILITY_WITH_DST]))
    result = client.get_user_availability(
        attendees=[{"email": "self@example.com", "attendee_type": "Organizer"}],
        start="2026-08-03T00:00:00Z", end="2026-08-04T00:00:00Z",
    )
    zone = result["attendees"][0]["working_hours"]["time_zone"]
    assert zone["observes_daylight_saving"] is True
    assert zone["utc_offset"] == "-08:00"
    assert zone["standard_utc_offset"] == "-08:00"
    assert zone["daylight_utc_offset"] == "-07:00"
    assert zone["standard_transition"]["month"] == 11
    assert zone["daylight_transition"]["month"] == 3
