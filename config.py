import os
from dotenv import load_dotenv

load_dotenv()

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# Stock data config
STOCK_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Claude CLI
CLAUDE_CMD = r"C:\Users\Administrator\AppData\Roaming\npm\claude.cmd"
CLAUDE_TIMEOUT = 120  # seconds

# Default chat ID for scheduled broadcasts
# Set this after capturing from first message
TARGET_CHAT_IDS = []  # list of chat_ids to send scheduled reports to
