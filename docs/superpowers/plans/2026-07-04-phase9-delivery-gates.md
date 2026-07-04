# Phase 9 Delivery Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent delivery package export when Phase 8 quality gates indicate that an asset is blocked, and require explicit override for review-required assets.

**Architecture:** Add a focused `delivery_gate.py` policy module that interprets `quality_gate.json` and returns an allow/block decision. Keep `delivery_package.py` as a pure packaging module; enforce the policy in the `export-delivery-package` CLI command and expose the policy status through existing deployment/delivery reports and frontend display.

**Tech Stack:** Python standard library, existing CLI parser/commands, FastAPI report reads, static frontend JavaScript/CSS, pytest.

---

### Task 1: P9-M1 Delivery Gate Policy

**Files:**
- Create: `src/pc_system/delivery_gate.py`
- Test: `tests/test_phase9_delivery_gates.py`

- [ ] Write failing tests for `evaluate_delivery_gate` covering `passed`, `review_required`, and `blocked`.
- [ ] Verify RED with `python -m pytest tests\test_phase9_delivery_gates.py::test_p9_m1_evaluates_delivery_gate_policy -q -p no:cacheprovider`.
- [ ] Implement the minimal policy function.
- [ ] Verify GREEN.

### Task 2: P9-M2 Block Export for Blocked Quality Gate

**Files:**
- Modify: `src/pc_system/commands/phase3.py`
- Test: `tests/test_phase9_delivery_gates.py`

- [ ] Write failing CLI test proving `export-delivery-package` returns 2 and does not create delivery output when quality gate is `blocked`.
- [ ] Add a quality gate read in `run_export_delivery_package` before export.
- [ ] Verify Phase 9 tests pass.

### Task 3: P9-M3 Review Override

**Files:**
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Modify: `src/pc_system/commands/phase3.py`
- Test: `tests/test_phase9_delivery_gates.py`

- [ ] Write failing CLI tests proving `review_required` blocks by default but passes with `--allow-review-required`.
- [ ] Add parser argument and function parameter.
- [ ] Verify Phase 9 tests pass.

### Task 4: P9-M4 Deployment Checklist Gate Status

**Files:**
- Modify: `src/pc_system/deployment_checklist.py`
- Test: `tests/test_phase9_delivery_gates.py`

- [ ] Write failing test proving deployment checklist includes `quality_gate` item and blocks when the gate is blocked.
- [ ] Add quality gate as a required checklist item.
- [ ] Verify Phase 9 tests pass.

### Task 5: P9-M5 Frontend Delivery Gate Notice

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Test: `tests/test_phase9_delivery_gates.py`

- [ ] Write failing frontend contract test for `delivery-gate-notice`, `renderDeliveryGateNotice`, and delivery gate messaging.
- [ ] Add a compact notice derived from the current quality gate status.
- [ ] Verify Phase 9 tests pass.

### Task 6: P9-M6 Docs and Regression

**Files:**
- Create: `docs/phase9-delivery-gates.md`
- Modify: `README.md`
- Test: `tests/test_phase9_delivery_gates.py`

- [ ] Write failing docs contract test for `P9-M1`, `P9-M6`, `--allow-review-required`, and blocked delivery behavior.
- [ ] Update docs and README.
- [ ] Run full verification: tests, compileall, and frontend JS syntax checks.
