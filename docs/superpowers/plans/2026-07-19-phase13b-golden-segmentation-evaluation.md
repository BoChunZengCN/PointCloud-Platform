# Phase 13B Golden Segmentation Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add versioned point and 3D-box golden labels, measurable segmentation metrics, regression gates, and bounded deterministic parameter search on top of Phase 13A.

**Architecture:** Keep benchmark import, point correspondence, metric calculation, evaluation orchestration, regression comparison, and search in separate pure-Python modules. Phase 13A gains only the source provenance needed by evaluators. Every evaluation, comparison, and search writes an immutable run directory; recommendations are advisory and never mutate production configuration.

**Tech Stack:** Python 3.11+, standard library, pytest, FastAPI, vanilla HTML/CSS/JavaScript.

## Global Constraints

- Support both point-level instance labels and oriented 3D bounding-box labels.
- Default correspondence is strict `source_fingerprint + point_index`; coordinate tolerance matching requires an explicit mode.
- Operational proxy metrics and golden-label accuracy must remain visibly and semantically separate.
- Built-in box fallback is labeled `axis_aligned_envelope`, never oriented IoU.
- Search strategies are deterministic grid search and fixed-seed random search without replacement.
- Search budgets are finite and mandatory.
- Search recommendations never modify production segmentation configuration.
- External identifiers use `validate_identifier` before path construction.
- Existing run directories are never silently overwritten.
- All code changes follow red-green TDD and preserve the full existing test suite.

---

### Task 1: Preserve Phase 13A Source Provenance

**Files:**
- Create: `src/pc_system/segmentation_provenance.py`
- Modify: `src/pc_system/segmentation_service.py`
- Modify: `src/pc_system/commands/phase13.py`
- Test: `tests/test_phase13b_provenance.py`

**Interfaces:**
- Produces: `fingerprint_points(points: list[dict]) -> str`
- Produces: membership artifacts containing `source_point_indices`
- Produces: `segmentation_run.json["source_fingerprint"]`
- Consumes: existing `run_segmentation` and Phase 13A membership artifacts

- [ ] **Step 1: Write failing provenance tests**

```python
from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_service import run_segmentation


def test_point_fingerprint_is_deterministic_and_order_sensitive():
    points = [{"x": 0, "y": 1, "z": 2}, {"x": 3, "y": 4, "z": 5}]
    assert fingerprint_points(points) == fingerprint_points([dict(item) for item in points])
    assert fingerprint_points(points) != fingerprint_points(list(reversed(points)))


def test_run_records_source_fingerprint_and_membership_indices(tmp_path):
    points = [{"x": 0, "y": 0, "z": 0}, {"x": 0.1, "y": 0, "z": 0}]
    run = run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=points,
        config={"engine": "builtin_geometric", "distance_threshold": 0.2, "min_points": 1},
        run_id="run-001",
    )
    artifact = tmp_path / "reports" / "segmentation_runs" / "scan" / "run-001" / run["artifacts"]["memberships"]["obj-001"]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert run["source_fingerprint"] == fingerprint_points(points)
    assert payload["source_point_indices"] == [0, 1]
    assert all("_source_index" not in point for point in payload["points"])
```

- [ ] **Step 2: Run tests and confirm the missing module/fields**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_provenance.py -q --basetemp .pytest-tmp-p13b-1-red
```

Expected: FAIL because `pc_system.segmentation_provenance` does not exist.

- [ ] **Step 3: Implement canonical point fingerprints**

```python
# src/pc_system/segmentation_provenance.py
import hashlib
import json
import math


