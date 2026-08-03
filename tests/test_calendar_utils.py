from datetime import datetime, timezone

from exchange_ews_mcp.calendar_utils import (
    Interval, configured_working_intervals, ews_working_intervals, find_common_slots,
    is_exact_interval_available,
)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_configured_working_hours_convert_from_asia_shanghai() -> None:
    intervals = configured_working_intervals(
        window_start=dt("2026-08-03T00:00:00Z"),
        window_end=dt("2026-08-04T00:00:00Z"),
        zone_name="Asia/Shanghai", workday_start="09:00", workday_end="18:00",
        workdays=[0, 1, 2, 3, 4],
    )
    assert intervals == [Interval(dt("2026-08-03T01:00:00Z"), dt("2026-08-03T10:00:00Z"))]


def test_common_slots_exclude_busy_events() -> None:
    work = [Interval(dt("2026-08-03T01:00:00Z"), dt("2026-08-03T05:00:00Z"))]
    attendees = [{
        "events": [{"start": "2026-08-03T02:00:00Z", "end": "2026-08-03T03:00:00Z", "busy_type": "Busy"}],
        "working_hours": None,
    }]
    slots = find_common_slots(
        window_start=dt("2026-08-03T01:00:00Z"), window_end=dt("2026-08-03T05:00:00Z"),
        duration_minutes=60, interval_minutes=60, attendees=attendees,
        fallback_work_intervals=work, max_results=10,
    )
    assert [slot["start"] for slot in slots] == [
        "2026-08-03T01:00:00Z", "2026-08-03T03:00:00Z", "2026-08-03T04:00:00Z"
    ]


def test_exact_interval_does_not_require_epoch_alignment() -> None:
    work = [Interval(dt("2026-08-03T01:00:00Z"), dt("2026-08-03T05:00:00Z"))]
    assert is_exact_interval_available(
        candidate=Interval(dt("2026-08-03T01:15:00Z"), dt("2026-08-03T02:15:00Z")),
        attendees=[{"events": [], "working_hours": None}],
        fallback_work_intervals=work,
    )


def test_utc_works_without_iana_database(monkeypatch) -> None:
    import exchange_ews_mcp.calendar_utils as calendar_utils

    def missing_zone(_: str):
        raise calendar_utils.ZoneInfoNotFoundError("no time zone database")

    monkeypatch.setattr(calendar_utils, "ZoneInfo", missing_zone)
    assert calendar_utils.validate_zone("UTC") == "UTC"
    intervals = calendar_utils.configured_working_intervals(
        window_start=dt("2026-08-03T00:00:00Z"),
        window_end=dt("2026-08-04T00:00:00Z"),
        zone_name="UTC",
        workday_start="09:00",
        workday_end="18:00",
        workdays=[0, 1, 2, 3, 4],
    )
    assert intervals == [Interval(dt("2026-08-03T09:00:00Z"), dt("2026-08-03T18:00:00Z"))]


def test_decorate_time_range_keeps_utc_and_adds_local_time() -> None:
    from exchange_ews_mcp.calendar_utils import decorate_time_range

    result = decorate_time_range(
        {"start": "2026-08-03T01:00:00Z", "end": "2026-08-03T02:00:00Z"},
        start_key="start",
        end_key="end",
        zone_name="Asia/Shanghai",
    )
    assert result["start"] == "2026-08-03T01:00:00Z"
    assert result["start_utc"] == "2026-08-03T01:00:00Z"
    assert result["local_start"] == "2026-08-03T09:00:00+08:00"
    assert result["local_end"] == "2026-08-03T10:00:00+08:00"
    assert result["display_time_zone"] == "Asia/Shanghai"
    assert result["transport_time_zone"] == "UTC"


def test_offset_free_input_uses_configured_time_zone() -> None:
    from exchange_ews_mcp.calendar_utils import format_utc, parse_input_datetime

    parsed = parse_input_datetime(
        "2026-08-03T09:00:00", "Asia/Shanghai", "start"
    )
    assert format_utc(parsed) == "2026-08-03T01:00:00Z"


def test_explicit_offset_overrides_configured_time_zone() -> None:
    from exchange_ews_mcp.calendar_utils import format_utc, parse_input_datetime

    parsed = parse_input_datetime(
        "2026-08-03T09:00:00+09:00", "Asia/Shanghai", "start"
    )
    assert format_utc(parsed) == "2026-08-03T00:00:00Z"


def test_ambiguous_local_input_requires_explicit_offset() -> None:
    from exchange_ews_mcp.calendar_utils import parse_input_datetime

    try:
        parse_input_datetime("2026-11-01T01:30:00", "America/New_York", "start")
    except ValueError as exc:
        assert "两个可能时刻" in str(exc)
        assert "带偏移" in str(exc)
    else:
        raise AssertionError("expected ambiguous local time error")


def test_nonexistent_local_input_requires_explicit_offset() -> None:
    from exchange_ews_mcp.calendar_utils import parse_input_datetime

    try:
        parse_input_datetime("2026-03-08T02:30:00", "America/New_York", "start")
    except ValueError as exc:
        assert "不存在" in str(exc)
        assert "带偏移" in str(exc)
    else:
        raise AssertionError("expected nonexistent local time error")


def test_normalized_no_dst_working_hours_are_used_by_interval_algorithm() -> None:
    working_hours = {
        "time_zone": {
            "bias_minutes": -480,
            "utc_offset": "+08:00",
            "observes_daylight_saving": False,
            "standard_transition": None,
            "daylight_transition": None,
        },
        "working_periods": [{
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "start_minutes": 540,
            "end_minutes": 1080,
            "start": "09:00",
            "end": "18:00",
        }],
    }
    intervals = ews_working_intervals(
        working_hours,
        window_start=datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
    )
    assert len(intervals) == 1
    assert intervals[0].start == datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
    assert intervals[0].end == datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
