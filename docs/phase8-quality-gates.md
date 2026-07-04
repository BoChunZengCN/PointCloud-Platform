# Phase 8 质量门禁 / Quality Gates

Phase 8 将 Phase 6/7 的分析 findings 转换成生产工作流可直接读取的质量门禁状态。分析报告仍然负责描述点云事实，质量门禁报告负责回答“当前资产能否继续交付”。

Phase 8 converts Phase 6/7 analysis findings into quality-gate states that production workflows can read directly. Analysis reports describe point-cloud facts; quality-gate reports answer whether an asset can continue toward delivery.

## 模块清单 / Module List

| 模块 / Module | 状态 / Status | 说明 / Notes |
| --- | --- | --- |
| P8-M1 Findings 规则映射 / Findings rule mapping | 已完成 / Done | `build_quality_gate` 将 findings 映射为 `passed`、`review_required` 或 `blocked`。 |
| P8-M2 质量门禁报告 / Quality gate report | 已完成 / Done | 写出 `quality_gate.json` 和 `quality_gate.md`。 |
| P8-M3 check-quality-gate CLI | 已完成 / Done | 从 `point_cloud_analysis.json` 生成质量门禁报告。 |
| P8-M4 质量门禁 API / Quality gate API | 已完成 / Done | `GET /quality-gates/<asset_id>` 返回门禁报告。 |
| P8-M5 前端质量门禁状态条 / Frontend quality gate status bar | 已完成 / Done | 驾驶舱显示可交付、需复核、阻塞状态。 |
| P8-M6 文档与回归 / Docs and regression | 已完成 / Done | README、Phase 8 文档和测试契约保持同步。 |

## 门禁状态 / Gate States

| 状态 / Status | 触发条件 / Trigger | 含义 / Meaning |
| --- | --- | --- |
| `passed` | 没有 findings | 可以继续交付流程。 |
| `review_required` | 只有 warning findings | 需要人工复核，但不自动阻塞。 |
| `blocked` | 存在 critical finding | 交付前必须处理或确认。 |

当前规则：

Current rules:

- `low_rgb_coverage` -> `review_required`
- `low_density_cells` -> `review_required`
- `high_z_span` -> `blocked`

## 命令 / Command

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli check-quality-gate `
  --project-root .\workspace `
  --asset-id sample
```

输入 / Input:

```text
workspace/reports/analysis/<asset_id>/point_cloud_analysis.json
```

输出 / Output:

```text
workspace/reports/quality_gates/<asset_id>/quality_gate.json
workspace/reports/quality_gates/<asset_id>/quality_gate.md
```

## API / API

```text
GET /quality-gates/<asset_id>
```

返回示例 / Example response:

```json
{
  "schema_version": "1.0",
  "asset_id": "sample",
  "status": "review_required",
  "severity": "warning",
  "finding_count": 1,
  "actions": ["Review RGB colorization before delivery."],
  "source_analysis": "reports/analysis/sample/point_cloud_analysis.json"
}
```

## 前端 / Frontend

驾驶舱新增 `quality-gate-status-bar`：

The dashboard adds `quality-gate-status-bar`:

- `passed` 显示为可交付。
- `review_required` 显示为需复核。
- `blocked` 显示为阻塞。

## 后续扩展 / Next Extensions

- 将 `blocked` 门禁状态联动到 Phase 4 production job 的 step 状态。
- 在交付包导出前强制读取质量门禁，阻止未复核资产被打包。
- 支持项目级质量门禁，汇总多个资产的最严重状态。
