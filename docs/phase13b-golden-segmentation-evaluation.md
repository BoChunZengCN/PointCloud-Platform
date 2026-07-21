# Phase 13B Golden Segmentation Evaluation / 黄金分割评估

Phase 13B measures segmentation accuracy against versioned golden labels. It supports point-level instance labels and oriented 3D boxes, while keeping measured accuracy separate from Phase 13A operational proxy quality.

## 1. Import a Benchmark

Benchmark source layout:

```text
benchmark-source/
  benchmark.json
  labels.json
```

Minimal `benchmark.json`:

```json
{
  "schema_version": "1.0",
  "benchmark_id": "plant-golden-v1",
  "benchmark_version": "v1",
  "split": "golden_regression",
  "scene_type": "pipe-rack",
  "point_density": 120.0,
  "coordinate_unit": "m",
  "label_version": "labels-v1",
  "license": "internal",
  "samples": [
    {
      "sample_id": "scan-001",
      "asset_id": "scan",
      "asset_version": "v1",
      "source_uri": "D:/data/scan.points.json",
      "source_fingerprint": "<sha256>",
      "labels_path": "labels.json",
      "labels_format": "json"
    }
  ]
}
```

Labels may use JSON:

```json
{
  "schema_version": "1.0",
  "point_labels": [
    {
      "point_index": 0,
      "x": 0.0,
      "y": 0.0,
      "z": 0.0,
      "instance_id": "pipe-001",
      "class_id": "pipe",
      "is_noise": false
    }
  ],
  "boxes": [
    {
      "instance_id": "pipe-001",
      "class_id": "pipe",
      "center": [0.0, 0.0, 0.0],
      "size": [1.0, 0.2, 0.2],
      "rotation": [0.0, 0.0, 0.0, 1.0]
    }
  ]
}
```

JSONL uses one record per line with `record_type` equal to `point_label` or `box`.

```powershell
python -m pc_system.cli import-segmentation-benchmark `
  --project-root workspace `
  --manifest benchmark-source/benchmark.json
```

Imported files are normalized under `benchmarks/<benchmark_id>/`.

## 2. Point Correspondence

The default `strict_index` mode requires an exact `source_fingerprint` and valid `point_index` values. It is deterministic and fails before reporting accuracy when the point source differs.

The explicit `coordinate_tolerance` mode matches XYZ coordinates using `coordinate_tolerance` and requires `min_match_coverage`. Multiple candidates inside the tolerance are ambiguous and are never selected silently.

Evaluation configuration:

```json
{
  "correspondence_mode": "coordinate_tolerance",
  "coordinate_tolerance": 0.002,
  "min_match_coverage": 0.98,
  "instance_iou_threshold": 0.5,
  "box_iou_threshold": 0.5
}
```

## 3. Evaluate a Segmentation Run

```powershell
python -m pc_system.cli evaluate-segmentation-run `
  --project-root workspace `
  --asset-id scan `
  --run-id seg-run-001 `
  --benchmark-id plant-golden-v1 `
  --sample-id scan-001 `
  --evaluation-id eval-001 `
  --config evaluation-config.json
```

Outputs:

```text
reports/segmentation_evaluations/<asset_id>/<evaluation_id>/
  evaluation_run.json
  correspondence.json
  point_metrics.json
  instance_metrics.json
  bbox_metrics.json
  evaluation_summary.json
```

Key metrics are `point_miou`, `instance_f1`, `mean_box_iou`, noise F1, and over/under-segmentation counts. The built-in box fallback reports `axis_aligned_envelope`; it never claims oriented IoU.

## 4. Compare Against a Baseline

Regression thresholds are explicit:

```json
{
  "point_miou": {"allowed_drop": 0.01},
  "instance_f1": {"allowed_drop": 0.01},
  "mean_box_iou": {"allowed_drop": 0.02},
  "noise_f1": {"allowed_drop": 0.02},
  "over_segmentation_count": {"allowed_increase": 0},
  "under_segmentation_count": {"allowed_increase": 0}
}
```

```powershell
python -m pc_system.cli compare-segmentation-runs `
  --project-root workspace `
  --asset-id scan `
  --comparison-id cmp-001 `
  --baseline-evaluation-id eval-baseline `
  --candidate-evaluation-id eval-001 `
  --thresholds regression-thresholds.json
```

Results are written to `comparison.json` and `regression_gate.json`. A failed gate never overwrites the baseline.

## 5. Bounded Parameter Search

```json
{
  "strategy": "grid",
  "parameter_space": {
    "distance_threshold": [0.1, 0.2, 0.3],
    "min_points": [5, 10, 20]
  },
  "base_config": {
    "engine": "builtin_geometric",
    "max_points": 10000
  },
  "evaluation_config": {
    "instance_iou_threshold": 0.5,
    "box_iou_threshold": 0.5
  },
  "max_trials": 9,
  "seed": 17,
  "trial_timeout_seconds": 300,
  "weights": {
    "instance_f1": 0.4,
    "point_miou": 0.25,
    "mean_box_iou": 0.2,
    "noise_f1": 0.15,
    "over_segmentation": 0.05,
    "under_segmentation": 0.05,
    "runtime_seconds": 0.001
  }
}
```

Use `strategy: "random"` with a fixed seed for repeatable random search without replacement.

```powershell
python -m pc_system.cli search-segmentation-params `
  --project-root workspace `
  --asset-id scan `
  --benchmark-id plant-golden-v1 `
  --sample-id scan-001 `
  --search-id search-001 `
  --config search-config.json
```

The result is advisory. `recommendation.json` contains `advisory_only: true`; the system does not apply it to production configuration.

## 6. API

```text
GET /segmentation-benchmarks
GET /segmentation-benchmarks/<benchmark_id>
GET /segmentation-evaluations/<asset_id>
GET /segmentation-evaluations/<asset_id>/<evaluation_id>
GET /segmentation-comparisons/<asset_id>/<comparison_id>
GET /segmentation-searches/<asset_id>
GET /segmentation-searches/<asset_id>/<search_id>
GET /segmentation-searches/<asset_id>/<search_id>/trials
GET /segmentation-searches/<asset_id>/<search_id>/recommendation
```

Search trials and recommendations record `comparison_id` and the actual
`gate_status`. A search without a baseline remains eligible for an advisory
recommendation but uses `gate_status: "not_evaluated"`; the frontend never
converts recommendation existence into a passed regression gate.

Phase 14 will add visual correction and feedback events. Phase 15 will add model-library retrieval and registration.
