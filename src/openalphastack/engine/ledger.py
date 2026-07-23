"""Append-only decision ledger for engine runs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime


class Ledger:
    """Append-only decision ledger. Cross-session decision continuity."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "ledger.jsonl")
        self._lock = threading.Lock()
        self._seq = self._count()

    def _count(self) -> int:
        if not os.path.exists(self.path):
            return 0
        c = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for _ in f:
                c += 1
        return c

    def append(self, entry: dict) -> int:
        """Append a decision entry. Returns sequence number."""
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            entry["time"] = entry.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return self._seq

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def read_recent(self, n: int = 20) -> list[dict]:
        return self.read_all()[-n:]

    @property
    def next_seq(self) -> int:
        return self._seq + 1
