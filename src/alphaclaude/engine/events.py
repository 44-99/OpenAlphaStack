"""Persistent event queue for batching fast-lane signals."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime


class EventQueue:
    """Thread-safe queue backed by a JSONL file. Crash-recoverable."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "event_queue.jsonl")
        self._lock = threading.Lock()

    def push(self, event: dict) -> None:
        event["id"] = uuid.uuid4().hex[:8]
        event["timestamp"] = datetime.now().isoformat()
        event["processed"] = False
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def pop_unprocessed(self) -> list[dict]:
        """Get all unprocessed events and mark them as processed."""
        if not os.path.exists(self.path):
            return []
        with self._lock:
            events = []
            lines = []
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        lines.append(("", False))
                        continue
                    try:
                        e = json.loads(line)
                        if not e.get("processed"):
                            e["processed"] = True
                            events.append(e)
                        lines.append((json.dumps(e, ensure_ascii=False), True))
                    except json.JSONDecodeError:
                        lines.append((line, False))
            with open(self.path, "w", encoding="utf-8") as f:
                for ltext, _ in lines:
                    if ltext:
                        f.write(ltext + "\n")
        return events

    def pending_count(self) -> int:
        if not os.path.exists(self.path):
            return 0
        count = 0
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line.strip())
                        if not e.get("processed"):
                            count += 1
                    except json.JSONDecodeError:
                        pass
        return count

    def should_trigger(self, count_threshold: int = 3, time_threshold: int = 900) -> bool:
        """Check if enough events accumulated to trigger Claude Code."""
        pending = self.pending_count()
        if pending >= count_threshold:
            return True
        if pending > 0 and os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line.strip())
                        if not e.get("processed"):
                            ts = datetime.fromisoformat(e["timestamp"])
                            if (datetime.now() - ts).total_seconds() > time_threshold:
                                return True
                            break
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass
        return False
