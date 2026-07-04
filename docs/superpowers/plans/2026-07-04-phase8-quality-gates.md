# Phase 8 Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Phase 6/7 analysis findings into explicit quality-gate decisions that can be read by CLI, API, and frontend workflows.

**Architecture:** Add a small `quality_gate.py` domain module that maps analysis findings into a separate gate report. Reuse existing JSON/Markdown report patterns, expose it through a Phase 8 CLI command, add a read-only FastAPI endpoint, and render a compact status bar in the dashboard.

**Tech Stack:** Python standard library, existing FastAPI app, static frontend JavaScript/CSS, pytest, existing project JSON IO helpers.

---

### Task 1: P8-M1 Findings Rule Mapping

**Files:**
- Create: `src/pc_system/quality_gate.py`
- Test: `tests/test_phase8_quality_gates.py`

- [ ] Write a failing test for `build_quality_gate("scan", analysis)` mapping `high_z_span` to `blocked` and `low_rgb_coverage` to `review_required`.
- [ ] Run: `python -m pytest tests\test_phase8_quality_gates.py::test_p8_m1_maps_analysis_findings_to_quality_gate -q -p no:cacheprovider`. Expected: fail because module is missing.
- [ ] Implement `build_quality_gate` with `status`, `severity`, `actions`, and `finding_count`.
- [ ] Re-run the test. Expected: pass.

### Task 2: P8-M2 Quality Gate Report Writer

**Files:**
- Modify: `src/pc_system/quality_gate.py`
- Test: `tests/test_phase8_quality_gates.py`

- [ ] Write a failing test for `write_quality_gate_report(gate, output_dir)` creating `quality_gate.json` and `quality_gate.md`.
- [ ] Run the single test. Expected: fail because writer is missing.
- [ ] Implement JSON and Markdown writing with Chinese comments.
- [ ] Re-run Phase 8 tests. Expected: pass.

### Task 3: P8-M3 check-quality-gate CLI

**Files:**
- Create: `src/pc_system/commands/phase8.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Test: `tests/test_phase8_quality_gates.py`

- [ ] Write a failing CLI test that reads `reports/analysis/<asset_id>/point_cloud_analysis.json` and writes quality gate outputs.
- [ ] Run the single test. Expected: fail because `check-quality-gate` command is unknown.
- [ ] Implement `run_check_quality_gate(project_root, asset_id)`.
- [ ] Add parser and CLI dispatch.
- [ ] Re-run Phase 8 tests. Expected: pass.

### Task 4: P8-M4 Quality Gate API

**Files:**
- Modify: `src/pc_system/api.py`
- Test: `tests/test_phase8_quality_gates.py`

- [ ] Write a failing API test for `GET /quality-gates/<asset_id>` returning the gate report.
- [ ] Run the single test. Expected: 404.
- [ ] Add read-only API route using existing `_read_json_or_404` style.
- [ ] Re-run Phase 8 tests. Expected: pass.

### Task 5: P8-M5 Frontend Gate Status Bar

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Test: `tests/test_phase8_quality_gates.py`

- [ ] Write a failing frontend contract test for `quality-gate-status-bar`, `fetchQualityGate`, `renderQualityGateStatus`, and `/quality-gates/`.
- [ ] Run the single test. Expected: fail because contract is absent.
- [ ] Add HTML status bar, JS fetch/render logic, and CSS states for `passed`, `review_required`, and `blocked`.
- [ ] Re-run Phase 8 tests. Expected: pass.

### Task 6: P8-M6 Docs and Full Regression

**Files:**
- Create: `docs/phase8-quality-gates.md`
- Modify: `README.md`
- Test: `tests/test_phase8_quality_gates.py`

- [ ] Write a failing docs contract test for `P8-M1`, `P8-M6`, `check-quality-gate`, and `GET /quality-gates/<asset_id>`.
- [ ] Run the single test. Expected: fail because docs are missing.
- [ ] Update README and add Phase 8 docs.
- [ ] Run `python -m pytest tests -q -p no:cacheprovider`.
- [ ] Run `python -m compileall -q src tests scripts`.
- [ ] Run `node --check frontend\app.js` and `node --check frontend\viewer.js`.
