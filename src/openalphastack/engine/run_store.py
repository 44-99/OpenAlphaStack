"""Transactional local persistence for plans, account state and audit events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


class PlanRevisionConflict(RuntimeError):
    """Raised when a plan publication is based on a stale revision."""


class RunStore:
    """SQLite-backed source of truth for one engine run.

    JSON and JSONL files remain human-readable projections. Correctness and
    cross-record atomicity live here.
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.output_dir / "run.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS active_plan (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plan_mutations (
                    idempotency_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _load(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}

    def load_state(self) -> tuple[dict[str, Any], int]:
        with self._connect() as connection:
            row = connection.execute("SELECT payload, revision FROM runtime_state WHERE singleton = 1").fetchone()
        return (self._load(row["payload"]), int(row["revision"])) if row else ({}, 0)

    def save_state(self, payload: dict[str, Any]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.transaction() as connection:
            row = connection.execute("SELECT revision FROM runtime_state WHERE singleton = 1").fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            connection.execute(
                """INSERT INTO runtime_state(singleton, payload, revision, updated_at)
                   VALUES(1, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                     payload=excluded.payload, revision=excluded.revision, updated_at=excluded.updated_at""",
                (self._dump(payload), revision, now),
            )
        return revision

    def load_plan(self) -> tuple[dict[str, Any], int]:
        with self._connect() as connection:
            row = connection.execute("SELECT payload, revision FROM active_plan WHERE singleton = 1").fetchone()
        return (self._load(row["payload"]), int(row["revision"])) if row else ({}, 0)

    def save_plan(self, payload: dict[str, Any]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.transaction() as connection:
            row = connection.execute("SELECT revision FROM active_plan WHERE singleton = 1").fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            connection.execute(
                """INSERT INTO active_plan(singleton, payload, revision, updated_at)
                   VALUES(1, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                     payload=excluded.payload, revision=excluded.revision, updated_at=excluded.updated_at""",
                (self._dump(payload), revision, now),
            )
        return revision

    def publish_plan(
        self,
        payload: dict[str, Any],
        mutation: dict[str, Any],
        *,
        expected_updated: str = "",
    ) -> tuple[int, bool, dict[str, Any]]:
        """Atomically publish a plan and record its idempotency key."""
        key = str(mutation["idempotency_key"])
        now = datetime.now().isoformat(timespec="seconds")
        with self.transaction() as connection:
            replay = connection.execute(
                "SELECT payload FROM plan_mutations WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if replay:
                return 0, True, self._load(replay["payload"])

            current = connection.execute(
                "SELECT payload, revision FROM active_plan WHERE singleton = 1"
            ).fetchone()
            current_payload = self._load(current["payload"]) if current else {}
            if expected_updated and str(current_payload.get("updated") or "") != expected_updated:
                raise PlanRevisionConflict("plan changed since it was read")

            revision = int(current["revision"]) + 1 if current else 1
            connection.execute(
                """INSERT INTO active_plan(singleton, payload, revision, updated_at)
                   VALUES(1, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                     payload=excluded.payload, revision=excluded.revision, updated_at=excluded.updated_at""",
                (self._dump(payload), revision, now),
            )
            connection.execute(
                "INSERT INTO plan_mutations(idempotency_key, payload, created_at) VALUES(?, ?, ?)",
                (key, self._dump(mutation), now),
            )
        return revision, False, dict(mutation)

    def ledger_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM ledger_events").fetchone()
        return int(row["seq"])

    def append_ledger(self, entry: dict[str, Any]) -> int:
        payload = dict(entry)
        payload.pop("seq", None)
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO ledger_events(payload, created_at) VALUES(?, ?)",
                (self._dump(payload), datetime.now().isoformat(timespec="seconds")),
            )
            seq = int(cursor.lastrowid)
        return seq

    def commit_trade(self, state: dict[str, Any], entry: dict[str, Any]) -> tuple[int, int]:
        """Commit account state and its matching ledger event atomically."""
        now = datetime.now().isoformat(timespec="seconds")
        payload = dict(entry)
        payload.pop("seq", None)
        with self.transaction() as connection:
            row = connection.execute("SELECT revision FROM runtime_state WHERE singleton = 1").fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            connection.execute(
                """INSERT INTO runtime_state(singleton, payload, revision, updated_at)
                   VALUES(1, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                     payload=excluded.payload, revision=excluded.revision, updated_at=excluded.updated_at""",
                (self._dump(state), revision, now),
            )
            cursor = connection.execute(
                "INSERT INTO ledger_events(payload, created_at) VALUES(?, ?)",
                (self._dump(payload), now),
            )
            seq = int(cursor.lastrowid)
        return revision, seq

    def read_ledger(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT seq, payload FROM ledger_events ORDER BY seq"
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql = "SELECT seq, payload FROM ledger_events ORDER BY seq DESC LIMIT ?"
            params = (max(1, int(limit)),)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        if limit is not None:
            rows = list(reversed(rows))
        result = []
        for row in rows:
            payload = self._load(row["payload"])
            payload["seq"] = int(row["seq"])
            result.append(payload)
        return result

    def import_ledger(self, entries: list[dict[str, Any]]) -> None:
        if not entries or self.ledger_count():
            return
        with self.transaction() as connection:
            for entry in entries:
                payload = dict(entry)
                seq = int(payload.pop("seq", 0) or 0)
                if seq > 0:
                    connection.execute(
                        "INSERT INTO ledger_events(seq, payload, created_at) VALUES(?, ?, ?)",
                        (seq, self._dump(payload), str(payload.get("time") or datetime.now().isoformat())),
                    )
                else:
                    connection.execute(
                        "INSERT INTO ledger_events(payload, created_at) VALUES(?, ?)",
                        (self._dump(payload), str(payload.get("time") or datetime.now().isoformat())),
                    )

    def read_snapshot(self, ledger_limit: int = 100) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            state_row = connection.execute("SELECT payload, revision FROM runtime_state WHERE singleton = 1").fetchone()
            plan_row = connection.execute("SELECT payload, revision FROM active_plan WHERE singleton = 1").fetchone()
            ledger_rows = connection.execute(
                "SELECT seq, payload FROM ledger_events ORDER BY seq DESC LIMIT ?",
                (max(1, int(ledger_limit)),),
            ).fetchall()
            connection.commit()
        ledger = []
        for row in reversed(ledger_rows):
            payload = self._load(row["payload"])
            payload["seq"] = int(row["seq"])
            ledger.append(payload)
        return {
            "state": self._load(state_row["payload"]) if state_row else {},
            "state_revision": int(state_row["revision"]) if state_row else 0,
            "plan": self._load(plan_row["payload"]) if plan_row else {},
            "plan_revision": int(plan_row["revision"]) if plan_row else 0,
            "ledger_tail": ledger,
        }
