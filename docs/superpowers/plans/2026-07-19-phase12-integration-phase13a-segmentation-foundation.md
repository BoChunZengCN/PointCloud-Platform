# Phase 12 Integration and Phase 13A Segmentation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the completed Phase 12 reliability work into `main`, establish a reproducible green baseline, and implement a truthful, versioned, conservative Phase 13A segmentation pipeline that later model retrieval can consume.

**Architecture:** Preserve the existing file-backed workflow and compatibility report, but introduce immutable segmentation runs below `reports/segmentation_runs/<asset_id>/<run_id>/`. Separate preprocessing, engine selection, orchestration, artifacts, and operational quality into focused modules. Keep heavy dependencies optional and record the requested engine, executed engine, fallback reason, configuration fingerprint, and data lineage.

**Tech Stack:** Python 3.11+, pytest 8.x, FastAPI, JSON/Markdown artifacts, optional Open3D external adapter, static HTML/JavaScript frontend.

## Global Constraints

- Original point-cloud files are immutable; preprocessing always creates derived in-memory data or artifacts.
- Phase 13A does not claim real accuracy without golden labels.
- `requested_engine`, `executed_engine`, and `fallback_reason` must describe actual execution.
- A failed run never replaces the latest successful compatibility report.
- The existing `GET /segments/<asset_id>/objects` contract remains readable.
- Heavy Open3D/PCL/learned engines remain optional and behind adapters.
- Thin pipes, cables, handrails, and walls must not be silently removed.
- Every code change follows red-green-refactor TDD and ends with a focused commit.

---

## Planned File Structure

- Create `src/pc_system/segmentation_run.py`: run IDs, configuration fingerprints, lifecycle records, atomic run persistence, and latest-success projection.
- Create `src/pc_system/segmentation_preprocessing.py`: input validation, deduplication, optional voxel sampling, and conservative preprocessing metrics.
- Create `src/pc_system/segmentation_engines.py`: engine registry, truthful execution metadata, explicit fallback behavior.
- Create `src/pc_system/segmentation_service.py`: orchestration from asset points through preprocessing, engine execution, membership artifacts, quality, and compatibility projection.
- Create `src/pc_system/segmentation_operational_quality.py`: no-label proxy metrics and `passed`/`review_required` decisions.
- Create `src/pc_system/commands/phase13.py`: Phase 13A CLI handlers.
- Modify `src/pc_system/object_segmentation.py`: preserve Phase 10 behavior while exposing internal membership indices to the new service.
- Modify `src/pc_system/cli_parser.py` and `src/pc_system/cli.py`: add Phase 13A commands.
- Modify `src/pc_system/api.py`: expose segmentation-run list, detail, and quality routes.
- Modify `frontend/index.html`, `frontend/app.js`, and `frontend/app.css`: show latest run engine, status, and findings.
- Create `tests/test_phase13_segmentation_run.py`.
- Create `tests/test_phase13_segmentation_preprocessing.py`.
- Create `tests/test_phase13_segmentation_engines.py`.
- Create `tests/test_phase13_segmentation_service.py`.
- Create `tests/test_phase13_operational_quality.py`.
- Create `tests/test_phase13_cli_api.py`.
- Create `docs/phase13-segmentation-foundation.md`.
- Modify `README.md` and `docs/system-function-module-inventory.md`.

---

### Task 1: Integrate Phase 12 and Establish the Verified Baseline

**Files:**
- Merge: `agent/phase12-production-reliability`
- Verify: `pyproject.toml`
- Verify: `.github/workflows/test.yml`
- Verify: `tests/test_phase12_reliability.py`

**Interfaces:**
- Consumes: Phase 11 at commit `9f6ea03`, Phase 12 at commit `5b6d73d`, approved Phase 13 design at commit `7491537`.
- Produces: `main` containing Phase 12 reliability and the approved Phase 13 design with a reproducible test environment.

- [ ] **Step 1: Confirm a clean working tree and branch topology**

Run:

```powershell
git status --short
git log --left-right --oneline main...agent/phase12-production-reliability
```

Expected: no unstaged source changes; one Phase 13 design commit on the left and one Phase 12 commit on the right.

