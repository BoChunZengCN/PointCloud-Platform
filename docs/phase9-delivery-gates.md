# Phase 9 交付门禁 / Delivery Gates

Phase 9 将 Phase 8 的质量门禁接入交付包导出流程。系统不再只是显示质量状态，而是在导出交付包前执行放行策略，防止 blocked 资产被误交付。

Phase 9 connects Phase 8 quality gates to delivery package export. The system no longer only displays quality state; it enforces a release policy before exporting delivery packages so blocked assets cannot be delivered accidentally.

## 模块清单 / Module List

| 模块 / Module | 状态 / Status | 说明 / Notes |
| --- | --- | --- |
| P9-M1 交付门禁策略 / Delivery gate policy | 已完成 / Done | `evaluate_delivery_gate` 判断 `passed`、`review_required`、`blocked` 是否允许导出。 |
| P9-M2 阻止 blocked 导出 / Block blocked delivery export | 已完成 / Done | `export-delivery-package` 遇到 `blocked` 返回退出码 2，不生成交付 manifest。 |
| P9-M3 复核态显式放行 / Review override | 已完成 / Done | `--allow-review-required` 只放行 `review_required`，不放行 `blocked`。 |
| P9-M4 部署检查门禁状态 / Deployment checklist gate status | 已完成 / Done | 部署检查清单增加 required 的 `quality_gate` 项。 |
| P9-M5 前端交付提示 / Frontend delivery gate notice | 已完成 / Done | 驾驶舱新增 `delivery-gate-notice`，展示可导出、需复核、不可导出。 |
| P9-M6 文档与回归 / Docs and regression | 已完成 / Done | README、Phase 9 文档和测试契约保持同步。 |

## 策略 / Policy

| Quality Gate | 默认导出 / Default Export | Override |
| --- | --- | --- |
| `passed` | allowed | 不需要 override |
| `review_required` | blocked | `--allow-review-required` 后允许 |
| `blocked` | blocked | 不允许 override |
| missing gate | blocked | 先生成质量门禁 |

## 命令 / Commands

默认导出会读取：

Default export reads:

```text
workspace/reports/quality_gates/<asset_id>/quality_gate.json
```

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli export-delivery-package `
  --project-root .\workspace `
  --asset-id sample
```

复核态显式放行：

Explicit review-required override:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli export-delivery-package `
  --project-root .\workspace `
  --asset-id sample `
  --allow-review-required
```

## 部署检查 / Deployment Checklist

`build_deployment_checklist` 会增加 required 的质量门禁项：

`build_deployment_checklist` adds a required quality-gate item:

```json
{"name": "quality_gate", "required": true, "path": "reports/quality_gates/sample/quality_gate.json", "status": "passed"}
```

如果状态是 `blocked` 或 `review_required`，部署检查整体状态为 `blocked`。

If the status is `blocked` or `review_required`, the overall deployment checklist status becomes `blocked`.

## 前端 / Frontend

驾驶舱新增 `delivery-gate-notice`：

The dashboard adds `delivery-gate-notice`:

- `passed` -> 可导出。
- `review_required` -> 需复核，使用 `--allow-review-required` 后导出。
- `blocked` -> 不可导出。

## 后续扩展 / Next Extensions

- 将 delivery gate 决策写入交付 manifest，形成审计链。
- 增加项目级 delivery gate，按多个资产的最严重状态汇总。
- 将 blocked 状态自动写入 Phase 4 production job step。
