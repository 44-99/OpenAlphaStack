from __future__ import annotations

import pytest

from openalphastack.engine.execution import ExecutionEngine
from openalphastack.engine.ledger import Ledger
from openalphastack.engine.plan import PlanManager
from openalphastack.engine.state import EngineState


def test_buy_rolls_back_state_when_transaction_fails(tmp_path, monkeypatch):
    state = EngineState(str(tmp_path), initial_capital=100000)
    plan = PlanManager(str(tmp_path))
    ledger = Ledger(str(tmp_path))
    execution = ExecutionEngine(state, plan, ledger, mode="paper", run_id="paper_atomic")
    monkeypatch.setattr(
        state.store,
        "commit_trade",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected transaction failure")),
    )

    before = state.load()
    with pytest.raises(OSError, match="injected transaction failure"):
        execution.execute_buy("600519", 100, 10.0)

    after = state.load()
    assert after == before
    assert ledger.read_all() == []