- [ ] **Step 2: Merge Phase 12 into `main`**

Run:

```powershell
git merge --no-ff agent/phase12-production-reliability -m "Merge Phase 12 production reliability"
```

Expected: merge succeeds without unresolved conflicts.

- [ ] **Step 3: Create the local test environment**

Run:

```powershell
& "C:\Users\BoZeng\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Expected: editable package and `pytest`, `fastapi`, and `httpx` install successfully.

- [ ] **Step 4: Run the Phase 12 and complete regression suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase12_reliability.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

Expected: Phase 12 tests pass; all 189 merged baseline tests pass.

- [ ] **Step 5: Confirm GitHub Actions configuration and source cleanliness**

Run:

```powershell
git diff --check
git status --short
Get-Content -Raw .github/workflows/test.yml
```

Expected: no tracked source changes from verification; workflow installs `.[test]` and runs pytest.

---

### Task 2: Add Versioned Segmentation Run Contracts

**Files:**
- Create: `src/pc_system/segmentation_run.py`
- Create: `tests/test_phase13_segmentation_run.py`

**Interfaces:**
- Consumes: `pc_system.json_io.write_json`.
- Produces: `fingerprint_config(config: dict) -> str`, `build_segmentation_run(...) -> dict`, `write_segmentation_run(run: dict, run_dir: Path) -> Path`, `publish_latest_success(run: dict, run_dir: Path, compatibility_dir: Path) -> Path`.

- [ ] **Step 1: Write failing tests for deterministic fingerprints and lifecycle fields**

```python
from pc_system.segmentation_run import build_segmentation_run, fingerprint_config


def test_config_fingerprint_is_order_independent():
    assert fingerprint_config({"min_points": 10, "distance": 0.2}) == fingerprint_config(
        {"distance": 0.2, "min_points": 10}
    )


def test_build_run_records_requested_and_executed_engine_separately():
    run = build_segmentation_run(
        run_id="seg-run-001",
        asset_id="scan-a",
        asset_version="v1",
        source_uri="data/assets/scan-a/source.las",
        source_point_count=120,
        config={"engine": "open3d_dbscan"},
        requested_engine="open3d_dbscan",
    )
    assert run["status"] == "planned"
    assert run["requested_engine"] == "open3d_dbscan"
    assert run["executed_engine"] is None
    assert run["config_fingerprint"]
```

- [ ] **Step 2: Run tests and confirm the module is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_run.py -q
```

Expected: FAIL with `ModuleNotFoundError: pc_system.segmentation_run`.

- [ ] **Step 3: Implement deterministic run construction**

```python
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pc_system.json_io import write_json


def fingerprint_config(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_segmentation_run(
    *,
    run_id: str,
    asset_id: str,
    asset_version: str,
    source_uri: str,
    source_point_count: int,
    config: dict,
    requested_engine: str,
) -> dict:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "asset_id": asset_id,
        "asset_version": asset_version,
        "source_uri": source_uri,
        "source_point_count": int(source_point_count),
        "config": config,
        "config_fingerprint": fingerprint_config(config),
        "requested_engine": requested_engine,
        "executed_engine": None,
        "fallback_reason": None,
        "status": "planned",
        "started_at": None,
        "completed_at": None,
        "preprocessing": None,
        "artifacts": {},
        "quality": None,
        "error": None,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_segmentation_run(run: dict, run_dir: Path) -> Path:
    return write_json(run, run_dir / "segmentation_run.json")
```

- [ ] **Step 4: Add and test latest-success projection behavior**

Add tests proving `publish_latest_success` rejects non-completed runs and copies only the completed run's object report to `reports/object_segments/<asset_id>/object_segments.json`. Implement it with `shutil.copy2` after validating `run["status"] == "completed"` and the artifact exists.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_run.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the run contract**

```powershell
git add src/pc_system/segmentation_run.py tests/test_phase13_segmentation_run.py
git commit -m "Add versioned segmentation run contract"
```

---

### Task 3: Implement Conservative Segmentation Preprocessing

**Files:**
- Create: `src/pc_system/segmentation_preprocessing.py`
- Create: `tests/test_phase13_segmentation_preprocessing.py`

