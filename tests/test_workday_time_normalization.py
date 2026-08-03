from __future__ import annotations

import json

import pytest

from exchange_ews_mcp import config as config_module
from exchange_ews_mcp.calendar_utils import normalize_hhmm, parse_hhmm
from exchange_ews_mcp.config import AppConfig


def make_config(**overrides) -> AppConfig:
    values = {
        "ews_url": "https://mail.example.com/EWS/Exchange.asmx",
        "username": r"DOMAIN\user",
        "company_email_domains": ["company.com"],
        "calendar_workday_start": "9:30",
        "calendar_workday_end": "18:00",
    }
    values.update(overrides)
    return AppConfig(**values)


def test_normalize_hhmm_accepts_single_digit_hour() -> None:
    assert normalize_hhmm("9:30", "calendar_workday_start") == "09:30"
    assert normalize_hhmm("09:30", "calendar_workday_start") == "09:30"
    assert parse_hhmm("9:30", "calendar_workday_start").isoformat(timespec="minutes") == "09:30"


@pytest.mark.parametrize("value", ["9:3", "24:00", "09:60", "930", "abc"])
def test_normalize_hhmm_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="H:MM 或 HH:MM"):
        normalize_hhmm(value, "calendar_workday_start")


def test_app_config_validation_accepts_single_digit_hour() -> None:
    make_config().validate()


def test_save_config_persists_canonical_hhmm(monkeypatch, tmp_path) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "config_path", lambda: path)

    config_module.save_config(make_config())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["calendar_workday_start"] == "09:30"
    assert raw["calendar_workday_end"] == "18:00"


def test_load_config_normalizes_legacy_single_digit_hour(monkeypatch, tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "ews_url": "https://mail.example.com/EWS/Exchange.asmx",
                "username": r"DOMAIN\user",
                "company_email_domains": ["company.com"],
                "calendar_workday_start": "9:30",
                "calendar_workday_end": "18:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "config_path", lambda: path)

    loaded = config_module.load_config()

    assert loaded.calendar_workday_start == "09:30"
    assert loaded.calendar_workday_end == "18:00"
