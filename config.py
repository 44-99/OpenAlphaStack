import os
from dotenv import load_dotenv

load_dotenv()

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

FEISHU_BOT_NAME = os.getenv("FEISHU_BOT_NAME", "")
FEISHU_BOT_OPEN_ID = os.getenv("FEISHU_BOT_OPEN_ID", "")

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# Stock data config
STOCK_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Claude CLI
CLAUDE_CMD = os.getenv("CLAUDE_CMD", "claude")
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "300"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Crash alert — comma-separated Feishu chat IDs that receive crash notifications
_alert_raw = os.getenv("ALERT_CHAT_IDS", "")
ALERT_CHAT_IDS = [cid.strip() for cid in _alert_raw.split(",") if cid.strip()]
