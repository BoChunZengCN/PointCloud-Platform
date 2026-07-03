# Phase 7 真实 LAS/LAZ 分析接入 / Real LAS/LAZ Analysis Integration

Phase 7 将 Phase 6 的点云分析模型接入真实 workspace 资产。用户不再需要手工准备点记录 JSON；系统可以从 `asset.json` 的 `file.path` 找到源文件，采样后生成同一套分析报告。

Phase 7 connects the Phase 6 point-cloud analysis model to real workspace assets. Users no longer need to manually prepare point-record JSON; the system reads the source path from `asset.json`, samples points, and writes the same analysis report contract.

## 模块清单 / Module List

| 模块 / Module | 状态 / Status | 说明 / Notes |
| --- | --- | --- |
| P7-M1 LAS/LAZ 采样适配器 / LAS/LAZ sampling adapter | 已完成 / Done | `sample_points_from_source` 支持轻量 JSON，并为真实 LAS/LAZ 预留 `laspy` 读取边界。 |
| P7-M2 analyze-asset CLI | 已完成 / Done | 按 `asset_id` 读取 `data/assets/<asset_id>/asset.json`，采样源点云并写分析报告。 |
| P7-M3 资产索引分析状态 / Asset registry analysis status | 已完成 / Done | `asset_index.json` 增加 `analysis_status`、`analysis_report_path` 和 `report_paths.analysis_report`。 |
| P7-M4 分析概览 API / Analysis overview API | 已完成 / Done | `GET /analysis` 返回所有分析报告的轻量汇总。 |
| P7-M5 前端分析概览 / Frontend analysis overview | 已完成 / Done | 驾驶舱新增 `analysis-overview-panel`，显示已生成分析报告数量。 |
| P7-M6 文档与回归 / Docs and regression | 已完成 / Done | README、Phase 7 文档和测试契约保持同步。 |

## 数据流 / Data Flow

```text
data/assets/<asset_id>/asset.json
  -> file.path
  -> sample_points_from_source
  -> analyze_point_records
  -> reports/analysis/<asset_id>/point_cloud_analysis.json
  -> GET /analysis and GET /analysis/<asset_id>
  -> frontend quality and analysis overview panels
```

## 命令 / Command

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli analyze-asset `
  --project-root .\workspace `
  --asset-id sample `
  --max-points 10000 `
  --grid-cell-size 5
```

`--max-points` 控制采样上限，避免在超大 LAS/LAZ 上一次性加载全部点。对于 `.las` / `.laz`，如果未安装 `laspy`，命令会返回明确提示：`python -m pip install laspy`。

`--max-points` controls the sampling limit so very large LAS/LAZ files are not loaded all at once. For `.las` / `.laz`, the command returns a clear `python -m pip install laspy` hint when `laspy` is not installed.

## API / API

```text
GET /analysis
GET /analysis/<asset_id>
```

`GET /analysis` 返回：

`GET /analysis` returns:

```json
{
  "asset_count": 1,
  "analyses": [
    {"asset_id": "sample", "point_count": 10000, "rgb_coverage": 0.98, "finding_count": 0}
  ]
}
```

## 前端 / Frontend

驾驶舱现在有两个分析相关面板：

The dashboard now has two analysis-related panels:

- `quality-insight-panel`：展示当前选中资产的 RGB 覆盖率、网格数量和质量提示。
- `analysis-overview-panel`：展示 workspace 中已生成分析报告的资产数量。

## 后续扩展 / Next Extensions

- 使用分层采样策略，覆盖空间边界和分类类别。
- 把分析 findings 写入生产 job，自动提示复核或阻塞交付。
- 在展示页叠加空间网格热力图。
- 将 `laspy` 读取能力拆成可替换 adapter，便于未来接 PDAL streaming。
