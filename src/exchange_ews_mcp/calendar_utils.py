from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from datetime import tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BUSY_TYPES = {"Busy", "Tentative", "OOF", "WorkingElsewhere", "NoData"}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_INDEX = {name: index for index, name in enumerate(DAY_NAMES)}


def parse_iso_datetime(value: str, field_name: str = "datetime") -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError(f"{field_name} 不能为空。")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field_name} 必须是带时区的 ISO 8601 日期时间，例如 "
            "2026-08-03T09:00:00+08:00。"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} 必须包含时区偏移或 Z，例如 2026-08-03T09:00:00+08:00。"
        )
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime 必须包含时区。")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_input_datetime(
    value: str,
    zone_name: str,
    field_name: str = "datetime",
) -> datetime:
    """Parse user-facing ISO 8601 input and normalize it to UTC.

    Inputs that already contain ``Z`` or an explicit offset are absolute instants
    and keep that meaning.  Offset-free inputs are interpreted in the configured
    calendar display time zone.  Ambiguous or nonexistent local wall-clock times
    around daylight-saving transitions are rejected so the caller must provide an
    explicit offset instead of silently choosing the wrong instant.
    """
    raw = value.strip()
    if not raw:
        raise ValueError(f"{field_name} 不能为空。")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field_name} 必须是 ISO 8601 日期时间，例如 "
            "2026-08-03T09:00:00、2026-08-03T09:00:00+08:00 或 "
            "2026-08-03T01:00:00Z。"
        ) from exc

    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)

    zone = load_zone(zone_name)
    if zone is timezone.utc:
        return parsed.replace(tzinfo=timezone.utc)

    first = parsed.replace(tzinfo=zone, fold=0)
    second = parsed.replace(tzinfo=zone, fold=1)
    first_valid = (
        first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == parsed
    )
    second_valid = (
        second.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == parsed
    )
    if not first_valid and not second_valid:
        raise ValueError(
            f"{field_name}={raw!r} 在时区 {validate_zone(zone_name)!r} 中不存在，"
            "通常位于夏令时跳变区间；请提供带偏移的时间。"
        )
    if (
        first_valid
        and second_valid
        and first.utcoffset() != second.utcoffset()
    ):
        raise ValueError(
            f"{field_name}={raw!r} 在时区 {validate_zone(zone_name)!r} 中存在两个可能时刻，"
            "通常位于夏令时回拨区间；请提供带偏移的时间。"
        )
    localized = first if first_valid else second
    return localized.astimezone(timezone.utc)


def format_in_zone(value: datetime | str, zone_name: str) -> str:
    """Format one absolute instant in the configured display time zone."""
    parsed = parse_iso_datetime(value, "datetime") if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("datetime 必须包含时区。")
    return parsed.astimezone(load_zone(zone_name)).isoformat(timespec="seconds")


def decorate_time_range(
    payload: dict[str, Any],
    *,
    start_key: str,
    end_key: str,
    zone_name: str,
) -> dict[str, Any]:
    """Keep canonical UTC fields and add user-time-zone presentation fields.

    The original start/end keys remain normalized UTC for backward compatibility.
    Additional ``*_utc`` and ``local_*`` fields make the transport/calculation time
    and the user-facing time explicit.
    """
    result = dict(payload)
    start_raw = result.get(start_key)
    end_raw = result.get(end_key)
    result["display_time_zone"] = validate_zone(zone_name)
    result["transport_time_zone"] = "UTC"
    if not start_raw or not end_raw:
        return result
    start_dt = parse_iso_datetime(str(start_raw), start_key)
    end_dt = parse_iso_datetime(str(end_raw), end_key)
    start_utc = format_utc(start_dt)
    end_utc = format_utc(end_dt)
    result[start_key] = start_utc
    result[end_key] = end_utc
    result[f"{start_key}_utc"] = start_utc
    result[f"{end_key}_utc"] = end_utc
    result[f"local_{start_key}"] = format_in_zone(start_dt, zone_name)
    result[f"local_{end_key}"] = format_in_zone(end_dt, zone_name)
    return result


