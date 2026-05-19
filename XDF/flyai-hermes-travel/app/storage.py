from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    status TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    stderr TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queries_created_at ON queries(created_at DESC);
"""


class QueryStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path))
        conn.row_factory = sqlite3.Row
        return conn

    def insert(
        self,
        query: str,
        status: str,
        blocks: List[Dict[str, Any]],
        raw_output: str,
        stderr: str,
        duration_ms: int,
    ) -> Dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        blocks_json = json.dumps(blocks, ensure_ascii=False)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queries
                    (query, status, blocks_json, raw_output, stderr, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (query, status, blocks_json, raw_output, stderr, duration_ms, created_at),
            )
            query_id = int(cursor.lastrowid)
            conn.commit()

        return {
            "id": query_id,
            "query": query,
            "status": status,
            "blocks": blocks,
            "raw_output": raw_output,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "created_at": created_at,
        }

    def list_recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, query, status, blocks_json, duration_ms, created_at
                FROM queries
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "query": row["query"],
                    "status": row["status"],
                    "blocks": json.loads(row["blocks_json"]),
                    "duration_ms": row["duration_ms"],
                    "created_at": row["created_at"],
                }
            )
        return items

    def check(self) -> Dict[str, Any]:
        try:
            self.init()
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return {"ok": True, "path": str(self.database_path)}
        except sqlite3.Error as exc:
            return {"ok": False, "path": str(self.database_path), "error": str(exc)}

