from __future__ import annotations

import logging
import shutil
import uuid

from openalphastack import paths
from openalphastack.logging_config import setup_logging


def test_setup_logging_creates_nested_logs_directory():
    root = paths.PROJECT_ROOT / "data" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    log_root = root / f"logging_{uuid.uuid4().hex}"

    logger = setup_logging(str(log_root), "INFO")
    logger.info("log directory smoke test")

    assert (log_root / "logs" / "bot.json.log").exists()
    assert (log_root / "logs" / "bot.error.log").exists()

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logging.shutdown()
    shutil.rmtree(log_root, ignore_errors=True)
