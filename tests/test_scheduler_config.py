from __future__ import annotations

import scheduler as scheduler_module


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
    assert {"dream_am", "dream_pm"} <= job_ids


def test_start_scheduler_default_keeps_legacy_jobs_for_explicit_legacy_use(monkeypatch):
    _install_fake_scheduler(monkeypatch)

    scheduler_module.start_scheduler()

    scheduler = _FakeScheduler.last
    assert scheduler is not None
    job_ids = {job["id"] for job in scheduler.jobs}
    assert {"morning", "midday", "closing", "dream_am", "dream_pm"} <= job_ids
