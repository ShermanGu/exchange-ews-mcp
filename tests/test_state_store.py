from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import exchange_ews_mcp.state_store as state_store_module

from exchange_ews_mcp.state_store import ReferenceStore


def test_reference_store_is_stable_and_updates_payload(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    first = store.upsert_reference(
        kind="message", external_key="ITEM", payload={"item_id": "ITEM", "change_key": "A"}
    )
    second = store.upsert_reference(
        kind="message", external_key="ITEM", payload={"item_id": "ITEM", "change_key": "B"}
    )
    assert first == second
    assert store.get_reference(first, expected_kind="message").payload["change_key"] == "B"
    with pytest.raises(ValueError):
        store.get_reference(first, expected_kind="draft")


def test_action_session_roundtrip(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    token = store.create_action_session({"step": 1}, ttl_hours=1)
    assert store.get_action_session(token)["status"] == "pending"
    updated = store.update_action_session(token, state={"step": 2}, status="resolved")
    assert updated["state"] == {"step": 2}
    assert updated["status"] == "resolved"
    assert store.delete_action_session(token)
    with pytest.raises(KeyError):
        store.get_action_session(token)


def test_scoped_session_supersedes_old_ready_token(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    first = store.create_scoped_action_session(
        {"value": 1},
        action="weekly_report_update",
        scope_key="WK3",
        ttl_minutes=30,
    )
    second = store.create_scoped_action_session(
        {"value": 2},
        action="weekly_report_update",
        scope_key="WK3",
        ttl_minutes=30,
    )
    assert first.startswith("weeklyflow_")
    assert second.startswith("weeklyflow_")
    assert store.get_action_session(first)["status"] == "superseded"
    assert store.get_action_session(second)["status"] == "context_ready"


def test_claim_action_session_is_one_shot(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    token = store.create_scoped_action_session(
        {},
        action="weekly_report_update",
        scope_key="WK3",
        ttl_minutes=30,
    )
    claimed = store.claim_action_session(
        token,
        expected_status="context_ready",
        next_status="applying",
    )
    assert claimed["status"] == "applying"
    with pytest.raises(ValueError, match="当前状态为 applying"):
        store.claim_action_session(
            token,
            expected_status="context_ready",
            next_status="applying",
        )


def test_scoped_session_blocks_while_same_scope_is_applying(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    token = store.create_scoped_action_session(
        {},
        action="weekly_report_update",
        scope_key="WK3",
        ttl_minutes=30,
    )
    store.claim_action_session(token, expected_status="context_ready", next_status="applying")
    with pytest.raises(ValueError, match="正在执行"):
        store.create_scoped_action_session(
            {},
            action="weekly_report_update",
            scope_key="WK3",
            ttl_minutes=30,
        )


def test_concurrent_claim_allows_exactly_one_caller(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "state.db")
    token = store.create_scoped_action_session(
        {},
        action="weekly_report_update",
        scope_key="WK3",
        ttl_minutes=30,
    )

    def attempt() -> str:
        try:
            result = store.claim_action_session(
                token,
                expected_status="context_ready",
                next_status="applying",
            )
            return result["status"]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: attempt(), range(2)))
    assert sorted(results) == ["applying", "rejected"]


def test_action_session_expiry_is_enforced(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 8, 6, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(state_store_module, "_utc_now", lambda: now)
    store = ReferenceStore(tmp_path / "state.db")
    token = store.create_scoped_action_session(
        {},
        action="weekly_report_update",
        scope_key="WK3",
        ttl_minutes=30,
    )
    monkeypatch.setattr(
        state_store_module,
        "_utc_now",
        lambda: now + timedelta(minutes=31),
    )
    with pytest.raises(KeyError, match="已过期"):
        store.claim_action_session(
            token,
            expected_status="context_ready",
            next_status="applying",
        )
