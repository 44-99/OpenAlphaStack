from __future__ import annotations

import json

from alphaclaude.app import scheduler as scheduler_module


class _FakeScheduler:
    last: "_FakeScheduler | None" = None

    def __init__(self):
        self.jobs = []
        self.started = False
        _FakeScheduler.last = self

    def add_job(self, func, trigger, id=None, name=None, replace_existing=False):
        self.jobs.append({
            "func": func,
            "trigger": trigger,
            "id": id,
            "name": name,
            "replace_existing": replace_existing,
        })

    def start(self):
        self.started = True

    def get_jobs(self):
        return self.jobs


def _install_fake_scheduler(monkeypatch):
    _FakeScheduler.last = None
    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    monkeypatch.setattr(scheduler_module, "BackgroundScheduler", _FakeScheduler)
    monkeypatch.setattr(scheduler_module, "restore_dynamic_tasks", lambda: None)


def test_start_scheduler_without_legacy_market_jobs(monkeypatch):
    _install_fake_scheduler(monkeypatch)

    scheduler_module.start_scheduler(include_market_jobs=False)

    scheduler = _FakeScheduler.last
    assert scheduler is not None
    assert scheduler.started is True

    job_ids = {job["id"] for job in scheduler.jobs}
    assert "morning" not in job_ids
    assert "midday" not in job_ids
    assert "closing" not in job_ids
    assert {"dream_am", "dream_pm", "agent_premarket_plan", "agent_postclose_review"} <= job_ids


def test_start_scheduler_default_keeps_legacy_jobs_for_explicit_legacy_use(monkeypatch):
    _install_fake_scheduler(monkeypatch)

    scheduler_module.start_scheduler()

    scheduler = _FakeScheduler.last
    assert scheduler is not None
    job_ids = {job["id"] for job in scheduler.jobs}
    assert {"morning", "midday", "closing", "dream_am", "dream_pm", "agent_premarket_plan", "agent_postclose_review"} <= job_ids


def test_launch_scheduled_agent_task_starts_external_process(tmp_path, monkeypatch):
    launched = []

    class FakeProc:
        pid = 4567

    def fake_popen(cmd, **kwargs):
        launched.append((cmd, kwargs))
        return FakeProc()

    monkeypatch.setattr(scheduler_module, "STOCK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler_module, "_PROJECT_ROOT", str(tmp_path.parent))
    monkeypatch.setattr(scheduler_module, "_is_trading_day_for_agent_task", lambda _today: True)
    monkeypatch.setattr(scheduler_module, "_is_agent_task_process_running", lambda _task_id, _today: False)
    monkeypatch.setattr(scheduler_module.subprocess, "Popen", fake_popen)

    result = scheduler_module._launch_scheduled_agent_task("premarket_plan", today=scheduler_module.date(2026, 6, 20))

    assert result["started"] is True
    assert result["pid"] == 4567
    cmd = launched[0][0]
    assert cmd[:4] == [scheduler_module.sys.executable, "-u", "-m", "alphaclaude.engine.cli"]
    assert ["--agent-task", "premarket_plan"] == cmd[4:6]
    sentinel = tmp_path / "state" / ".agent_task_premarket_plan_last_start"
    assert sentinel.read_text(encoding="utf-8") == "2026-06-20"


def test_launch_scheduled_agent_task_skips_existing_sentinel(tmp_path, monkeypatch):
    sentinel = tmp_path / "state" / ".agent_task_postclose_review_last_start"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("2026-06-20", encoding="utf-8")
    launched = []

    monkeypatch.setattr(scheduler_module, "STOCK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler_module, "_is_trading_day_for_agent_task", lambda _today: True)
    monkeypatch.setattr(scheduler_module.subprocess, "Popen", lambda *a, **k: launched.append(a))

    result = scheduler_module._launch_scheduled_agent_task("postclose_review", today=scheduler_module.date(2026, 6, 20))

    assert result["started"] is False
    assert result["reason"] == "already_started"
    assert launched == []


def test_launch_scheduled_agent_task_records_failure_without_sentinel(tmp_path, monkeypatch):
    def fake_popen(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(scheduler_module, "STOCK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler_module, "_PROJECT_ROOT", str(tmp_path.parent))
    monkeypatch.setattr(scheduler_module, "_is_trading_day_for_agent_task", lambda _today: True)
    monkeypatch.setattr(scheduler_module, "_is_agent_task_process_running", lambda _task_id, _today: False)
    monkeypatch.setattr(scheduler_module.subprocess, "Popen", fake_popen)

    result = scheduler_module._launch_scheduled_agent_task("premarket_plan", today=scheduler_module.date(2026, 6, 20))

    assert result["started"] is False
    assert result["reason"] == "launch_failed"
    assert "spawn failed" in result["error"]
    assert not (tmp_path / "state" / ".agent_task_premarket_plan_last_start").exists()
    events = (tmp_path / "output" / "agent_2026-06-20_premarket_plan" / "workflow_events.jsonl").read_text(encoding="utf-8")
    assert "agent_task_launch" in events
    assert "spawn failed" in events


def test_agent_task_running_detects_live_state(tmp_path, monkeypatch):
    run_dir = tmp_path / "output" / "agent_2026-06-20_premarket_plan"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({"engine_meta": {"process_id": 9999, "status": "running"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler_module, "STOCK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler_module, "_is_pid_alive", lambda pid: int(pid or 0) == 9999)

    assert scheduler_module._is_agent_task_process_running("premarket_plan", scheduler_module.date(2026, 6, 20)) is True
