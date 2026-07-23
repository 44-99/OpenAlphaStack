"""Append-only decision ledger for engine runs."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

from openalphastack.engine.run_store import RunStore


logger = logging.getLogger(__name__)


class Ledger:
    """Append-only decision ledger. Cross-session decision continuity."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "ledger.jsonl")
        self._lock = threading.Lock()
        self.store = RunStore(output_dir)
        legacy = self._read_legacy()
        self.store.import_ledger(legacy)
        self._seq = self.store.ledger_count()

    def _read_legacy(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    value = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def append(self, entry: dict) -> int:
        """Append a decision entry. Returns sequence number."""
        with self._lock:
            payload = dict(entry)
            payload["time"] = payload.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._seq = self.store.append_ledger(payload)
            entry.update(payload)
            entry["seq"] = self._seq
            try:
                self.export_jsonl()
            except OSError as exc:
                logger.warning("Ledger projection refresh failed; SQLite remains canonical: %s", exc)
        return self._seq

    def read_all(self) -> list[dict]:
        return self.store.read_ledger()

    def read_recent(self, n: int = 20) -> list[dict]:
        return self.read_all()[-n:]

    @property
    def next_seq(self) -> int:
        return self._seq + 1

    def export_jsonl(self) -> None:
        """Refresh the human-readable projection from the SQLite source of truth."""
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            for entry in self.store.read_ledger():
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(temp, self.path)
