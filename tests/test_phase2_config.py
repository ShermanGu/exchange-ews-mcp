from __future__ import annotations

import pytest

from exchange_ews_mcp.errors import ConfigurationError
from exchange_ews_mcp.phase2_config import Phase2TestConfig


def test_phase2_config_normalizes_and_deduplicates_senders() -> None:
    config = Phase2TestConfig(
        senders=["Alice <alice@example.com>", "ALICE@example.com", "bob@example.com"],
        draft_recipient="self@example.com",
        subject_contains="  Weekly  ",
        search_limit=25,
    ).normalized()
    assert config.senders == ["alice@example.com", "bob@example.com"]
    assert config.subject_contains == "Weekly"
    assert config.search_limit == 25


def test_phase2_config_rejects_missing_senders() -> None:
    with pytest.raises(ConfigurationError, match="至少需要"):
        Phase2TestConfig(senders=[], draft_recipient="self@example.com").normalized()


def test_phase2_config_rejects_invalid_recipient() -> None:
    with pytest.raises(ConfigurationError, match="draft_recipient"):
        Phase2TestConfig(senders=["a@example.com"], draft_recipient="not-an-email").normalized()
