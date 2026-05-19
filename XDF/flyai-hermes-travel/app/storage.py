from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import hash_password, verify_password


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    enabled INTEGER NOT NULL DEFAULT 1,
    daily_limit INTEGER NOT NULL DEFAULT 10,
    max_concurrent INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER NOT NULL DEFAULT 300,
    can_view_history INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    query TEXT NOT NULL,
    status TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    stderr TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queries_created_at ON queries(created_at DESC);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_created_at ON usage_events(user_id, created_at DESC);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_prefix() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class QueryStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def init(self, owner_password: str = "") -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "queries", "user_id", "INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queries_user_created_at ON queries(user_id, created_at DESC)")
            if owner_password:
                self.ensure_owner(owner_password, conn=conn)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def ensure_owner(self, owner_password: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        owns_connection = conn is None
        conn = conn or self._connect()
        try:
            now = _utc_now()
            owner = conn.execute("SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").fetchone()
            password_hash = hash_password(owner_password)
            if owner:
                conn.execute(
                    """
                    UPDATE users
                    SET label = ?, password_hash = ?, enabled = 1, daily_limit = -1,
                        max_concurrent = 4, timeout_seconds = 900, can_view_history = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    ("Owner", password_hash, now, owner["id"]),
                )
                user_id = int(owner["id"])
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO users
                        (label, password_hash, role, enabled, daily_limit, max_concurrent,
                         timeout_seconds, can_view_history, created_at, updated_at)
                    VALUES (?, ?, 'owner', 1, -1, 4, 900, 1, ?, ?)
                    """,
                    ("Owner", password_hash, now, now),
                )
                user_id = int(cursor.lastrowid)
            if owns_connection:
                conn.commit()
            return self.get_user(user_id) or {}
        finally:
            if owns_connection:
                conn.close()

    def authenticate(self, password: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users WHERE enabled = 1").fetchall()
            for row in rows:
                if verify_password(password, row["password_hash"]):
                    now = _utc_now()
                    conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
                    conn.commit()
                    user = dict(row)
                    user["last_login_at"] = now
                    return self._public_user(user)
        return None

    def get_user(self, user_id: int, include_disabled: bool = False) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            if include_disabled:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM users WHERE id = ? AND enabled = 1", (user_id,)).fetchone()
        return self._public_user(dict(row)) if row else None

    def create_user(
        self,
        label: str,
        password: str,
        daily_limit: int = 10,
        max_concurrent: int = 1,
        timeout_seconds: int = 300,
        can_view_history: bool = True,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        now = _utc_now()
        with self._connect() as conn:
            if self._password_exists(conn, password):
                raise ValueError("这个口令已经被使用，请换一个。")
            cursor = conn.execute(
                """
                INSERT INTO users
                    (label, password_hash, role, enabled, daily_limit, max_concurrent,
                     timeout_seconds, can_view_history, created_at, updated_at)
                VALUES (?, ?, 'user', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    label.strip(),
                    hash_password(password),
                    int(enabled),
                    int(daily_limit),
                    int(max_concurrent),
                    int(timeout_seconds),
                    int(can_view_history),
                    now,
                    now,
                ),
            )
            user_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_user(user_id, include_disabled=True) or {}

    def update_user(self, user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "label",
            "enabled",
            "daily_limit",
            "max_concurrent",
            "timeout_seconds",
            "can_view_history",
        }
        assignments = []
        values: List[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            if key in {"enabled", "can_view_history"}:
                values.append(int(bool(value)))
            elif key in {"daily_limit", "max_concurrent", "timeout_seconds"}:
                values.append(int(value))
            else:
                values.append(str(value).strip())
        if assignments:
            assignments.append("updated_at = ?")
            values.append(_utc_now())
            values.append(int(user_id))
            with self._connect() as conn:
                conn.execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ? AND role != 'owner'", values)
                conn.commit()
        return self.get_user(user_id, include_disabled=True) or {}

    def reset_password(self, user_id: int, password: str) -> Dict[str, Any]:
        with self._connect() as conn:
            if self._password_exists(conn, password, exclude_user_id=user_id):
                raise ValueError("这个口令已经被使用，请换一个。")
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ? AND role != 'owner'",
                (hash_password(password), _utc_now(), int(user_id)),
            )
            conn.commit()
        return self.get_user(user_id, include_disabled=True) or {}

    def _password_exists(self, conn: sqlite3.Connection, password: str, exclude_user_id: Optional[int] = None) -> bool:
        rows = conn.execute("SELECT id, password_hash FROM users").fetchall()
        for row in rows:
            if exclude_user_id is not None and int(row["id"]) == int(exclude_user_id):
                continue
            if verify_password(password, row["password_hash"]):
                return True
        return False

    def list_users(self) -> List[Dict[str, Any]]:
        today = _today_prefix()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    users.*,
                    COUNT(queries.id) AS used_today,
                    MAX(queries.created_at) AS last_query_at
                FROM users
                LEFT JOIN queries ON queries.user_id = users.id AND substr(queries.created_at, 1, 10) = ?
                GROUP BY users.id
                ORDER BY users.role = 'owner' DESC, users.created_at DESC
                """,
                (today,),
            ).fetchall()
        return [self._public_user(dict(row)) for row in rows]

    def count_queries_today(self, user_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM queries WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
                (int(user_id), _today_prefix()),
            ).fetchone()
        return int(row["count"] if row else 0)

    def insert(
        self,
        query: str,
        status: str,
        blocks: List[Dict[str, Any]],
        raw_output: str,
        stderr: str,
        duration_ms: int,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        created_at = _utc_now()
        blocks_json = json.dumps(blocks, ensure_ascii=False)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queries
                    (user_id, query, status, blocks_json, raw_output, stderr, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, query, status, blocks_json, raw_output, stderr, duration_ms, created_at),
            )
            query_id = int(cursor.lastrowid)
            conn.commit()

        return {
            "id": query_id,
            "user_id": user_id,
            "query": query,
            "status": status,
            "blocks": blocks,
            "raw_output": raw_output,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "created_at": created_at,
        }

    def list_recent(self, user_id: Optional[int] = None, limit: int = 30, include_all: bool = False) -> List[Dict[str, Any]]:
        params: List[Any] = []
        where = ""
        if not include_all:
            where = "WHERE queries.user_id = ?"
            params.append(user_id)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT queries.id, queries.user_id, users.label AS user_label, query, status,
                       blocks_json, raw_output, duration_ms, queries.created_at
                FROM queries
                LEFT JOIN users ON users.id = queries.user_id
                {where}
                ORDER BY queries.created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "user_label": row["user_label"],
                    "query": row["query"],
                    "status": row["status"],
                    "blocks": json.loads(row["blocks_json"]),
                    "raw_output": row["raw_output"],
                    "duration_ms": row["duration_ms"],
                    "created_at": row["created_at"],
                }
            )
        return items

    def log_usage_event(self, user_id: Optional[int], event_type: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (user_id, event_type, metadata_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, event_type, json.dumps(metadata or {}, ensure_ascii=False), _utc_now()),
            )
            conn.commit()

    def check(self) -> Dict[str, Any]:
        try:
            self.init()
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return {"ok": True, "path": str(self.database_path)}
        except sqlite3.Error as exc:
            return {"ok": False, "path": str(self.database_path), "error": str(exc)}

    def _public_user(self, row: Dict[str, Any]) -> Dict[str, Any]:
        row.pop("password_hash", None)
        for key in ("enabled", "can_view_history"):
            if key in row:
                row[key] = bool(row[key])
        row["used_today"] = int(row.get("used_today") or 0)
        return row
