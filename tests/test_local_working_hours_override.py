from datetime import datetime, timezone

from exchange_ews_mcp.calendar_utils import (
    apply_current_user_working_hours_override,
    ews_working_intervals,
)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def raw_exchange_hours():
    return {
        "time_zone": {
            "bias_minutes": -480,
            "utc_offset": "+08:00",
            "observes_daylight_saving": False,
            "standard_transition": None,
            "daylight_transition": None,
        },
        "working_periods": [{
            "days": [],
            "start_minutes": 480,
            "end_minutes": 1020,
            "start": "08:00",
            "end": "17:00",
        }],
    }


def test_current_user_local_working_hours_override_exchange_values() -> None:
    result = apply_current_user_working_hours_override(
        {
            "start": "2026-08-03T00:00:00Z",
            "end": "2026-08-04T00:00:00Z",
            "attendees": [{
                "email": "self@company.com",
                "attendee_type": "Organizer",
                "working_hours": raw_exchange_hours(),
                "events": [],
            }],
        },
        current_user_email="self@company.com",
        zone_name="Asia/Shanghai",
        workday_start="09:30",
        workday_end="18:00",
        workdays=[0, 1, 2, 3, 4],
    )
    person = result["attendees"][0]
    assert person["working_hours_source"] == "local_config_override"
    assert person["working_hours"]["working_periods"][0]["days"] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
    ]
    assert person["working_hours"]["working_periods"][0]["start"] == "09:30"
    assert person["working_hours"]["working_periods"][0]["end"] == "18:00"
    assert person["exchange_working_hours"]["working_periods"][0]["start"] == "08:00"


def test_local_override_is_used_by_scheduling_intervals() -> None:
    result = apply_current_user_working_hours_override(
        {
            "start": "2026-08-03T00:00:00Z",
            "end": "2026-08-04T00:00:00Z",
            "attendees": [{
                "email": "self@company.com",
                "attendee_type": "Organizer",
                "working_hours": raw_exchange_hours(),
                "events": [],
            }],
        },
        current_user_email="self@company.com",
        zone_name="Asia/Shanghai",
        workday_start="09:30",
        workday_end="18:00",
        workdays=[0, 1, 2, 3, 4],
    )
    intervals = ews_working_intervals(
        result["attendees"][0]["working_hours"],
        window_start=dt("2026-08-03T00:00:00Z"),
        window_end=dt("2026-08-04T00:00:00Z"),
    )
    assert intervals[0].start == dt("2026-08-03T01:30:00Z")
    assert intervals[0].end == dt("2026-08-03T10:00:00Z")


def test_other_attendee_keeps_exchange_working_hours() -> None:
    result = apply_current_user_working_hours_override(
        {
            "start": "2026-08-03T00:00:00Z",
            "attendees": [{
                "email": "other@company.com",
                "attendee_type": "Required",
                "working_hours": raw_exchange_hours(),
            }],
        },
        current_user_email="self@company.com",
        zone_name="Asia/Shanghai",
        workday_start="09:30",
        workday_end="18:00",
        workdays=[0, 1, 2, 3, 4],
    )
    person = result["attendees"][0]
    assert person["working_hours_source"] == "exchange"
    assert person["working_hours"]["working_periods"][0]["start"] == "08:00"
    assert "exchange_working_hours" not in person


def test_atomic_availability_returns_effective_local_hours(monkeypatch) -> None:
    from exchange_ews_mcp import service
    from exchange_ews_mcp.config import AppConfig

    config = AppConfig(
        ews_url="https://mail.company.com/EWS/Exchange.asmx",
        username="DOMAIN\\user",
        primary_email="self@company.com",
        calendar_time_zone="Asia/Shanghai",
        calendar_workday_start="09:30",
        calendar_workday_end="18:00",
        calendar_workdays=[0, 1, 2, 3, 4],
    )

    class Client:
        def get_user_availability(self, **kwargs):
            return {
                "status": "success",
                "start": kwargs["start"],
                "end": kwargs["end"],
                "attendees": [{
                    "email": "self@company.com",
                    "attendee_type": "Organizer",
                    "status": "success",
                    "events": [],
                    "working_hours": raw_exchange_hours(),
                }],
            }

    monkeypatch.setattr(service, "load_config", lambda: config)
    monkeypatch.setattr(service, "configured_client", lambda: Client())
    result = service.get_user_availability(
        attendees=[{"email": "self@company.com", "attendee_type": "Organizer"}],
        start="2026-08-03T09:00:00",
        end="2026-08-03T18:00:00",
    )
    person = result["attendees"][0]
    assert person["working_hours_source"] == "local_config_override"
    assert person["working_hours"]["working_periods"][0]["start"] == "09:30"
    assert person["exchange_working_hours"]["working_periods"][0]["start"] == "08:00"