def decorate_calendar_item(item: dict[str, Any], zone_name: str) -> dict[str, Any]:
    return decorate_time_range(
        item, start_key="start", end_key="end", zone_name=zone_name
    )


def decorate_availability_result(
    result: dict[str, Any], zone_name: str
) -> dict[str, Any]:
    decorated = decorate_time_range(
        result, start_key="start", end_key="end", zone_name=zone_name
    )
    attendees: list[dict[str, Any]] = []
    for attendee in result.get("attendees") or []:
        person = dict(attendee)
        person["events"] = [
            decorate_calendar_item(event, zone_name)
            for event in attendee.get("events") or []
        ]
        attendees.append(person)
    decorated["attendees"] = attendees
    return decorated


def configured_working_hours_payload(
    *,
    zone_name: str,
    workday_start: str,
    workday_end: str,
    workdays: list[int],
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Return the user's locally configured working hours in the public schema.

    This payload is deliberately IANA-based.  It represents the user's explicit
    MCP preference and therefore takes precedence over Exchange WorkingHours for
    the current user.
    """
    zone = load_zone(zone_name)
    start_value = normalize_hhmm(workday_start, "calendar_workday_start")
    end_value = normalize_hhmm(workday_end, "calendar_workday_end")
    start_minutes = sum(
        value * multiplier
        for value, multiplier in zip((int(part) for part in start_value.split(":")), (60, 1))
    )
    end_minutes = sum(
        value * multiplier
        for value, multiplier in zip((int(part) for part in end_value.split(":")), (60, 1))
    )
    probe = (reference_time or datetime.now(timezone.utc)).astimezone(zone)
    offset = probe.utcoffset() or timedelta(0)
    offset_minutes = int(offset.total_seconds() // 60)
    sign = "+" if offset_minutes >= 0 else "-"
    absolute = abs(offset_minutes)
    hours, minutes = divmod(absolute, 60)
    return {
        "source": "local_config",
        "time_zone": {
            "type": "iana",
            "iana_name": validate_zone(zone_name),
            "utc_offset_at_reference": f"{sign}{hours:02d}:{minutes:02d}",
            "reference_time": format_utc(probe),
        },
        "working_periods": [
            {
                "days": [DAY_NAMES[index] for index in sorted(set(workdays))],
                "start_minutes": start_minutes,
                "end_minutes": end_minutes,
                "start": start_value,
                "end": end_value,
            }
        ],
    }


def apply_current_user_working_hours_override(
    result: dict[str, Any],
    *,
    current_user_email: str | None,
    zone_name: str,
    workday_start: str,
    workday_end: str,
    workdays: list[int],
) -> dict[str, Any]:
    """Use the MCP calendar preferences as authoritative for the current user.

    Exchange's mailbox WorkingHours are retained under ``exchange_working_hours``
    for diagnostics, while ``working_hours`` becomes the effective value used by
    presentation and scheduling.
    """
    output = dict(result)
    reference_raw = output.get("start")
    reference_time = None
    if reference_raw:
        try:
            reference_time = parse_iso_datetime(str(reference_raw), "availability.start")
        except ValueError:
            reference_time = None
    configured = configured_working_hours_payload(
        zone_name=zone_name,
        workday_start=workday_start,
        workday_end=workday_end,
        workdays=workdays,
        reference_time=reference_time,
    )
    normalized_current = (current_user_email or "").strip().casefold()
    attendees: list[dict[str, Any]] = []
    for attendee in output.get("attendees") or []:
        item = dict(attendee)
        email = str(item.get("email") or "").strip().casefold()
        is_current = bool(normalized_current and email == normalized_current)
        if not normalized_current and item.get("attendee_type") == "Organizer":
            is_current = True
        if is_current:
            item["exchange_working_hours"] = item.get("working_hours")
            item["working_hours"] = configured
            item["working_hours_source"] = "local_config_override"
            item["working_hours_notice"] = (
                "当前用户的工作时间采用 MCP 本地配置；Exchange 返回值仅保留在 "
                "exchange_working_hours 中供诊断。"
            )
        else:
            item["working_hours_source"] = (
                "exchange" if item.get("working_hours") else "unavailable"
            )
        attendees.append(item)
    output["attendees"] = attendees
    output["working_hours_policy"] = (
        "local_config_overrides_current_user_exchange_working_hours"
    )
    return output

def normalize_hhmm(value: str, field_name: str) -> str:
    """Accept H:MM or HH:MM and return canonical HH:MM."""
    raw = value.strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        if not (1 <= len(hour_text) <= 2 and len(minute_text) == 2):
            raise ValueError
        if not (hour_text.isdigit() and minute_text.isdigit()):
            raise ValueError
        hour = int(hour_text)
        minute = int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"{field_name} 必须使用 H:MM 或 HH:MM，例如 9:30 或 09:30。"
        ) from exc
    return f"{hour:02d}:{minute:02d}"


def parse_hhmm(value: str, field_name: str) -> time:
    normalized = normalize_hhmm(value, field_name)
    hour, minute = (int(part) for part in normalized.split(":"))
    return time(hour=hour, minute=minute)


UTC_ZONE_NAMES = {"utc", "etc/utc", "etc/gmt", "gmt", "zulu"}


def load_zone(zone_name: str) -> tzinfo:
    """Load an IANA zone, with a database-independent UTC fallback.

    Windows Python installations normally do not ship the IANA database. The
    project installs ``tzdata`` for named zones, while UTC remains usable even
    before or without that package.
    """
    value = zone_name.strip()
    if not value:
        raise ValueError("calendar_time_zone 不能为空。")
    if value.casefold() in UTC_ZONE_NAMES:
        return timezone.utc
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"未知 IANA 时区：{value!r}。例如 Asia/Shanghai。"
            "如果在 Windows 上运行，请重新执行 install.cmd 以安装 tzdata。"
        ) from exc


def validate_zone(zone_name: str) -> str:
    value = zone_name.strip()
    load_zone(value)
    return "UTC" if value.casefold() in UTC_ZONE_NAMES else value


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Interval 时间必须包含时区。")
        if self.end <= self.start:
            raise ValueError("Interval end 必须晚于 start。")

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: "Interval") -> bool:
        return self.start <= other.start and other.end <= self.end


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    ordered = sorted(intervals, key=lambda item: item.start)
    merged: list[Interval] = []
    for current in ordered:
        if not merged or current.start > merged[-1].end:
            merged.append(current)
            continue
        previous = merged[-1]
        merged[-1] = Interval(previous.start, max(previous.end, current.end))
    return merged


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    if occurrence == 0 or not -5 <= occurrence <= 5:
        raise ValueError("occurrence 必须在 -5..-1 或 1..5。")
    if occurrence > 0:
        first = date(year, month, 1)
        delta = (weekday - first.weekday()) % 7
        candidate = first + timedelta(days=delta + 7 * (occurrence - 1))
        if candidate.month != month:
            candidate -= timedelta(days=7)
        return candidate
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    delta = (last.weekday() - weekday) % 7
    candidate = last - timedelta(days=delta + 7 * (abs(occurrence) - 1))
    if candidate.month != month:
        candidate += timedelta(days=7)
    return candidate


def _transition_local(year: int, rule: dict[str, Any] | None) -> datetime | None:
    if not rule:
        return None
    try:
        month = int(rule.get("month") or 0)
        occurrence = int(rule.get("day_order") or 0)
        weekday = DAY_INDEX[str(rule.get("day_of_week") or "")]
        hour, minute, second = [int(part) for part in str(rule.get("time") or "00:00:00").split(":")]
    except (ValueError, KeyError, TypeError):
        return None
    if not 1 <= month <= 12 or occurrence == 0:
        return None
    transition_date = _nth_weekday(year, month, weekday, occurrence if occurrence != 5 else -1)
    return datetime.combine(transition_date, time(hour, minute, second))


def working_hours_bias_minutes(working_hours: dict[str, Any], local_day: date) -> int | None:
    zone = working_hours.get("time_zone") or {}
    try:
        # v0.5.8 normalized field, with legacy compatibility for callers that
        # persisted or directly constructed the previous response shape.
        base_bias = int(zone.get("bias_minutes", zone.get("bias")))
    except (TypeError, ValueError):
        return None
    standard = zone.get("standard_transition", zone.get("standard")) or {}
    daylight = zone.get("daylight_transition", zone.get("daylight")) or {}
    standard_extra = int(standard.get("bias_minutes", standard.get("bias", 0)) or 0)
    daylight_extra = int(daylight.get("bias_minutes", daylight.get("bias", 0)) or 0)
    daylight_start = _transition_local(local_day.year, daylight)
    standard_start = _transition_local(local_day.year, standard)
    use_daylight = False
    if daylight_start and standard_start:
        probe = datetime.combine(local_day, time(12, 0))
        if daylight_start < standard_start:
            use_daylight = daylight_start <= probe < standard_start
        else:
            use_daylight = probe >= daylight_start or probe < standard_start
    return base_bias + (daylight_extra if use_daylight else standard_extra)


def ews_working_intervals(
    working_hours: dict[str, Any] | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[Interval]:
    if not working_hours:
        return []
    periods = working_hours.get("working_periods") or []
    if not periods:
        return []
    zone_data = working_hours.get("time_zone") or {}
    iana_name = str(zone_data.get("iana_name") or "").strip()
    if iana_name:
        zone = load_zone(iana_name)
        local_start_day = window_start.astimezone(zone).date() - timedelta(days=1)
        local_end_day = window_end.astimezone(zone).date() + timedelta(days=1)
        intervals: list[Interval] = []
        day = local_start_day
        while day <= local_end_day:
            for period in periods:
                days = period.get("days") or []
                if DAY_NAMES[day.weekday()] not in days:
                    continue
                try:
                    start_minutes = int(period.get("start_minutes"))
                    end_minutes = int(period.get("end_minutes"))
                except (TypeError, ValueError):
                    continue
                local_start = datetime.combine(day, time.min, tzinfo=zone) + timedelta(minutes=start_minutes)
                local_end = datetime.combine(day, time.min, tzinfo=zone) + timedelta(minutes=end_minutes)
                utc_start = local_start.astimezone(timezone.utc)
                utc_end = local_end.astimezone(timezone.utc)
                if utc_end > window_start and utc_start < window_end:
                    intervals.append(Interval(max(utc_start, window_start), min(utc_end, window_end)))
            day += timedelta(days=1)
        return merge_intervals(intervals)
    start_day = window_start.astimezone(timezone.utc).date() - timedelta(days=2)
    end_day = window_end.astimezone(timezone.utc).date() + timedelta(days=2)
    intervals: list[Interval] = []
    day = start_day
    while day <= end_day:
        bias = working_hours_bias_minutes(working_hours, day)
        if bias is None:
            day += timedelta(days=1)
            continue
        for period in periods:
            days = period.get("days") or []
            if DAY_NAMES[day.weekday()] not in days:
                continue
            try:
                start_minutes = int(period.get("start_minutes"))
                end_minutes = int(period.get("end_minutes"))
            except (TypeError, ValueError):
                continue
            local_start = datetime.combine(day, time.min) + timedelta(minutes=start_minutes)
            local_end = datetime.combine(day, time.min) + timedelta(minutes=end_minutes)
            utc_start = (local_start + timedelta(minutes=bias)).replace(tzinfo=timezone.utc)
            utc_end = (local_end + timedelta(minutes=bias)).replace(tzinfo=timezone.utc)
            if utc_end <= window_start or utc_start >= window_end:
                continue
            intervals.append(Interval(max(utc_start, window_start), min(utc_end, window_end)))
        day += timedelta(days=1)
    return merge_intervals(intervals)


def configured_working_intervals(
    *,
    window_start: datetime,
    window_end: datetime,
    zone_name: str,
    workday_start: str,
    workday_end: str,
    workdays: list[int],
) -> list[Interval]:
    zone = load_zone(zone_name)
    start_time = parse_hhmm(workday_start, "calendar_workday_start")
    end_time = parse_hhmm(workday_end, "calendar_workday_end")
    if end_time <= start_time:
        raise ValueError("calendar_workday_end 必须晚于 calendar_workday_start。")
    local_start_day = window_start.astimezone(zone).date() - timedelta(days=1)
    local_end_day = window_end.astimezone(zone).date() + timedelta(days=1)
    intervals: list[Interval] = []
    current = local_start_day
    while current <= local_end_day:
        if current.weekday() in workdays:
            start_local = datetime.combine(current, start_time, tzinfo=zone)
            end_local = datetime.combine(current, end_time, tzinfo=zone)
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = end_local.astimezone(timezone.utc)
            if end_utc > window_start and start_utc < window_end:
                intervals.append(Interval(max(start_utc, window_start), min(end_utc, window_end)))
        current += timedelta(days=1)
    return merge_intervals(intervals)


def event_busy_intervals(availability: dict[str, Any]) -> list[Interval]:
    intervals: list[Interval] = []
    for event in availability.get("events") or []:
        busy_type = str(event.get("busy_type") or "Busy")
        if busy_type not in BUSY_TYPES:
            continue
        start_raw = event.get("start")
        end_raw = event.get("end")
        if not start_raw or not end_raw:
            continue
        try:
            intervals.append(Interval(parse_iso_datetime(str(start_raw), "event.start"), parse_iso_datetime(str(end_raw), "event.end")))
        except ValueError:
            continue
    return merge_intervals(intervals)


def _aligned_start(value: datetime, interval_minutes: int) -> datetime:
    seconds = interval_minutes * 60
    timestamp = int(value.timestamp())
    aligned = ((timestamp + seconds - 1) // seconds) * seconds
    return datetime.fromtimestamp(aligned, tz=timezone.utc)



def is_exact_interval_available(
    *,
    candidate: Interval,
    attendees: list[dict[str, Any]],
    fallback_work_intervals: list[Interval],
    respect_attendee_working_hours: bool = True,
) -> bool:
    """Return True when the exact interval is inside working hours and has no busy overlap."""
    if not any(container.contains(candidate) for container in fallback_work_intervals):
        return False
    for attendee in attendees:
        own_work = ews_working_intervals(
            attendee.get("working_hours"),
            window_start=candidate.start,
            window_end=candidate.end,
        ) or fallback_work_intervals
        if respect_attendee_working_hours and not any(
            container.contains(candidate) for container in own_work
        ):
            return False
        if any(candidate.overlaps(busy) for busy in event_busy_intervals(attendee)):
            return False
    return True

def find_common_slots(
    *,
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    interval_minutes: int,
    attendees: list[dict[str, Any]],
    fallback_work_intervals: list[Interval],
    respect_attendee_working_hours: bool = True,
    max_results: int = 10,
    display_zone_name: str | None = None,
) -> list[dict[str, Any]]:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes 必须大于 0。")
    if interval_minutes <= 0:
        raise ValueError("interval_minutes 必须大于 0。")
    if max_results <= 0:
        raise ValueError("max_results 必须大于 0。")
    duration = timedelta(minutes=duration_minutes)
    busy_by_attendee = [event_busy_intervals(item) for item in attendees]
    work_by_attendee: list[list[Interval]] = []
    for item in attendees:
        own = ews_working_intervals(
            item.get("working_hours"),
            window_start=window_start,
            window_end=window_end,
        )
        work_by_attendee.append(own or fallback_work_intervals)

    slots: list[dict[str, Any]] = []
    cursor = _aligned_start(window_start, interval_minutes)
    while cursor + duration <= window_end:
        candidate = Interval(cursor, cursor + duration)
        if not any(container.contains(candidate) for container in fallback_work_intervals):
            cursor += timedelta(minutes=interval_minutes)
            continue
        if respect_attendee_working_hours and any(
            not any(container.contains(candidate) for container in work_intervals)
            for work_intervals in work_by_attendee
        ):
            cursor += timedelta(minutes=interval_minutes)
            continue
        if any(any(candidate.overlaps(busy) for busy in busy_intervals) for busy_intervals in busy_by_attendee):
            cursor += timedelta(minutes=interval_minutes)
            continue
        slot = {
            "start": format_utc(candidate.start),
            "end": format_utc(candidate.end),
            "duration_minutes": duration_minutes,
        }
        if display_zone_name:
            slot = decorate_time_range(
                slot, start_key="start", end_key="end", zone_name=display_zone_name
            )
        slots.append(slot)
        if len(slots) >= max_results:
            break
        cursor += timedelta(minutes=interval_minutes)
    return slots
