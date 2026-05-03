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
CLAUDE_CMD = os.getenv("CLAUDE_CMD", r"C:\Users\Administrator\AppData\Roaming\npm\claude.cmd")
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "300"))

# Default chat ID for scheduled broadcasts
# Set this after capturing from first message
TARGET_CHAT_IDS = []  # list of chat_ids to send scheduled reports to
