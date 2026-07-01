# Phase 6 点云分析与质量洞察 / Point-Cloud Analysis and Quality Insights

Phase 6 面向已经进入平台的 LAS/LAZ 资产，先使用轻量点记录 JSON 建立可测试的分析闭环。真实 LAS/LAZ 的全量采样可以在后续通过 `laspy` 或 PDAL 适配器接入，但核心输出契约保持不变。

Phase 6 targets LAS/LAZ assets already managed by the platform. It starts with lightweight point-record JSON so the analysis loop remains testable. Full LAS/LAZ sampling can later be connected through `laspy` or PDAL adapters while keeping the same output contract.

## 模块清单 / Module List

| 模块 / Module | 状态 / Status | 输出 / Output |
| --- | --- | --- |
| P6-M1 点云深度统计模型 / Point-cloud analysis model | 已完成 / Done | `point_count`, `bounds`, `density`, `rgb_coverage`, `classification_distribution` |
| P6-M2 采样与空间网格统计 / Sampling and spatial grid stats | 已完成 / Done | `grid.cell_size`, `grid.cell_count`, `grid.cells` |
| P6-M3 质量异常检测报告 / Quality findings report | 已完成 / Done | `findings`，包含低 RGB 覆盖、高 Z 跨度、低密度网格提示 |
| P6-M4 CLI 命令 / CLI command | 已完成 / Done | `analyze-point-cloud` 写入 JSON 与 Markdown 报告 |
| P6-M5 API 读取分析结果 / API analysis read | 已完成 / Done | `GET /analysis/<asset_id>` |
| P6-M6 前端质量洞察面板 / Frontend quality-insight panel | 已完成 / Done | 驾驶舱展示 RGB 覆盖率、网格数量和质量提示数量 |

## 输入契约 / Input Contract

`analyze-point-cloud` 当前读取轻量 JSON 数组，每个点记录支持以下字段：

`analyze-point-cloud` currently reads a lightweight JSON array. Each point record can contain:

```json
[
  {"x": 0.0, "y": 0.0, "z": 0.0, "red": 10, "green": 20, "blue": 30, "classification": 2}
]
```

必需字段是 `x`、`y`、`z`。`red`、`green`、`blue` 用于计算 `rgb_coverage`；`classification` 用于生成分类分布。

Required fields are `x`, `y`, and `z`. `red`, `green`, and `blue` drive `rgb_coverage`; `classification` drives the classification distribution.

## 命令 / Command

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli analyze-point-cloud `
  --project-root .\workspace `
  --asset-id sample `
  --points-json .\samples\sample_points.json `
  --grid-cell-size 5
```

输出目录 / Output directory:

```text
workspace/reports/analysis/<asset_id>/point_cloud_analysis.json
workspace/reports/analysis/<asset_id>/point_cloud_analysis.md
```

## API 与前端 / API and Frontend

FastAPI 读取分析报告：

FastAPI reads the analysis report through:

```text
GET /analysis/<asset_id>
```

前端驾驶舱在 API 模式下调用 `/analysis/<asset_id>`，并在 `quality-insight-panel` 中展示 `rgb_coverage`、网格数量和质量提示数量。API 不可用或报告缺失时，面板保持可读的降级状态。

The frontend dashboard calls `/analysis/<asset_id>` in API mode and renders `rgb_coverage`, grid count, and finding count inside `quality-insight-panel`. If the API or report is unavailable, the panel falls back to a readable empty state.

## 后续扩展 / Next Extensions

- 接入真实 LAS/LAZ 分层采样，避免一次性加载超大点云。
- 将质量提示与生产 job 状态联动，形成自动阻塞或复核建议。
- 为分类结果增加业务标签映射，例如地面、结构、管线、设备。
- 将空间网格统计扩展为热力图或查看器叠加层。

- Add real LAS/LAZ stratified sampling to avoid loading very large point clouds at once.
- Link findings to production jobs so severe issues can block or request review.
- Map classifications into business labels such as ground, structure, pipe, and equipment.
- Extend spatial grid statistics into heatmaps or viewer overlays.