def fingerprint_points(points: list[dict]) -> str:
    canonical = []
    for point in points:
        xyz = [float(point[axis]) for axis in ("x", "y", "z")]
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError("Point coordinates must be finite before fingerprinting.")
        canonical.append(xyz)
    payload = json.dumps(canonical, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Add source indices without leaking private fields**

Before preprocessing, copy input records and add `_source_index`. In `_write_membership_artifacts`, write `source_point_indices`, strip `_source_index` from public points, and validate every index. Add `source_fingerprint` to the run manifest before the first write.

```python
indexed_points = [dict(point, _source_index=index) for index, point in enumerate(points)]
processed_points, preprocessing = preprocess_points(indexed_points, config)

source_indices = [int(points[index]["_source_index"]) for index in indices]
public_points = [
    {key: value for key, value in points[index].items() if key != "_source_index"}
    for index in indices
]
```

Add `max_points` to the Phase 13 CLI config so later coordinate matching can reproduce source sampling.

- [ ] **Step 5: Run focused and compatibility tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_provenance.py tests/test_phase13_segmentation_service.py tests/test_phase13_cli_api.py -q --basetemp .pytest-tmp-p13b-1-green
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/segmentation_provenance.py src/pc_system/segmentation_service.py src/pc_system/commands/phase13.py tests/test_phase13b_provenance.py
git commit -m "Add segmentation source provenance"
```

---

### Task 2: Import and Validate Versioned Benchmarks

**Files:**
- Create: `src/pc_system/segmentation_benchmarks.py`
- Test: `tests/test_phase13b_benchmarks.py`

**Interfaces:**
- Produces: `BenchmarkValidationError(code: str, message: str)`
- Produces: `load_label_document(path: Path, labels_format: str) -> dict`
- Produces: `import_benchmark(project_root: Path, manifest_path: Path) -> dict`
- Produces: `load_benchmark_sample(project_root: Path, benchmark_id: str, sample_id: str) -> tuple[dict, dict]`

- [ ] **Step 1: Write failing JSON/JSONL and validation tests**

Cover:

```python
def test_imports_json_benchmark_into_versioned_workspace(tmp_path): ...
def test_imports_jsonl_point_and_box_records(tmp_path): ...
def test_rejects_duplicate_point_indices(tmp_path): ...
def test_rejects_invalid_box_size_or_quaternion(tmp_path): ...
def test_rejects_labels_path_outside_manifest_directory(tmp_path): ...
def test_refuses_to_overwrite_existing_benchmark(tmp_path): ...
```

Use a manifest with `schema_version="1.0"`, one sample, point labels for `pipe-1`, and one box with identity quaternion `[0, 0, 0, 1]`.

- [ ] **Step 2: Run tests and confirm import symbols are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_benchmarks.py -q --basetemp .pytest-tmp-p13b-2-red
```

Expected: FAIL on missing `segmentation_benchmarks`.

- [ ] **Step 3: Implement format parsing and stable errors**

```python
class BenchmarkValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_label_document(path: Path, labels_format: str) -> dict:
    if labels_format == "json":
        document = json.loads(path.read_text(encoding="utf-8"))
        return {
            "schema_version": document.get("schema_version", "1.0"),
            "point_labels": list(document.get("point_labels", [])),
            "boxes": list(document.get("boxes", [])),
        }
    if labels_format == "jsonl":
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {
            "schema_version": "1.0",
            "point_labels": [item for item in records if item.get("record_type") == "point_label"],
            "boxes": [item for item in records if item.get("record_type") == "box"],
        }
    raise BenchmarkValidationError("unsupported_labels_format", labels_format)
```

- [ ] **Step 4: Implement semantic and path validation**

Validate identifiers with `validate_identifier`, resolve `labels_path` relative to the manifest directory, and require the resolved path to remain under that directory. Validate finite coordinates, non-negative indices, unique indices, positive box sizes, finite four-number quaternions with non-zero norm, and unique box `instance_id` values.

When a sample contains boxes, every non-noise point-label `instance_id` must reference one of those boxes. Point-only and box-only fixtures remain valid so the two annotation forms can also be evaluated independently.

Normalize imported data into:

```text
<project_root>/benchmarks/<benchmark_id>/benchmark.json
<project_root>/benchmarks/<benchmark_id>/samples/<sample_id>/labels.json
```

- [ ] **Step 5: Run benchmark tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_benchmarks.py tests/test_phase12_reliability.py -q --basetemp .pytest-tmp-p13b-2-green
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/segmentation_benchmarks.py tests/test_phase13b_benchmarks.py
git commit -m "Add versioned segmentation benchmarks"
```

---

### Task 3: Resolve Strict and Coordinate Point Correspondence

**Files:**
- Create: `src/pc_system/segmentation_correspondence.py`
- Test: `tests/test_phase13b_correspondence.py`

**Interfaces:**
- Consumes: normalized point labels and `fingerprint_points`
- Produces: `CorrespondenceError(code: str, message: str)`
- Produces: `match_point_labels(labels, source_points, *, expected_fingerprint, mode="strict_index", tolerance=None, min_coverage=1.0) -> tuple[list[dict], dict]`

- [ ] **Step 1: Write failing correspondence tests**

```python
def test_strict_mode_matches_indices_when_fingerprint_matches(): ...
def test_strict_mode_rejects_fingerprint_mismatch(): ...
def test_strict_mode_rejects_out_of_range_index(): ...
def test_coordinate_mode_matches_unique_points_within_tolerance(): ...
def test_coordinate_mode_reports_unmatched_and_ambiguous_points(): ...
def test_coordinate_mode_fails_below_minimum_coverage(): ...
def test_coordinate_mode_is_deterministic(): ...
```

Assert stable error codes: `source_fingerprint_mismatch`, `point_index_out_of_range`, `invalid_tolerance`, and `insufficient_match_coverage`.

- [ ] **Step 2: Run tests and verify the missing module failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_correspondence.py -q --basetemp .pytest-tmp-p13b-3-red
```

Expected: FAIL on missing module.

- [ ] **Step 3: Implement strict index matching**

```python
if mode == "strict_index":
    actual = fingerprint_points(source_points)
    if actual != expected_fingerprint:
        raise CorrespondenceError("source_fingerprint_mismatch", "...")
    matched = []
    for label in labels:
        index = int(label["point_index"])
        if index < 0 or index >= len(source_points):
            raise CorrespondenceError("point_index_out_of_range", "...")
        matched.append({**label, "source_point_index": index})
```

- [ ] **Step 4: Implement explicit coordinate tolerance matching**

For each labeled XYZ, calculate squared distance to every source point. Match only when exactly one candidate is within tolerance. Classify zero candidates as unmatched and multiple nearest candidates at the same distance as ambiguous. Sort results by input label order and never silently choose an ambiguous point.

Return:

```python
{
    "schema_version": "1.0",
    "mode": mode,
    "label_count": len(labels),
    "matched_count": len(matched),
    "matched_ratio": len(matched) / len(labels) if labels else 1.0,
    "unmatched_count": unmatched_count,
    "ambiguous_count": ambiguous_count,
    "tolerance": tolerance,
}
```

- [ ] **Step 5: Run correspondence tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_correspondence.py -q --basetemp .pytest-tmp-p13b-3-green
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/segmentation_correspondence.py tests/test_phase13b_correspondence.py
git commit -m "Add golden point correspondence"
```

---

### Task 4: Compute Point, Instance, and Box Metrics

**Files:**
- Create: `src/pc_system/segmentation_metrics.py`
- Test: `tests/test_phase13b_metrics.py`

**Interfaces:**
- Produces: `associate_instances(golden: dict[int, str], predicted: dict[int, str], threshold: float) -> dict`
- Produces: `build_point_metrics(golden: dict[int, dict], predicted: dict[int, dict], matches: dict) -> dict`
- Produces: `build_instance_metrics(golden: dict[int, str], predicted: dict[int, str], iou_threshold: float = 0.5) -> dict`
- Produces: `build_bbox_metrics(golden_boxes: list[dict], predicted_objects: list[dict], instance_matches: dict, *, iou_threshold=0.5, runner=None) -> dict`

- [ ] **Step 1: Write failing known-answer metric tests**

Create small point sets whose exact answers are obvious:

```python
def test_perfect_point_and_instance_metrics_equal_one(): ...
def test_instance_metrics_count_false_positive_and_false_negative(): ...
def test_instance_metrics_detect_over_segmentation(): ...
def test_instance_metrics_detect_under_segmentation(): ...
def test_point_metrics_report_noise_precision_recall_and_f1(): ...
def test_axis_aligned_box_iou_known_overlap(): ...
def test_rotated_box_fallback_is_truthfully_labeled(): ...
def test_injected_oriented_runner_is_recorded_as_executed(): ...
```

For boxes `[0,0,0]-[2,2,2]` and `[1,0,0]-[3,2,2]`, assert IoU `1/3`.

- [ ] **Step 2: Run tests and confirm metric functions are absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_metrics.py -q --basetemp .pytest-tmp-p13b-4-red
```

Expected: FAIL on missing module.

- [ ] **Step 3: Implement deterministic instance association**

Build intersection and union counts for every golden/predicted pair, sort candidates by descending IoU and then IDs, and greedily accept one-to-one matches at the threshold.

Return matched pairs, unmatched IDs, pair IoUs, and overlap maps used for over/under-segmentation findings.

- [ ] **Step 4: Implement point and instance metrics**

Use safe division returning `0.0` for empty denominators. Compute:

```python
precision = tp / (tp + fp) if tp + fp else 0.0
recall = tp / (tp + fn) if tp + fn else 0.0
f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
```

Remap matched predicted instance IDs to golden instance IDs before point IoU. Keep unmatched predictions as distinct false-positive labels. Report per-instance IoU, macro point mIoU, labeled-point accuracy, and noise metrics.

- [ ] **Step 5: Implement truthful box metrics**

Implement exact AABB intersection/union from `bounds.min/max`. Convert golden oriented boxes to an axis-aligned envelope by rotating all eight local corners with the normalized quaternion. Label the built-in engine `axis_aligned_envelope` and set `fallback_reason="oriented_runner_unavailable"` when no oriented runner is injected.

- [ ] **Step 6: Run metric tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_metrics.py -q --basetemp .pytest-tmp-p13b-4-green
```

Expected: all tests PASS with exact or `pytest.approx` metric assertions.

- [ ] **Step 7: Commit**

```powershell
git add src/pc_system/segmentation_metrics.py tests/test_phase13b_metrics.py
git commit -m "Add golden segmentation metrics"
```

---

### Task 5: Orchestrate Versioned Evaluation Runs

**Files:**
- Create: `src/pc_system/segmentation_evaluation.py`
- Test: `tests/test_phase13b_evaluation.py`

**Interfaces:**
- Consumes: Phase 13A run/artifacts, imported benchmark samples, correspondence, metrics
- Produces: `evaluate_segmentation_run(project_root: Path, *, asset_id: str, run_id: str, benchmark_id: str, sample_id: str, evaluation_id: str, config: dict, box_runner=None) -> dict`
- Produces: evaluation artifacts under `reports/segmentation_evaluations/<asset_id>/<evaluation_id>/`

- [ ] **Step 1: Write failing evaluation lifecycle tests**

```python
def test_completed_evaluation_writes_all_metric_artifacts(tmp_path): ...
def test_evaluation_uses_public_source_point_indices(tmp_path): ...
def test_evaluation_failure_is_persisted_without_summary(tmp_path): ...
def test_evaluation_refuses_existing_evaluation_id(tmp_path): ...
def test_operational_proxy_and_accuracy_remain_separate(tmp_path): ...
```

Build a real Phase 13A run in the fixture, import a matching benchmark, and evaluate it.

- [ ] **Step 2: Run tests and confirm orchestration is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_evaluation.py -q --basetemp .pytest-tmp-p13b-5-red
```

Expected: FAIL on missing module.

- [ ] **Step 3: Implement predicted assignment loading**

Read `object_segments.json` and each `point_membership_artifact`. Construct:

```python
predicted_by_index[source_index] = {
    "instance_id": object_id,
    "class_id": object_item.get("class_id", object_item.get("label", "object_candidate")),
    "is_noise": False,
}
```

Indices not present in any object membership are predicted noise.

- [ ] **Step 4: Implement evaluation lifecycle and artifacts**

Write `evaluation_run.json` with `planned`, `running`, `completed`, or `failed` status. Load source points from `source_uri` with the recorded `max_points`, validate the fingerprint, run correspondence and metrics, and atomically write:

```text
correspondence.json
point_metrics.json
instance_metrics.json
bbox_metrics.json
evaluation_summary.json
```

The summary must use `evaluation_kind="golden_labels"` and must not copy Phase 13A operational proxy fields as accuracy.

- [ ] **Step 5: Run evaluation and Phase 13A compatibility tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_evaluation.py tests/test_phase13_segmentation_service.py tests/test_phase13_cli_api.py -q --basetemp .pytest-tmp-p13b-5-green
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/segmentation_evaluation.py tests/test_phase13b_evaluation.py
git commit -m "Add versioned golden evaluations"
```

---

### Task 6: Compare Baselines and Enforce Golden Regression Gates

**Files:**
- Create: `src/pc_system/segmentation_regression.py`
- Test: `tests/test_phase13b_regression.py`

**Interfaces:**
- Produces: `build_regression_comparison(baseline: dict, candidate: dict, thresholds: dict) -> tuple[dict, dict]`
- Produces: `compare_evaluations(project_root: Path, *, asset_id: str, comparison_id: str, baseline_evaluation_id: str, candidate_evaluation_id: str, thresholds: dict) -> dict`
- Produces: `reports/segmentation_comparisons/<asset_id>/<comparison_id>/comparison.json`
- Produces: `reports/segmentation_comparisons/<asset_id>/<comparison_id>/regression_gate.json`

- [ ] **Step 1: Write failing comparison and gate tests**

```python
def test_candidate_improvement_passes_gate(): ...
def test_metric_drop_beyond_tolerance_fails_gate(): ...
def test_increased_split_and_merge_counts_fail_gate(): ...
def test_thresholds_are_recorded_in_gate(): ...
def test_comparison_reports_available_scene_and_class_deltas(): ...
def test_comparison_requires_completed_evaluations(tmp_path): ...
def test_comparison_does_not_modify_baseline(tmp_path): ...
```

- [ ] **Step 2: Run tests and confirm comparison functions are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_regression.py -q --basetemp .pytest-tmp-p13b-6-red
```

Expected: FAIL on missing module.

- [ ] **Step 3: Implement metric deltas and findings**

Compare `point_miou`, `instance_f1`, `mean_box_iou`, `noise_f1`, `over_segmentation_count`, and `under_segmentation_count`. For higher-is-better metrics, fail when:

```python
candidate_value - baseline_value < -allowed_drop
```

For count metrics, fail when:

```python
candidate_value - baseline_value > allowed_increase
```

When both summaries contain matching `by_scene` or `by_class` rows, apply the same delta rules and include those scoped deltas in `comparison.json`. Missing optional scopes do not invent zero-valued metrics.

- [ ] **Step 4: Persist immutable comparison artifacts**

Validate all IDs, refuse an existing comparison directory, require both evaluations to be completed, and write the comparison and gate without modifying either source evaluation.

- [ ] **Step 5: Run regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_regression.py -q --basetemp .pytest-tmp-p13b-6-green
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/segmentation_regression.py tests/test_phase13b_regression.py
git commit -m "Add segmentation regression gates"
```

---

### Task 7: Add Bounded Deterministic Parameter Search

**Files:**
- Create: `src/pc_system/segmentation_search.py`
- Test: `tests/test_phase13b_search.py`

**Interfaces:**
- Produces: `generate_trial_configs(parameter_space: dict, *, strategy: str, max_trials: int, seed: int) -> list[dict]`
- Produces: `score_evaluation(summary: dict, weights: dict) -> float`
- Produces: `run_parameter_search(project_root: Path, *, asset_id: str, search_id: str, search_config: dict, trial_runner) -> dict`
- `trial_runner(trial_id: str, config: dict) -> dict` returns `evaluation_id`, `summary`, `gate_status`, and `runtime_seconds`

- [ ] **Step 1: Write failing search generation and lifecycle tests**

```python
def test_grid_search_is_lexicographic_and_bounded(): ...
def test_random_search_is_repeatable_for_fixed_seed(): ...
def test_random_search_has_no_duplicate_trials(): ...
def test_search_rejects_missing_or_nonpositive_budget(): ...
def test_search_records_per_trial_timeout_metadata(): ...
def test_composite_score_uses_metrics_and_penalties(): ...
def test_failed_trial_is_retained_and_later_trials_continue(tmp_path): ...
def test_recommendation_excludes_failed_regression_gate(tmp_path): ...
def test_search_never_mutates_production_config(tmp_path): ...
```

- [ ] **Step 2: Run tests and confirm search functions are absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_search.py -q --basetemp .pytest-tmp-p13b-7-red
```

Expected: FAIL on missing module.

- [ ] **Step 3: Implement deterministic trial generation**

Sort parameter names and values before Cartesian product generation. Grid search takes the first `max_trials`. Random search uses `random.Random(seed).sample` over the deterministic grid.

Reject empty parameter spaces, unsupported strategies, and `max_trials <= 0`.

- [ ] **Step 4: Implement composite scoring**

Use explicit weights:

```python
score = (
    weights["instance_f1"] * summary["instance_f1"]
    + weights["point_miou"] * summary["point_miou"]
    + weights["mean_box_iou"] * summary["mean_box_iou"]
    + weights["noise_f1"] * summary["noise_f1"]
    - weights["over_segmentation"] * summary["over_segmentation_count"]
    - weights["under_segmentation"] * summary["under_segmentation_count"]
    - weights["runtime_seconds"] * summary["runtime_seconds"]
)
```

- [ ] **Step 5: Implement immutable trial and recommendation records**

Write `search_run.json`, one `trials/trial-XXXX.json` per attempted configuration, and `recommendation.json`. Catch each trial exception, persist `status="failed"`, and continue. Recommend only completed trials whose `gate_status` is `passed`. Resolve ties by runtime then configuration fingerprint.

Require `trial_timeout_seconds > 0` in the search configuration and copy it into the search and trial manifests. The first implementation records the limit for an injected runner to enforce; it does not pretend to terminate an in-process Python call.

- [ ] **Step 6: Run search tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_search.py -q --basetemp .pytest-tmp-p13b-7-green
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/pc_system/segmentation_search.py tests/test_phase13b_search.py
git commit -m "Add deterministic segmentation search"
```

---

### Task 8: Expose Phase 13B Through CLI and Read-Only API

**Files:**
- Create: `src/pc_system/commands/phase13b.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Modify: `src/pc_system/api.py`
- Test: `tests/test_phase13b_cli_api.py`

**Interfaces:**
- Consumes: benchmark import, evaluation, regression, and search modules
- Produces CLI commands: `import-segmentation-benchmark`, `evaluate-segmentation-run`, `compare-segmentation-runs`, `search-segmentation-params`
- Produces read-only benchmark/evaluation/comparison/search routes

- [ ] **Step 1: Write failing CLI and API contract tests**

Cover:

```python
def test_import_benchmark_cli_writes_normalized_manifest(tmp_path): ...
def test_evaluate_run_cli_writes_golden_summary(tmp_path): ...
def test_compare_cli_writes_regression_gate(tmp_path): ...
def test_search_cli_uses_bounded_config_file(tmp_path): ...
def test_api_lists_benchmarks_and_reads_detail(tmp_path): ...
def test_api_reads_evaluation_comparison_and_search(tmp_path): ...
def test_api_validates_all_phase13b_identifiers(tmp_path): ...
```

- [ ] **Step 2: Run tests and confirm commands/routes are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_cli_api.py -q --basetemp .pytest-tmp-p13b-8-red
```

Expected: FAIL because parser commands and API routes do not exist.

- [ ] **Step 3: Register CLI arguments and dispatch**

Use JSON config files for evaluation thresholds and search spaces. Validate `benchmark_id`, `sample_id`, `evaluation_id`, `comparison_id`, and `search_id` alongside existing IDs before command dispatch.

The search command builds a trial runner that:

1. loads the asset source,
2. calls `run_segmentation` with the trial config,
3. calls `evaluate_segmentation_run`,
4. optionally compares against a baseline,
5. returns a compact summary to `run_parameter_search`.

- [ ] **Step 4: Add read-only API routes**

Add routes for:

```text
GET /segmentation-benchmarks
GET /segmentation-benchmarks/{benchmark_id}
GET /segmentation-evaluations/{asset_id}
GET /segmentation-evaluations/{asset_id}/{evaluation_id}
GET /segmentation-comparisons/{asset_id}/{comparison_id}
GET /segmentation-searches/{asset_id}
GET /segmentation-searches/{asset_id}/{search_id}
GET /segmentation-searches/{asset_id}/{search_id}/recommendation
```

Use `_validate_api_identifier` before constructing every path.

- [ ] **Step 5: Run CLI/API and security regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_cli_api.py tests/test_phase12_reliability.py tests/test_api.py -q --basetemp .pytest-tmp-p13b-8-green
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/commands/phase13b.py src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/api.py tests/test_phase13b_cli_api.py
git commit -m "Expose Phase 13B evaluation APIs"
```

---

### Task 9: Add Golden Evaluation Dashboard and Documentation

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/app.css`
- Create: `docs/phase13b-golden-segmentation-evaluation.md`
- Modify: `README.md`
- Modify: `docs/system-function-module-inventory.md`
- Test: `tests/test_phase13b_frontend_docs.py`

**Interfaces:**
- Consumes: latest evaluation and search read-only API payloads
- Produces: dashboard golden-evaluation panel and Phase 13B operating guide

- [ ] **Step 1: Write failing frontend/documentation contract tests**

```python
def test_frontend_has_golden_evaluation_panel_and_fetchers(): ...
def test_frontend_distinguishes_accuracy_from_operational_proxy(): ...
def test_phase13b_documentation_describes_formats_metrics_and_search_limits(): ...
def test_inventory_marks_phase13b_modules_complete(): ...
```

Require the Chinese labels `黄金标注准确率`, `实例 F1`, `包围盒 IoU`, `回归门禁`, and `推荐参数`.

- [ ] **Step 2: Run tests and confirm public surfaces are absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_frontend_docs.py -q --basetemp .pytest-tmp-p13b-9-red
```

Expected: FAIL because the panel and documentation do not exist.

- [ ] **Step 3: Add a compact read-only dashboard panel**

Render:

- matched label coverage
- point mIoU
- instance F1
- mean box IoU
- regression gate status
- best search score
- recommended parameters

Empty states must explain which CLI command creates the missing artifact. Do not add browser label editing; that remains Phase 14.

- [ ] **Step 4: Write operating documentation**

Document:

- benchmark JSON and JSONL examples
- strict and coordinate correspondence
- metric definitions
- comparison thresholds
- bounded search configuration
- artifact directory layout
- CLI and API examples
- accuracy versus operational proxy terminology
- Phase 14 and Phase 15 boundaries

- [ ] **Step 5: Run frontend syntax and focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_frontend_docs.py tests/test_frontend_workbench.py tests/test_phase13_cli_api.py -q --basetemp .pytest-tmp-p13b-9-green
& 'C:\Users\BoZeng\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check frontend\app.js
```

Expected: all tests PASS and Node exits `0`.

- [ ] **Step 6: Commit**

```powershell
git add frontend/index.html frontend/app.js frontend/app.css docs/phase13b-golden-segmentation-evaluation.md README.md docs/system-function-module-inventory.md tests/test_phase13b_frontend_docs.py
git commit -m "Document Phase 13B evaluation workflow"
```

---

## Final Verification Gate

- [ ] **Step 1: Run all Phase 13B tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase13b_*.py -q --basetemp .pytest-tmp-phase13b-focused
```

Expected: all Phase 13B tests PASS.

- [ ] **Step 2: Run the full repository suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-phase13b-full
```

Expected: all tests PASS with no failures.

- [ ] **Step 3: Run syntax, compile, and diff checks**

```powershell
& 'C:\Users\BoZeng\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check frontend\app.js
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
git status -sb
```

Expected: Node and compileall exit `0`, `git diff --check` prints nothing, and the feature worktree is clean after commits.

## Execution Notes

- Use a worktree at `.worktrees/phase13b-golden-evaluation` on branch `codex/phase13b-golden-evaluation`.
- Use `--basetemp` inside that worktree because the Windows sandbox cannot use the global pytest temporary directory.
- Remove each task's temporary pytest directory after its green run.
- Do not merge or push until the complete final verification gate passes.
