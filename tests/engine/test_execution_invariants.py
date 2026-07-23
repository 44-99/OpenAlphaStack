from openalphastack.engine.execution import ExecutionEngine
from openalphastack.engine.ledger import Ledger
from openalphastack.engine.plan import PlanManager
from openalphastack.engine.state import EngineState


def make_execution(tmp_path):
    state = EngineState(str(tmp_path), initial_capital=100000)
    plan = PlanManager(str(tmp_path))
    ledger = Ledger(str(tmp_path))
    return state, plan, ledger, ExecutionEngine(state, plan, ledger, mode="paper", run_id="paper_rules")


def test_buy_rejects_invalid_identity_and_price(tmp_path):
    _state, _plan, _ledger, execution = make_execution(tmp_path)

    assert "六位数字" in execution.execute_buy("BAD", 100, 10)["error"]
    assert "大于 0" in execution.execute_buy("600519", 100, 0)["error"]


def test_buy_enforces_cash_reserve_at_execution_time(tmp_path):
    _state, plan, ledger, execution = make_execution(tmp_path)
    plan._data["rules"]["min_cash_reserve"] = 90000
    plan.save("test")

    result = execution.execute_buy("600519", 1000, 10)

    assert "扣除保留现金" in result["error"]
    assert ledger.read_all() == []


def test_buy_enforces_combined_single_position_cap(tmp_path):
    _state, _plan, ledger, execution = make_execution(tmp_path)
    assert execution.execute_buy("600519", 1000, 10)["status"] == "executed"

    result = execution.execute_buy("600519", 2000, 10)

    assert "单仓位超限 25.0%" in result["error"]
    assert len(ledger.read_all()) == 1


def test_buy_enforces_total_position_cap_at_execution_time(tmp_path):
    state, _plan, ledger, execution = make_execution(tmp_path)
    for code in ("600001", "600002", "600003"):
        state.add_holding(code, 2000, 10, "fixture")

    result = execution.execute_buy("600004", 2000, 10)

    assert "总仓位超限 80.0%" in result["error"]
    assert ledger.read_all() == []
