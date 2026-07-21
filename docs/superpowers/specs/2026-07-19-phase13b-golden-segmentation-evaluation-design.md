# Phase 13B Golden Segmentation Evaluation Design

## 1. Goal

Phase 13B adds versioned golden labels, measurable segmentation accuracy, baseline comparison, regression gates, and bounded automatic parameter search to the Phase 13A segmentation workflow.

The system must support both point-level instance labels and oriented 3D bounding-box labels. It must distinguish measured accuracy from Phase 13A operational proxy quality, preserve every evaluation and search trial, and never change production segmentation configuration automatically.

## 2. Scope

Phase 13B includes:

- Versioned JSON and JSONL benchmark import.
- Point-level instance labels and oriented 3D bounding-box labels.
- Strict point matching by source fingerprint and point index.
- Explicit coordinate-tolerance matching with coverage and ambiguity reporting.
- Point, instance, and bounding-box accuracy metrics.
- Over-segmentation and under-segmentation findings.
- Candidate-versus-baseline comparison.
- Golden regression gates.
- Deterministic grid search and fixed-seed random search.
- CLI commands, read-only API routes, a compact frontend summary, and documentation.

Phase 13B excludes:

- Browser-based annotation or correction tools. Those belong to Phase 14.
- Import adapters for CVAT, Label Cloud, or vendor-specific formats.
- Bayesian optimization or distributed search orchestration.
- Automatic promotion of recommended parameters into production.
- Model-library retrieval and geometric registration. Those belong to Phase 15.

## 3. Architecture

Phase 13B is an offline evaluation subsystem around the existing Phase 13A run artifacts.

```text
benchmark import
  -> schema and semantic validation
  -> point correspondence
  -> metric evaluation
  -> candidate/baseline comparison
  -> golden regression gate
  -> bounded parameter search recommendation
```

The subsystem is split into focused modules:

1. `segmentation_benchmarks`: imports and validates benchmark manifests and label files.
2. `segmentation_correspondence`: aligns golden point labels with evaluated point records.
3. `segmentation_metrics`: computes point, instance, and box metrics.
4. `segmentation_evaluation`: orchestrates evaluation runs and writes immutable artifacts.
5. `segmentation_regression`: compares candidate metrics with a baseline and builds a gate.
6. `segmentation_search`: runs deterministic grid or fixed-seed random trials and ranks them.

Evaluation code consumes public Phase 13A artifacts. It does not reach into private in-memory membership indices.

## 4. Benchmark Contract

Benchmarks live under:

```text
benchmarks/<benchmark_id>/
  benchmark.json
  samples/<sample_id>/
    labels.json
    labels.jsonl
```

`benchmark.json` uses schema version `1.0` and records:

- `benchmark_id`
- `benchmark_version`
- `split`: `development`, `validation`, or `golden_regression`
- `scene_type`
- `point_density`
- `coordinate_unit`
- `label_version`
- `license`
- `samples`

Each sample records:

- `sample_id`
- `asset_id`
- `asset_version`
- `source_uri`
- `source_fingerprint`
- `labels_path`
- `labels_format`: `json` or `jsonl`

JSON label files contain a document with `point_labels` and `boxes`. JSONL files contain one record per line with a `record_type` of `point_label` or `box`.

A point label records:

- `point_index`
- optional `x`, `y`, `z`
- `instance_id`
- `class_id`
- `is_noise`

An oriented box records:

- `instance_id`
- `class_id`
- `center`: three finite numbers
- `size`: three finite positive numbers
- `rotation`: quaternion `[x, y, z, w]`
- optional `notes`

Unknown fields are preserved where practical, but required fields and numeric constraints are strict. Duplicate point indices, duplicate instance boxes, missing instance references, invalid quaternions, path traversal, and unsupported schema versions are rejected with stable error codes.

Import copies normalized benchmark data into the project workspace. Source files remain unchanged.

## 5. Point Correspondence

Two correspondence modes are supported.

### 5.1 Strict index mode

Strict mode is the default:

- The sample `source_fingerprint` must match the evaluated source.
- Every labeled `point_index` must be within bounds.
- Each index maps to exactly one evaluated source point.
- A fingerprint mismatch fails the evaluation.

### 5.2 Coordinate tolerance mode

Coordinate mode must be explicitly requested:

- Matching uses finite XYZ coordinates and a configured positive tolerance.
- A point with exactly one candidate inside the tolerance is matched.
- No candidates produce an unmatched point.
- Multiple equally valid candidates produce an ambiguous point.
- Matching is deterministic and never silently chooses an ambiguous point.

The correspondence report records:

- label count
- matched count and ratio
- unmatched count
- ambiguous count
- tolerance
- mode

Evaluation fails when matched coverage is below the configured minimum. It must not report accuracy from an invalid correspondence.

## 6. Metrics

### 6.1 Point metrics

Point metrics use matched golden labels:

- Per-class and per-instance IoU.
- Macro point mIoU.
- Labeled-point accuracy.
- Noise precision, recall, and F1.
- Label coverage and correspondence quality.

### 6.2 Instance metrics

