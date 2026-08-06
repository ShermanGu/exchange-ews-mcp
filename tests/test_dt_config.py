from __future__ import annotations

import pytest

from exchange_ews_mcp.dt_config import DtTestConfig
from exchange_ews_mcp.errors import ConfigurationError


def test_unified_dt_config_accepts_multiple_senders() -> None:
    config = DtTestConfig(
        person_queries=["wangxiaoming", "wangxiaoming123@example.com"],
        senders=["alice@example.com", "bob@example.com", "alice@example.com"],
        draft_recipient="self@example.com",
    ).normalized()
    assert config.person_queries == ["wangxiaoming", "wangxiaoming123@example.com"]
    assert config.senders == ["alice@example.com", "bob@example.com"]


def test_unified_dt_config_rejects_chinese_person_query() -> None:
    with pytest.raises(ConfigurationError, match="拼音"):
        DtTestConfig(
            person_queries=["王小明"],
            senders=["alice@example.com"],
            draft_recipient="self@example.com",
        ).normalized()
