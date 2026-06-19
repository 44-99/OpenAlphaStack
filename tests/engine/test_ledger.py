from __future__ import annotations

import re

from alphaclaude.engine.ledger import Ledger


def test_ledger_append_defaults_to_full_datetime(tmp_path):
    ledger = Ledger(str(tmp_path))

    ledger.append({"decision": "open_position", "symbol": "600500"})

    [entry] = ledger.read_all()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", entry["time"])