Predicted and golden instances are associated using point-set IoU. Matching is one-to-one and deterministic at a configured IoU threshold.

The report includes:

- instance precision, recall, and F1
- true positives, false positives, and false negatives
- mean matched-instance IoU
- over-segmentation findings
- under-segmentation findings

One golden instance associated with multiple predicted fragments is over-segmentation. Multiple golden instances substantially overlapping one predicted instance is under-segmentation.

### 6.3 Bounding-box metrics

Bounding boxes are evaluated by oriented 3D IoU when a geometry backend is available. The first-party fallback supports axis-aligned boxes and must record the actual metric engine used. It must not label an axis-aligned approximation as oriented IoU.

The report includes:

- per-instance box IoU
- mean box IoU
- box precision, recall, and F1 at the configured IoU threshold
- missing and extra boxes
- requested and executed box metric engines
- fallback reason

## 7. Evaluation Runs

Evaluation artifacts live under:

```text
reports/segmentation_evaluations/<asset_id>/<evaluation_id>/
  evaluation_run.json
  correspondence.json
  point_metrics.json
  instance_metrics.json
  bbox_metrics.json
  evaluation_summary.json
```

The run manifest records:

- evaluated Phase 13A `run_id`
- benchmark ID, version, split, and sample ID
- evaluation configuration and fingerprint
- lifecycle timestamps and status
- requested and executed metric engines
- artifact paths
- stable error code and message on failure

Only completed evaluations may be compared or used by a parameter search.

## 8. Baseline Comparison and Regression Gate

Comparison artifacts include:

- candidate and baseline evaluation IDs
- absolute and relative metric deltas
- per-scene and per-class deltas when available
- configured tolerances
- regression findings

The default gate considers:

- point mIoU
- instance F1
- mean box IoU
- noise F1
- over-segmentation count
- under-segmentation count

Thresholds are explicit configuration, not hidden constants. A failed golden regression gate prevents recommendation promotion but does not delete results, overwrite the baseline, or change production configuration.

## 9. Automatic Parameter Search

Two built-in strategies are supported:

- Deterministic grid search.
- Fixed-seed random search without replacement.

Search configuration defines:

- Phase 13A parameter space
- strategy
- random seed
- maximum trial count
- benchmark/sample set
- per-trial timeout metadata
- scoring weights
- gate thresholds

Every trial has an independent Phase 13A run and Phase 13B evaluation record. A failed trial is retained and does not stop unrelated trials.

The default composite score prioritizes instance F1 and includes:

- positive weights for instance F1, point mIoU, and mean box IoU
- penalties for noise, over-segmentation, under-segmentation, and runtime

All weights are recorded in the search manifest. Ranking is deterministic. Ties are resolved by lower runtime and then lexicographic configuration fingerprint.

The search produces:

```text
reports/segmentation_searches/<asset_id>/<search_id>/
  search_run.json
  trials/<trial_id>.json
  recommendation.json
```

`recommendation.json` is advisory. Applying it to production requires a later explicit workflow.

## 10. Public Interfaces

CLI commands:

- `import-segmentation-benchmark`
- `evaluate-segmentation-run`
- `compare-segmentation-runs`
- `search-segmentation-params`

Read-only API routes expose:

- benchmark list and detail
- evaluation list and detail
- comparison and regression gate
- search status, trials, and recommendation

The frontend displays:

- golden-label coverage
- point mIoU
- instance F1
- box IoU
- regression gate status
- best composite score
- recommended parameters

The frontend must label these values as golden-label evaluation results and keep them visually distinct from Phase 13A operational proxy quality.

## 11. Error Handling and Safety

- Strict fingerprint mismatch fails before metric calculation.
- Low coordinate-match coverage fails without publishing accuracy.
- Ambiguous matches are reported and never selected silently.
- Invalid or unsafe identifiers and paths are rejected before filesystem access.
- Existing benchmark, evaluation, or search IDs are not silently overwritten.
- Failed evaluations and trials retain their manifests.
- Regression failure never mutates a baseline or production configuration.
- Search budgets are mandatory and finite.
- Every fallback records requested engine, executed engine, and reason.

## 12. Verification

Tests cover:

- Valid JSON and JSONL benchmark import.
- Schema, semantic, identifier, and path-safety failures.
- Strict fingerprint/index correspondence.
- Coordinate tolerance coverage and ambiguity.
- Known point IoU and mIoU examples.
- Known instance precision, recall, F1, over-segmentation, and under-segmentation examples.
- Known axis-aligned box IoU examples and truthful engine fallback.
- Evaluation lifecycle and artifact persistence.
- Candidate/baseline deltas and regression gates.
- Reproducible grid and fixed-seed random search.
- Failed trial isolation and bounded trial counts.
- CLI, API, frontend, and documentation contracts.
- Full Phase 1 through Phase 13A regression.

## 13. Delivery Order

1. Benchmark contracts and import validation.
2. Point correspondence.
3. Point, instance, and box metrics.
4. Versioned evaluation orchestration.
5. Baseline comparison and regression gates.
6. Deterministic grid and random search.
7. CLI, API, frontend summary, and documentation.

