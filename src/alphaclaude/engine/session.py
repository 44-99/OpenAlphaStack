"""File-based session locking for Claude Code orchestration."""

from __future__ import annotations

import os
import time


class SessionLock:
    """File-based mutex. Only one Claude Code instance at a time."""

    def __init__(self, output_dir: str):
        self.lockfile = os.path.join(output_dir, ".session.lock")
        self._fd = None

    def acquire(self, timeout: float = 300) -> bool:
        """Block until lock acquired or timeout. Returns True if acquired."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if os.name == "nt":
                    self._fd = os.open(
                        self.lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR
                    )
                else:
                    import fcntl
                    self._fd = os.open(
                        self.lockfile, os.O_CREAT | os.O_RDWR
                    )
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, IOError):
                time.sleep(1)
        return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            try:
                os.remove(self.lockfile)
            except OSError:
                pass
            self._fd = None

    def locked(self) -> bool:
        return os.path.exists(self.lockfile)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
