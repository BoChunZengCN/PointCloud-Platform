# Phase 10 Object Segmentation / 点云物体分割

Phase 10 adds object-level segmentation on top of the existing LAS/LAZ analysis workflow. The first implementation is a lightweight geometric baseline: it clusters point records by distance and writes stable object candidate outputs. Heavy engines such as Open3D, PCL, or learned semantic segmentation models can be added later behind the same output schema.

Phase 10 在已有 LAS/LAZ 分析链路上增加物体级分割。当前第一版采用轻量几何基线：按三维距离连通关系聚类，生成稳定的物体候选输出。后续可在不改前端和 API 契约的情况下接入 Open3D、PCL 或深度学习语义分割模型。

## Modules / 功能模块

| Module | Status | Description | Output |
| --- | --- | --- | --- |
| P10-M1 Object candidate model / 物体候选模型 | Done | Builds clusters from point records and marks small clusters as noise. | in-memory report |
| P10-M2 Segmentation report output / 分割报告写出 | Done | Writes JSON and Markdown reports for review and downstream use. | `object_segments.json`, `object_segments.md` |
| P10-M3 segment-objects CLI | Done | Runs the geometric candidate segmentation from a lightweight points JSON file. | CLI exit code and report files |
| P10-M4 Object segmentation API / 物体分割 API | Done | Exposes the latest object segmentation report for a single asset. | `GET /segments/<asset_id>/objects` |
| P10-M5 Frontend object segmentation panel / 前端物体分割面板 | Done | Shows object count, noise point count, and first candidate summary in the dashboard. | dashboard card |
| P10-M6 Docs and regression / 文档与回归 | Done | Documents the workflow and locks behavior with tests. | this document, tests |

## Data Contract / 数据契约

`reports/object_segments/<asset_id>/object_segments.json` contains:

- `schema_version`: contract version.
- `asset_id`: source asset id.
- `method`: current segmentation method, initially `geometric_cluster`.
- `point_count`: input point count.
- `distance_threshold`: clustering distance threshold.
- `min_points`: minimum accepted object size.
- `object_count`: accepted object candidate count.
- `noise_point_count`: point count in rejected small clusters.
- `objects`: list of object candidates with `object_id`, `label`, `confidence`, `point_count`, `bounds`, `center`, `method`, and optional `rgb_mean`.

## CLI / 命令

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli segment-objects `
  --project-root workspace `
  --asset-id site-a-las `
  --points-json workspace/samples/site-a.points.json `
  --distance-threshold 1.0 `
  --min-points 10
```


## Extension Modules / 扩展模块

| Module | Status | Description | Output |
| --- | --- | --- | --- |
| P10-EX1 Asset source segmentation / 资产源直分割 | Done | Reads `data/assets/<asset_id>/asset.json`, samples the source LAS/LAZ or points JSON, and writes the same object segmentation schema. | `segment-asset-objects` |
| P10-EX2 Segmentation config / 分割配置文件 | Done | Allows `distance_threshold`, `min_points`, `max_points`, and `engine` to be loaded from JSON config. | config-driven run |
| P10-EX3 Open3D adapter boundary / Open3D 适配边界 | Done | Keeps the output schema stable while allowing an Open3D DBSCAN runner to be injected later. | `method=open3d_dbscan` |
| P10-EX4 Segmentation quality / 分割质量指标 | Done | Adds `segmentation_quality` with noise ratio, object count checks, and findings. | `segmentation_quality` |

Run directly from a workspace asset / 从 workspace 资产源直接分割：

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli segment-asset-objects `
  --project-root workspace `
  --asset-id site-a-las `
  --distance-threshold 1.0 `
  --min-points 10 `
  --max-points 10000
```

Run with config / 使用配置文件：

```json
{
  "distance_threshold": 1.0,
  "min_points": 10,
  "max_points": 10000,
  "engine": "builtin"
}
```

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli segment-asset-objects `
  --project-root workspace `
  --asset-id site-a-las `
  --config workspace/object-segmentation.json
```
## Next Extension Points / 后续扩展点

- Add an Open3D adapter for DBSCAN and plane-removal preprocessing.
- Add a PCL/PDAL production adapter for larger files.
- Add a semantic segmentation adapter that maps learned labels onto the same `objects` schema.
- Add viewer overlays so candidates can be inspected and corrected visually.