**Interfaces:**
- Consumes: point records containing finite `x`, `y`, and `z`.
- Produces: `preprocess_points(points: list[dict], config: dict) -> tuple[list[dict], dict]`.

- [ ] **Step 1: Write failing validation and deduplication tests**

```python
import math
import pytest

from pc_system.segmentation_preprocessing import preprocess_points


def test_preprocessing_rejects_non_finite_coordinates():
    with pytest.raises(ValueError, match="finite"):
        preprocess_points([{"x": math.nan, "y": 0, "z": 0}], {})


def test_preprocessing_deduplicates_without_mutating_input():
    source = [{"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}]
    result, report = preprocess_points(source, {"deduplicate": True})
    assert len(source) == 3
    assert len(result) == 2
    assert report["duplicate_points_removed"] == 1
    assert report["retention_ratio"] == pytest.approx(2 / 3, rel=1e-4)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_preprocessing.py -q
```

Expected: FAIL because the preprocessing module does not exist.

- [ ] **Step 3: Implement validation and stable deduplication**

```python
import math


def _validated_copy(point: dict) -> dict:
    if not {"x", "y", "z"}.issubset(point):
        raise ValueError("Point records require x, y, and z.")
    copied = dict(point)
    for key in ("x", "y", "z"):
        copied[key] = float(copied[key])
        if not math.isfinite(copied[key]):
            raise ValueError("Point coordinates must be finite.")
    return copied


def _deduplicate(points: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    result = []
    for point in points:
        key = (point["x"], point["y"], point["z"])
        if key not in seen:
            seen.add(key)
            result.append(point)
    return result, len(points) - len(result)
```

- [ ] **Step 4: Add optional deterministic voxel sampling tests and implementation**

Test that voxel sampling retains the first complete record per voxel, rejects non-positive/non-finite voxel sizes, and reports `voxel_points_removed`. Keep voxel sampling disabled unless `voxel_size` is explicitly present.

Implement voxel keys as:

```python
key = (
    math.floor(point["x"] / voxel_size),
    math.floor(point["y"] / voxel_size),
    math.floor(point["z"] / voxel_size),
)
```

- [ ] **Step 5: Add thin-structure retention warning**

When total retention falls below configurable `min_retention_ratio` (default `0.8`), add:

```python
{
    "code": "low_point_retention",
    "severity": "warning",
    "message": "Preprocessing removed enough points to require thin-structure review.",
}
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_preprocessing.py -q
```

Expected: all preprocessing tests pass.

- [ ] **Step 6: Commit preprocessing**

```powershell
git add src/pc_system/segmentation_preprocessing.py tests/test_phase13_segmentation_preprocessing.py
git commit -m "Add conservative segmentation preprocessing"
```

---

### Task 4: Add Truthful Segmentation Engine Execution

**Files:**
- Create: `src/pc_system/segmentation_engines.py`
- Modify: `src/pc_system/object_segmentation.py`
- Create: `tests/test_phase13_segmentation_engines.py`
- Modify: `tests/test_phase10_object_segmentation.py`

**Interfaces:**
- Consumes: `segment_object_candidates`, optional injected engine runners.
- Produces: `execute_engine(asset_id: str, points: list[dict], config: dict, runners: dict[str, Callable] | None = None) -> tuple[dict, dict]`.

- [ ] **Step 1: Write failing tests for unavailable engines**

```python
import pytest

from pc_system.segmentation_engines import SegmentationEngineUnavailable, execute_engine


def test_open3d_request_fails_when_runner_is_unavailable():
    with pytest.raises(SegmentationEngineUnavailable, match="open3d_dbscan"):
        execute_engine("scan", [], {"engine": "open3d_dbscan", "allow_fallback": False})


def test_explicit_fallback_records_actual_engine_and_reason():
    report, execution = execute_engine(
        "scan",
        [{"x": 0, "y": 0, "z": 0}],
        {"engine": "open3d_dbscan", "allow_fallback": True, "distance_threshold": 1.0, "min_points": 1},
    )
    assert report["method"] == "builtin_geometric"
    assert execution["requested_engine"] == "open3d_dbscan"
    assert execution["executed_engine"] == "builtin_geometric"
    assert execution["fallback_reason"] == "runner_unavailable"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_engines.py -q
```

