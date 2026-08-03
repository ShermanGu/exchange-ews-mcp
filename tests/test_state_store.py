from pathlib import Path

import pytest

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
