from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class WatchTarget:
    date: str
    location_id: str
    preferred_spot_ids: list[str] = field(default_factory=list)
    fulfilled: bool = False
    reservation_id: str | None = None


class StateStore:
    def __init__(self, path: str = "state.db"):
        self._conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS watch (
                date TEXT PRIMARY KEY,
                location_id TEXT NOT NULL,
                preferred_spot_ids TEXT NOT NULL DEFAULT '[]',
                fulfilled INTEGER NOT NULL DEFAULT 0,
                reservation_id TEXT
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS attempt_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                date TEXT NOT NULL,
                spot_id TEXT,
                result TEXT NOT NULL,
                message TEXT
            )"""
        )
        self._conn.commit()

    def add_watch(self, date: str, location_id: str, preferred_spot_ids: list[str] | None = None) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO watch (date, location_id, preferred_spot_ids, fulfilled, reservation_id)
               VALUES (?, ?, ?, 0, NULL)""",
            (date, location_id, json.dumps(preferred_spot_ids or [])),
        )
        self._conn.commit()

    def remove_watch(self, date: str) -> None:
        self._conn.execute("DELETE FROM watch WHERE date = ?", (date,))
        self._conn.commit()

    def list_watches(self) -> list[WatchTarget]:
        rows = self._conn.execute(
            "SELECT date, location_id, preferred_spot_ids, fulfilled, reservation_id FROM watch ORDER BY date"
        ).fetchall()
        return [
            WatchTarget(
                date=date,
                location_id=location_id,
                preferred_spot_ids=json.loads(preferred_spot_ids),
                fulfilled=bool(fulfilled),
                reservation_id=reservation_id,
            )
            for date, location_id, preferred_spot_ids, fulfilled, reservation_id in rows
        ]

    def active_watches(self) -> list[WatchTarget]:
        return [w for w in self.list_watches() if not w.fulfilled]

    def mark_fulfilled(self, date: str, reservation_id: str) -> None:
        self._conn.execute(
            "UPDATE watch SET fulfilled = 1, reservation_id = ? WHERE date = ?", (reservation_id, date)
        )
        self._conn.commit()

    def log_attempt(self, date: str, spot_id: str | None, result: str, message: str = "") -> None:
        self._conn.execute(
            "INSERT INTO attempt_log (ts, date, spot_id, result, message) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), date, spot_id, result, message),
        )
        self._conn.commit()

    def recent_attempts(self, limit: int = 20) -> list[tuple]:
        return self._conn.execute(
            "SELECT ts, date, spot_id, result, message FROM attempt_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def close(self) -> None:
        self._conn.close()