Expected: FAIL because the engine module does not exist.

- [ ] **Step 3: Implement engine selection**

```python
from collections.abc import Callable

from pc_system.object_segmentation import segment_object_candidates


class SegmentationEngineUnavailable(RuntimeError):
    pass


def execute_engine(asset_id: str, points: list[dict], config: dict, runners=None) -> tuple[dict, dict]:
    requested = str(config.get("engine", "builtin_geometric"))
    runners = runners or {}
    kwargs = {
        "distance_threshold": float(config.get("distance_threshold", 1.0)),
        "min_points": int(config.get("min_points", 10)),
    }
    if requested == "builtin_geometric":
        report = segment_object_candidates(asset_id, points, **kwargs)
        executed, reason = "builtin_geometric", None
    elif requested in runners:
        report = runners[requested](asset_id, points, config)
        executed, reason = requested, None
    elif bool(config.get("allow_fallback", False)):
        report = segment_object_candidates(asset_id, points, **kwargs)
        executed, reason = "builtin_geometric", "runner_unavailable"
    else:
        raise SegmentationEngineUnavailable(f"Segmentation engine is unavailable: {requested}")
    report["method"] = executed
    for item in report.get("objects", []):
        item["method"] = executed
    return report, {
        "requested_engine": requested,
        "executed_engine": executed,
        "fallback_reason": reason,
    }
```

- [ ] **Step 4: Preserve Phase 10 compatibility while removing false labeling**

Change `segment_with_open3d_adapter` so a missing runner raises a clear error instead of silently labeling the builtin result as Open3D. Update the Phase 10 test to inject its fake runner explicitly.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase10_object_segmentation.py tests/test_phase13_segmentation_engines.py -q
```

Expected: all Phase 10 and engine truth tests pass.

- [ ] **Step 5: Commit engine behavior**

```powershell
git add src/pc_system/object_segmentation.py src/pc_system/segmentation_engines.py tests/test_phase10_object_segmentation.py tests/test_phase13_segmentation_engines.py
git commit -m "Record truthful segmentation engine execution"
```

---

### Task 5: Orchestrate Runs and Write Object Membership Artifacts

**Files:**
- Create: `src/pc_system/segmentation_service.py`
- Modify: `src/pc_system/object_segmentation.py`
- Create: `tests/test_phase13_segmentation_service.py`

**Interfaces:**
- Consumes: `preprocess_points`, `execute_engine`, segmentation-run helpers.
- Produces: `run_segmentation(project_root: Path, *, asset_id: str, asset_version: str, source_uri: str, points: list[dict], config: dict, run_id: str, runners: dict | None = None) -> dict`.

- [ ] **Step 1: Expose internal membership indices in engine results**

Write a failing test that each builtin object contains private `_point_indices` matching its input members. Modify `segment_object_candidates` to add:

```python
"_point_indices": list(cluster),
```

Keep `_point_indices` internal; the service removes it before public JSON output.

- [ ] **Step 2: Write a failing end-to-end service test**

```python
def test_completed_run_writes_memberships_and_latest_projection(tmp_path):
    run = run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=[
            {"x": 0, "y": 0, "z": 0},
            {"x": 0.1, "y": 0, "z": 0},
        ],
        config={"engine": "builtin_geometric", "distance_threshold": 0.2, "min_points": 1},
        run_id="seg-run-001",
    )
    run_dir = tmp_path / "reports" / "segmentation_runs" / "scan" / "seg-run-001"
    assert run["status"] == "completed"
    assert (run_dir / "artifacts" / "obj-001.points.json").exists()
    assert (
        tmp_path / "reports" / "object_segments" / "scan" / "object_segments.json"
    ).exists()
