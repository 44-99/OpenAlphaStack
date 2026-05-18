# Paper Alpha Elasticity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve paper-mode upside elasticity by routing candidates through strategy-aware entry rules instead of buying every plan candidate as soon as price overlaps the entry zone.

**Architecture:** Phase A adds a strategy contract to `PlanManager` and `FastLane`: normalize each candidate into `breakout`, `pullback`, `defensive`, or `watch_only`; default unclear candidates to automatic `breakout`; prevent explicitly marked watch-only candidates from auto-buying; enforce daily new-position caps; and require breakout candidates to wait until an intraday confirmation time. Later phases can add probe sizing, pullback zones, and trailing exits on top of this stable contract.

**Tech Stack:** Python 3.10+, pytest, existing `alphaclaude.engine` modules, file-backed `plan.json/state.json/ledger.jsonl`.

---

### Task 1: Candidate Strategy Normalization

**Files:**
- Modify: `src/alphaclaude/engine/plan.py`
- Test: `tests/engine/test_alpha_elasticity.py`

- [x] Add tests that ambiguous candidates default to automatic `breakout`, source-B/high-volatility candidates default to `breakout`, defensive hints default to `defensive`, and explicit `watch_only` is preserved.
- [x] Implement `PlanManager.normalize_candidate_strategy()` and apply it from candidate update paths.
- [x] Run `python -m pytest -q tests/engine/test_alpha_elasticity.py -p no:cacheprovider`.

### Task 2: FastLane Phase A Routing

**Files:**
- Modify: `src/alphaclaude/engine/fast_lane.py`
- Test: `tests/engine/test_alpha_elasticity.py`

- [x] Add tests that `watch_only` candidates never auto-buy.
- [x] Add tests that `breakout` candidates do not buy before `confirm_after`.
- [x] Add tests that daily new-position cap rejects excess candidates and logs a deterministic `rejected_buy`.
- [x] Implement the minimal routing helpers in `FastLane`.
- [x] Run `python -m pytest -q tests/engine/test_alpha_elasticity.py -p no:cacheprovider`.

### Task 3: Status and Ledger Visibility

**Files:**
- Modify: `src/alphaclaude/engine/execution.py`
- Modify: `src/alphaclaude/tools/engine_status.py`
- Test: `tests/engine/test_alpha_elasticity.py`

- [x] Add tests that executed buy ledger entries include `strategy_type`.
- [x] Thread `strategy_type` through buy execution metadata without changing state semantics.
- [x] Show strategy type in plan summaries when present.
- [x] Run targeted tests.

### Task 4: Full Verification

**Files:**
- No additional files.

- [x] Run `python -m pytest -q -p no:cacheprovider`.
- [x] Run `python -m compileall -q src\alphaclaude`.
- [x] Inspect `git diff --stat`.
