from __future__ import annotations

import pytest

from exchange_ews_mcp.errors import ConfigurationError
from exchange_ews_mcp.workflow_test_config import Phase023TestConfig


def test_dt_config_accepts_romanized_person_queries() -> None:
    config = Phase023TestConfig(
        person_queries=["wangxiaoming", "wangxiaoming123@example.com"],
        sender="sender@example.com",
        draft_recipient="self@example.com",
    ).normalized()
    assert config.person_queries == [
        "wangxiaoming",
        "wangxiaoming123@example.com",
    ]


def test_dt_config_rejects_chinese_person_query() -> None:
    with pytest.raises(ConfigurationError, match="拼音"):
        Phase023TestConfig(
            person_queries=["王小明"],
            sender="sender@example.com",
            draft_recipient="self@example.com",
        ).normalized()