```

- [ ] **Step 3: Implement the orchestrator with failure persistence**

The service must:

1. Create a planned run.
2. Mark `running` and persist.
3. Preprocess a copied point list.
4. Execute the selected engine.
5. For each object, write selected points to `artifacts/<object_id>.points.json`.
6. Replace `_point_indices` with `point_membership_artifact`.
7. Write `object_segments.json`.
8. Mark `completed`.
9. Publish the compatibility report.
10. On exception, mark `failed`, persist `error.code`, `error.message`, and re-raise without publishing.

Use stable error codes:

```python
ENGINE_UNAVAILABLE = "engine_unavailable"
INVALID_INPUT = "invalid_input"
SEGMENTATION_FAILED = "segmentation_failed"
```

- [ ] **Step 4: Test failure isolation**

Add a runner that raises `RuntimeError("boom")`. Assert the failed run exists, the last successful compatibility report remains unchanged, and no partial run is marked completed.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_service.py -q
```

Expected: service and failure-isolation tests pass.

- [ ] **Step 5: Commit orchestration and membership artifacts**

```powershell
git add src/pc_system/object_segmentation.py src/pc_system/segmentation_service.py tests/test_phase13_segmentation_service.py
git commit -m "Write versioned segmentation runs and object artifacts"
```

---

### Task 6: Add No-Label Operational Quality Gates

**Files:**
- Create: `src/pc_system/segmentation_operational_quality.py`
- Modify: `src/pc_system/segmentation_service.py`
- Create: `tests/test_phase13_operational_quality.py`

**Interfaces:**
- Consumes: public object report, preprocessing report, execution metadata, thresholds.
- Produces: `build_operational_quality(...) -> dict`.

- [ ] **Step 1: Write failing metric tests**

```python
from pc_system.segmentation_operational_quality import build_operational_quality


def test_quality_flags_fragmentation_and_fallback_without_claiming_accuracy():
    quality = build_operational_quality(
        report={
            "asset_id": "scan",
            "point_count": 100,
            "noise_point_count": 20,
            "objects": [{"point_count": 70}, {"point_count": 5}, {"point_count": 5}],
        },
        preprocessing={"retention_ratio": 0.75, "findings": []},
        execution={"fallback_reason": "runner_unavailable"},
        thresholds={
            "max_noise_ratio": 0.1,
            "max_largest_object_ratio": 0.6,
            "max_tiny_fragment_ratio": 0.2,
            "tiny_object_points": 10,
        },
    )
    assert quality["evaluation_kind"] == "operational_proxy"
    assert quality["status"] == "review_required"
    assert "accuracy" not in quality
    assert {item["code"] for item in quality["findings"]} >= {
        "high_noise_ratio",
        "suspected_under_segmentation",
        "engine_fallback",
    }
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_operational_quality.py -q
```

Expected: FAIL because the quality module does not exist.

- [ ] **Step 3: Implement proxy metrics**

Calculate:

```python
noise_ratio = noise_count / point_count if point_count else 0.0
largest_object_ratio = max(object_sizes, default=0) / point_count if point_count else 0.0
tiny_points = sum(size for size in object_sizes if size < tiny_object_points)
tiny_fragment_ratio = tiny_points / point_count if point_count else 0.0
```

Return:

```python
{
    "schema_version": "1.0",
    "evaluation_kind": "operational_proxy",
    "status": "review_required" if findings else "passed",
    "metrics": {
        "noise_ratio": round(noise_ratio, 4),
        "largest_object_ratio": round(largest_object_ratio, 4),
        "tiny_fragment_ratio": round(tiny_fragment_ratio, 4),
        "retention_ratio": preprocessing["retention_ratio"],
    },
    "findings": findings,
}
```

- [ ] **Step 4: Integrate quality artifacts with the service**

Write `segmentation_quality.json` and `segmentation_quality.md` inside the run directory. Copy the quality summary into `run["quality"]`; a `review_required` quality result still permits the computational run to be `completed`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_operational_quality.py tests/test_phase13_segmentation_service.py -q
```

Expected: all quality and service tests pass.

- [ ] **Step 5: Commit operational quality**

```powershell
git add src/pc_system/segmentation_operational_quality.py src/pc_system/segmentation_service.py tests/test_phase13_operational_quality.py tests/test_phase13_segmentation_service.py
git commit -m "Add operational segmentation quality gates"
```

---

### Task 7: Expose Phase 13A Through CLI, API, Frontend, and Documentation

**Files:**
- Create: `src/pc_system/commands/phase13.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Modify: `src/pc_system/api.py`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Create: `tests/test_phase13_cli_api.py`
- Create: `docs/phase13-segmentation-foundation.md`
- Modify: `README.md`
- Modify: `docs/system-function-module-inventory.md`

