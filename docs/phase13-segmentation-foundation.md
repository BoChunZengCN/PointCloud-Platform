# Phase 13A Segmentation Foundation / 分割质量基础

Phase 13A turns the Phase 10 candidate segmentation into a versioned and auditable workflow. It preserves the latest Phase 10 compatibility report while retaining every segmentation run and its object membership artifacts.

Phase 13A 将 Phase 10 物体候选分割升级为版本化、可审计的工作流，同时保留最新 Phase 10 兼容报告。

## Modules / 模块

| Module | Status | Responsibility |
| --- | --- | --- |
| P13A-M1 Segmentation run | Done | Records source version, configuration fingerprint and lifecycle. |
| P13A-M2 Conservative preprocessing | Done | Validates points, removes exact duplicates and optionally samples voxels. |
| P13A-M3 Truthful engine execution | Done | Separates `requested_engine`, `executed_engine` and `fallback_reason`. |
| P13A-M4 Membership artifacts | Done | Writes one point artifact for every object candidate. |
| P13A-M5 Operational quality | Done | Reports no-label proxy metrics without claiming measured accuracy. |
| P13A-M6 Public surfaces | Done | Adds CLI, read-only API, dashboard summary and documentation. |

## Run a Segmentation / 执行分割

```powershell
$env:PYTHONPATH="src"
python -m pc_system.cli run-segmentation `
  --project-root workspace `
  --asset-id scan `
  --run-id seg-run-001 `
  --engine builtin_geometric `
  --distance-threshold 0.2 `
  --min-points 10
```

Optional `--voxel-size` enables deterministic voxel sampling. `--allow-fallback` must be supplied explicitly before an unavailable external engine can use `builtin_geometric`.

## Outputs / 输出

```text
reports/segmentation_runs/<asset_id>/<run_id>/
  segmentation_run.json
  object_segments.json
  segmentation_quality.json
  segmentation_quality.md
  artifacts/<object_id>.points.json
```

The latest successful object report is also published to:

```text
reports/object_segments/<asset_id>/object_segments.json
```

A failed run remains in its own run directory and never replaces the latest successful report.

## API

```text
GET /segmentation-runs/<asset_id>
GET /segmentation-runs/<asset_id>/<run_id>
GET /segmentation-runs/<asset_id>/<run_id>/quality
```

## Operational Proxy Limitation / 运行质量代理限制

The quality report includes point retention, noise ratio, largest-object ratio, tiny-fragment ratio and engine fallback findings. These are operational proxy metrics. They identify suspicious runs but do not measure real segmentation accuracy.

真实准确率、mIoU、实例 precision/recall、欠分割和过分割评价需要 Phase 13B 的黄金标注数据集。
