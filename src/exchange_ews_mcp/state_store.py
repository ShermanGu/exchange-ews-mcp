from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from platformdirs import user_data_dir

APP_NAME = "exchange-ews-mcp"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def default_state_path() -> Path:
    return Path(user_data_dir(APP_NAME, appauthor=False)) / "workflow-state.sqlite3"


@dataclass(frozen=True)
class StoredReference:
    ref: str
    kind: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    expires_at: str | None


class ReferenceStore:
    """Local opaque-reference and resumable-action store.

    Exchange ItemId/ChangeKey values stay inside this SQLite file. Public callers use
    message_ref, draft_ref, person_ref, or resume_token values instead.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_state_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open one short-lived SQLite connection and always close it.

        ``sqlite3.Connection`` as a context manager commits or rolls back, but does
        not close the underlying handle.  The MCP server is long-lived, so leaving
        those handles to garbage collection can accumulate descriptors on Windows.
        """
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS refs (
                    ref TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    external_key TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_refs_kind_external
                    ON refs(kind, external_key)
                    WHERE external_key IS NOT NULL;

                CREATE TABLE IF NOT EXISTS action_sessions (
                    token TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _deterministic_ref(kind: str, external_key: str) -> str:
        prefixes = {"message": "msg", "draft": "draft", "person": "person", "calendar": "cal", "weekly_report": "weekly", "weekly_report_context": "weeklyctx"}
        prefix = prefixes.get(kind, "ref")
        digest = hashlib.sha256(f"{kind}\0{external_key}".encode("utf-8")).hexdigest()[:20]
        return f"{prefix}_{digest}"

    def upsert_reference(
        self,
        *,
        kind: str,
        external_key: str,
        payload: dict[str, Any],
        ttl_days: int | None = None,
    ) -> str:
        if not kind.strip() or not external_key.strip():
            raise ValueError("kind 和 external_key 不能为空。")
        now = _utc_now()
        expires = now + timedelta(days=ttl_days) if ttl_days else None
        ref = self._deterministic_ref(kind.strip(), external_key.strip())
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO refs(ref, kind, external_key, payload_json, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at
                """,
                (
                    ref,
                    kind.strip(),
                    external_key.strip(),
                    json.dumps(payload, ensure_ascii=False),
                    _iso(now),
                    _iso(now),
                    _iso(expires) if expires else None,
                ),
            )
        return ref

    def get_reference(self, ref: str, *, expected_kind: str | None = None) -> StoredReference:
        normalized = ref.strip()
        if not normalized:
            raise ValueError("ref 不能为空。")
        with self._connect() as db:
            row = db.execute("SELECT * FROM refs WHERE ref=?", (normalized,)).fetchone()
        if row is None:
            raise KeyError(f"引用不存在或已清理：{normalized}")
        if expected_kind and row["kind"] != expected_kind:
            raise ValueError(f"引用 {normalized} 的类型是 {row['kind']}，不是 {expected_kind}。")
        if row["expires_at"]:
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires <= _utc_now():
                self.delete_reference(normalized)
                raise KeyError(f"引用已过期：{normalized}")
        return StoredReference(
            ref=row["ref"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )

    def delete_reference(self, ref: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM refs WHERE ref=?", (ref.strip(),))
        return cursor.rowcount > 0

    def create_action_session(
        self,
        state: dict[str, Any],
        *,
        ttl_hours: float = 24,
        ttl_minutes: int | None = None,
        status: str = "pending",
        token_prefix: str = "resume_",
    ) -> str:
        if ttl_minutes is not None:
            if ttl_minutes <= 0:
                raise ValueError("ttl_minutes 必须大于 0。")
            lifetime = timedelta(minutes=ttl_minutes)
        else:
            if ttl_hours <= 0:
                raise ValueError("ttl_hours 必须大于 0。")
            lifetime = timedelta(hours=ttl_hours)
        if not token_prefix or not token_prefix.strip():
            raise ValueError("token_prefix 不能为空。")
        now = _utc_now()
        token = token_prefix + secrets.token_urlsafe(18)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO action_sessions(token, status, state_json, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    status,
                    json.dumps(state, ensure_ascii=False),
                    _iso(now),
                    _iso(now),
                    _iso(now + lifetime),
                ),
            )
        return token

    def create_scoped_action_session(
        self,
        state: dict[str, Any],
        *,
        action: str,
        scope_key: str,
        ttl_minutes: int,
        status: str = "context_ready",
        token_prefix: str = "weeklyflow_",
        supersede_statuses: tuple[str, ...] = ("context_ready",),
        blocking_statuses: tuple[str, ...] = ("applying",),
    ) -> str:
        """Create one short-lived scoped session and supersede older active ones.

        Superseding and insertion happen in one SQLite transaction, so two
        sequential context requests for the same source cannot both remain
        usable. The state is tagged with ``action`` and ``scope_key`` for later
        auditing and deterministic invalidation.
        """
        action_value = action.strip()
        scope_value = scope_key.strip()
        if not action_value or not scope_value:
            raise ValueError("action 和 scope_key 不能为空。")
        if ttl_minutes <= 0:
            raise ValueError("ttl_minutes 必须大于 0。")
        now = _utc_now()
        now_iso = _iso(now)
        token = token_prefix + secrets.token_urlsafe(18)
        tagged_state = {**state, "action": action_value, "scope_key": scope_value}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT token, status, state_json FROM action_sessions WHERE expires_at > ?",
                (now_iso,),
            ).fetchall()
            for row in rows:
                try:
                    existing_state = json.loads(row["state_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if not (
                    existing_state.get("action") == action_value
                    and existing_state.get("scope_key") == scope_value
                ):
                    continue
                if row["status"] in blocking_statuses:
                    raise ValueError(
                        f"同一工作流范围已有状态为 {row['status']} 的任务正在执行。"
                    )
                if row["status"] in supersede_statuses:
                    db.execute(
                        "UPDATE action_sessions SET status='superseded', updated_at=? WHERE token=?",
                        (now_iso, row["token"]),
                    )
            db.execute(
                """
                INSERT INTO action_sessions(token, status, state_json, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    status,
                    json.dumps(tagged_state, ensure_ascii=False),
                    now_iso,
                    now_iso,
                    _iso(now + timedelta(minutes=ttl_minutes)),
                ),
            )
        return token

    def get_action_session(self, token: str) -> dict[str, Any]:
        normalized = token.strip()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM action_sessions WHERE token=?", (normalized,)
            ).fetchone()
        if row is None:
            raise KeyError(f"任务恢复令牌不存在：{normalized}")
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if expires <= _utc_now():
            self.delete_action_session(normalized)
            raise KeyError(f"任务恢复令牌已过期：{normalized}")
        return {
            "resume_token": row["token"],
            "status": row["status"],
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
        }

    def claim_action_session(
        self,
        token: str,
        *,
        expected_status: str,
        next_status: str,
    ) -> dict[str, Any]:
        """Atomically change a session status only from the expected state.

        The conditional UPDATE is the concurrency guard used by one-shot
        workflows: at most one caller can move ``context_ready`` to
        ``applying``.
        """
        normalized = token.strip()
        if not normalized:
            raise ValueError("token 不能为空。")
        now = _utc_now()
        now_iso = _iso(now)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM action_sessions WHERE token=?", (normalized,)
            ).fetchone()
            if row is None:
                raise KeyError(f"任务令牌不存在：{normalized}")
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if expires <= now:
                db.execute("DELETE FROM action_sessions WHERE token=?", (normalized,))
                raise KeyError(f"任务令牌已过期：{normalized}")
            cursor = db.execute(
                """
                UPDATE action_sessions
                SET status=?, updated_at=?
                WHERE token=? AND status=? AND expires_at>?
                """,
                (next_status, now_iso, normalized, expected_status, now_iso),
            )
            if cursor.rowcount != 1:
                current = db.execute(
                    "SELECT status FROM action_sessions WHERE token=?", (normalized,)
                ).fetchone()
                current_status = current["status"] if current is not None else "missing"
                raise ValueError(
                    f"任务令牌当前状态为 {current_status}，要求状态为 {expected_status}。"
                )
            updated = db.execute(
                "SELECT * FROM action_sessions WHERE token=?", (normalized,)
            ).fetchone()
        assert updated is not None
        return {
            "resume_token": updated["token"],
            "status": updated["status"],
            "state": json.loads(updated["state_json"]),
            "created_at": updated["created_at"],
            "updated_at": updated["updated_at"],
            "expires_at": updated["expires_at"],
        }

    def update_action_session(
        self,
        token: str,
        *,
        state: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_action_session(token)
        next_state = current["state"] if state is None else state
        next_status = current["status"] if status is None else status
        now = _utc_now()
        with self._connect() as db:
            db.execute(
                """
                UPDATE action_sessions
                SET status=?, state_json=?, updated_at=?
                WHERE token=?
                """,
                (
                    next_status,
                    json.dumps(next_state, ensure_ascii=False),
                    _iso(now),
                    token.strip(),
                ),
            )
        return self.get_action_session(token)

    def delete_action_session(self, token: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM action_sessions WHERE token=?", (token.strip(),)
            )
        return cursor.rowcount > 0

    def cleanup_expired(self) -> dict[str, int]:
        now = _iso(_utc_now())
        with self._connect() as db:
            refs = db.execute(
                "DELETE FROM refs WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
            ).rowcount
            sessions = db.execute(
                "DELETE FROM action_sessions WHERE expires_at <= ?", (now,)
            ).rowcount
        return {"references_removed": refs, "sessions_removed": sessions}