**Interfaces:**
- Consumes: `run_segmentation` and run JSON artifacts.
- Produces: `run-segmentation`, run listing/detail/quality APIs, dashboard summary.

- [ ] **Step 1: Write failing CLI test**

Create an asset with a lightweight `.points.json` source, invoke:

```python
exit_code = main([
    "run-segmentation",
    "--project-root", str(project),
    "--asset-id", "scan",
    "--run-id", "seg-run-001",
    "--engine", "builtin_geometric",
    "--distance-threshold", "0.2",
    "--min-points", "1",
])
```

Assert exit code `0` and the run manifest exists.

- [ ] **Step 2: Implement CLI parser and handler**

Add parser arguments:

```python
run_segmentation_parser.add_argument("--project-root", required=True, type=Path)
run_segmentation_parser.add_argument("--asset-id", required=True)
run_segmentation_parser.add_argument("--run-id", required=True)
run_segmentation_parser.add_argument("--engine", default="builtin_geometric")
run_segmentation_parser.add_argument("--allow-fallback", action="store_true")
run_segmentation_parser.add_argument("--distance-threshold", default=1.0, type=float)
run_segmentation_parser.add_argument("--min-points", default=10, type=int)
run_segmentation_parser.add_argument("--voxel-size", type=float)
```

The handler loads asset metadata, samples its source with the existing sampling adapter, calls `run_segmentation`, prints the run path, and returns `2` for invalid input or unavailable engines.

- [ ] **Step 3: Write failing API tests**

Test:

```text
GET /segmentation-runs/scan
GET /segmentation-runs/scan/seg-run-001
GET /segmentation-runs/scan/seg-run-001/quality
```

Expected responses: list ordered by run directory name, run manifest JSON, quality JSON; invalid identifiers return 400; missing artifacts return 404.

- [ ] **Step 4: Implement read-only API routes**

Use the Phase 12 identifier validator before creating paths. Do not add write routes in Phase 13A.

- [ ] **Step 5: Add frontend contract tests and summary**

Add a compact dashboard section showing:

- latest `run_id`
- `executed_engine`
- run `status`
- quality `status`
- first three finding codes

The frontend must display “运行质量代理指标” and must not label these values as “准确率”.

- [ ] **Step 6: Add Phase 13 documentation**

Document:

- directory layout
- CLI and API examples
- requested versus executed engine semantics
- conservative preprocessing
- operational proxy limitation
- Phase 13B golden-label boundary

- [ ] **Step 7: Run focused and complete verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13_segmentation_run.py tests/test_phase13_segmentation_preprocessing.py tests/test_phase13_segmentation_engines.py tests/test_phase13_segmentation_service.py tests/test_phase13_operational_quality.py tests/test_phase13_cli_api.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
git diff --check
git status --short
```

Expected: all Phase 13A tests and all repository tests pass; no whitespace errors.

- [ ] **Step 8: Commit Phase 13A public surfaces**

```powershell
git add src/pc_system/commands/phase13.py src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/api.py frontend/index.html frontend/app.js frontend/app.css tests/test_phase13_cli_api.py docs/phase13-segmentation-foundation.md README.md docs/system-function-module-inventory.md
git commit -m "Expose Phase 13A segmentation workflow"
```

---

## Final Verification Gate

- [ ] Run the complete test suite from the local `.venv`.
- [ ] Confirm the GitHub Actions workflow exists and is configured for Python 3.11.
- [ ] Confirm a failed run cannot replace the latest successful compatibility report.
- [ ] Confirm every public object has a membership artifact and no private `_point_indices`.
- [ ] Confirm fallback audit data names the actual executed engine.
- [ ] Confirm the UI says “operational proxy” rather than “accuracy”.
- [ ] Confirm raw source point-cloud files are unchanged.
- [ ] Confirm `git diff --check` and `git status --short` are clean.
- [ ] Push verified `main` only after the full gate passes.
