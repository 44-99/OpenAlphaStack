"""
Structured logging with JSON output and automatic rotation.
"""
import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emits log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            import traceback
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))
        for key in ("category", "chat_id", "session_id"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(log_dir: str, level: str = "INFO") -> logging.Logger:
    """
    Configure root logger with 3 handlers:
    1. stdout — plain text for Docker logs (INFO+)
    2. data/bot.json.log — rotating JSON full trace (DEBUG+)
    3. data/bot.error.log — rotating JSON errors only (ERROR+)
    """
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove existing handlers to avoid duplicates on reload
    root.handlers.clear()

    # 1. Console — plain text
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(console)

    # 2. JSON full log — rotating, DEBUG
    json_log = os.path.join(log_dir, "logs", "bot.json.log")
    json_handler = logging.handlers.RotatingFileHandler(
        json_log, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    json_handler.setLevel(logging.DEBUG)
    json_handler.setFormatter(JSONFormatter())
    root.addHandler(json_handler)

    # 3. JSON error log — rotating, ERROR only
    err_log = os.path.join(log_dir, "logs", "bot.error.log")
    err_handler = logging.handlers.RotatingFileHandler(
        err_log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(JSONFormatter())
    root.addHandler(err_handler)

    logging.captureWarnings(True)
    return root
