# Phase 7 Real LAS Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 7 so existing workspace LAS/LAZ assets can be sampled and analyzed without requiring users to manually create point-record JSON files.

**Architecture:** Add a focused sampling boundary that converts real or lightweight LAS-like sources into the P6 point-record contract. Keep `point_cloud_analysis.py` as the analysis core, add a `las_sampling.py` adapter, then connect it through CLI, asset registry, API, frontend, and docs.

**Tech Stack:** Python standard library, optional `laspy`, existing FastAPI app, existing static frontend JavaScript/CSS, pytest.

---

### Task 1: P7-M1 LAS/LAZ Sampling Adapter

**Files:**
- Create: `src/pc_system/las_sampling.py`
- Test: `tests/test_phase7_real_las_analysis.py`

- [ ] Write a failing test for sampling from a lightweight exported point JSON file with `max_points`.
- [ ] Run the single test and confirm it fails because `pc_system.las_sampling` does not exist.
- [ ] Implement `sample_points_from_source(path, max_points=10000)` with JSON input support and optional LAS/LAZ fallback error messaging.
- [ ] Run the Phase 7 test and confirm it passes.

### Task 2: P7-M2 analyze-asset CLI

**Files:**
- Modify: `src/pc_system/commands/phase6.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Test: `tests/test_phase7_real_las_analysis.py`

- [ ] Write a failing CLI test that creates `data/assets/<asset_id>/asset.json` with a source path and runs `analyze-asset`.
- [ ] Run the single test and confirm it fails because the command does not exist.
- [ ] Implement `run_analyze_asset(project_root, asset_id, max_points, grid_cell_size)` using ProjectConfig paths and `sample_points_from_source`.
- [ ] Add parser and CLI dispatch for `analyze-asset`.
- [ ] Run the Phase 7 tests and confirm they pass.

### Task 3: P7-M3 Asset Registry Analysis Status

**Files:**
- Modify: `src/pc_system/asset_registry.py`
- Test: `tests/test_phase7_real_las_analysis.py`

- [ ] Write a failing test proving `asset_index.json` includes `analysis_status` and `analysis_report_path` when a report exists.
- [ ] Run the single test and confirm it fails because registry output lacks the fields.
- [ ] Extend registry item creation to inspect `reports/analysis/<asset_id>/point_cloud_analysis.json`.
- [ ] Run Phase 7 tests and confirm they pass.

### Task 4: P7-M4 API Analysis Overview

**Files:**
- Modify: `src/pc_system/api.py`
- Test: `tests/test_phase7_real_las_analysis.py`

- [ ] Write a failing API test for `GET /analysis` returning asset-level analysis summaries.
- [ ] Run the single test and confirm it fails with 404.
- [ ] Add `GET /analysis` that scans reports and returns `asset_count` plus summary rows.
- [ ] Run Phase 7 tests and confirm they pass.

### Task 5: P7-M5 Frontend Analysis Status

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Test: `tests/test_phase7_real_las_analysis.py`

- [ ] Write a failing frontend contract test for analysis status markers, `fetchAnalysisOverview`, and `/analysis` usage.
- [ ] Run the single test and confirm it fails because the frontend contract is absent.
- [ ] Add a compact analysis status panel and JS fetch/render functions.
- [ ] Run Phase 7 tests and confirm they pass.

### Task 6: P7-M6 Docs and Regression

**Files:**
- Create: `docs/phase7-real-las-analysis.md`
- Modify: `README.md`
- Test: `tests/test_phase7_real_las_analysis.py`

- [ ] Write a failing docs contract test for P7-M1 through P7-M6, `analyze-asset`, and `GET /analysis`.
- [ ] Run the single test and confirm it fails.
- [ ] Update README and add the Phase 7 documentation.
- [ ] Run `python -m pytest tests -q -p no:cacheprovider`.
- [ ] Run `python -m compileall -q src tests scripts`.
- [ ] Run `node --check frontend\app.js` and `node --check frontend\viewer.js`.
