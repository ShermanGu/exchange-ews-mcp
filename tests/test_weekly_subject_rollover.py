from exchange_ews_mcp.weekly_report import (
    roll_forward_weekly_subject,
    subject_has_weekly_period_marker,
)


def test_rolls_full_date_range_preserving_width() -> None:
    assert roll_forward_weekly_subject(
        "项目周报 2026-07-27 至 2026-08-02",
        reference_sent_at="2026-08-03T01:00:00Z",
    ) == "项目周报 2026-08-03 至 2026-08-09"


def test_rolls_chinese_and_numeric_month_day_ranges() -> None:
    assert roll_forward_weekly_subject(
        "项目周报 8月3日-8月9日",
        reference_sent_at="2026-08-10T01:00:00Z",
    ) == "项目周报 8月10日-8月16日"
    assert roll_forward_weekly_subject(
        "项目周报 08/03-08/09",
        reference_sent_at="2026-08-10T01:00:00Z",
    ) == "项目周报 08/10-08/16"


def test_rolls_week_markers() -> None:
    assert roll_forward_weekly_subject("项目周报 WK32", reference_sent_at="2026-08-09T01:00:00Z") == "项目周报 WK33"
    assert roll_forward_weekly_subject("项目周报 W32", reference_sent_at="2026-08-09T01:00:00Z") == "项目周报 W33"
    assert roll_forward_weekly_subject("项目周报 第32周", reference_sent_at="2026-08-09T01:00:00Z") == "项目周报 第33周"


def test_year_month_only_is_not_treated_as_weekly_period() -> None:
    assert subject_has_weekly_period_marker("项目周报 2026-08") is False
    assert roll_forward_weekly_subject("项目周报 2026-08", reference_sent_at="2026-08-10T01:00:00Z") is None
