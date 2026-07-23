from __future__ import annotations

import json
import os

from openalphastack.engine import state as state_module
from openalphastack.engine.state import EngineState


def test_state_save_retries_transient_windows_replace_denial(tmp_path, monkeypatch):
    engine_state = EngineState(str(tmp_path), 100000)
    real_replace = os.replace
    calls = []

    def flaky_replace(src: str, dst: str) -> None:
        calls.append((src, dst))
        if len(calls) == 1:
            raise PermissionError(5, "access denied", dst)
        real_replace(src, dst)

    monkeypatch.setattr(state_module.os, "replace", flaky_replace)
    monkeypatch.setattr(state_module.time, "sleep", lambda _seconds: None)

    engine_state.set_data_time("2026-06-09 13:55:00")
    engine_state.save()

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["data_time"] == "2026-06-09 13:55:00"
    assert len(calls) == 2

