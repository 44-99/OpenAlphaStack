"""Trading engine constants shared across runtime modes."""

from __future__ import annotations

from datetime import time as dtime


STAMP_DUTY = 0.001         # 0.1% (sell only)
COMMISSION = 0.0003        # 0.03% (buy + sell)
LOT_SIZE = 100             # 100-share board lot
MIN_COMMISSION = 5.0       # minimum commission per trade

PRE_MARKET_START = dtime(8, 0)
AUCTION_START = dtime(9, 15)
AUCTION_END = dtime(9, 25)
MORNING_START = dtime(9, 30)
MORNING_END = dtime(11, 30)
AFTERNOON_START = dtime(13, 0)
AFTERNOON_END = dtime(15, 0)
