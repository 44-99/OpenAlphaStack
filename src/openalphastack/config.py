import os
from dotenv import load_dotenv

from openalphastack.paths import DATA_DIR

load_dotenv()

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

FEISHU_BOT_NAME = os.getenv("FEISHU_BOT_NAME", "")
FEISHU_BOT_OPEN_ID = os.getenv("FEISHU_BOT_OPEN_ID", "")

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# Stock data config
STOCK_DATA_DIR = str(DATA_DIR)

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Crash alert — comma-separated Feishu chat IDs that receive crash notifications
_alert_raw = os.getenv("ALERT_CHAT_IDS", "")
ALERT_CHAT_IDS = [cid.strip() for cid in _alert_raw.split(",") if cid.strip()]

# Engine notifications — comma-separated Feishu chat IDs for engine events
# (start/stop/trades/alerts/daily summary). Falls back to ALERT_CHAT_IDS if unset.
_engine_raw = os.getenv("ENGINE_CHAT_IDS", "")
ENGINE_CHAT_IDS = [cid.strip() for cid in _engine_raw.split(",") if cid.strip()] or ALERT_CHAT_IDS
