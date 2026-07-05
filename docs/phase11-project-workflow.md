# Phase 11 Project Workflow / 项目级工作流闭环

Phase 11 connects asset-level gates and reports into a project-level workflow. It keeps the current local-file architecture, but adds project summaries that production users can use before delivery.

Phase 11 将资产级质量门禁、交付门禁、Job 和报告统一到项目级闭环。当前仍保持本地文件架构，但增加可供生产交付前判断的项目级汇总。

## Modules / 功能模块

| Module | Status | Description | Output |
| --- | --- | --- | --- |
| P11-M1 Project gate / 项目级门禁 | Done | Aggregates all asset quality gates and derives the worst project state. | `project_gate.json` |
| P11-M2 Project gate CLI / 项目级门禁命令 | Done | Builds the project gate from `asset_index.json` and quality gate reports. | `check-project-gate` |
| P11-M3 Delivery manifest audit / 交付审计增强 | Done | Records the delivery gate decision inside `delivery_manifest.json`. | `delivery_gate_decision` |
| P11-M4 Job gate link / Job 门禁联动 | Done | Applies quality gate status to a production job step. | blocked/completed job step |
| P11-M5 Batch run plan / 批处理计划 | Done | Generates a multi-asset batch plan for analyze, gate, segmentation, and delivery checks. | `batch_run_plan.json` |
| P11-M6 Report center / 报告中心 | Done | Exposes a report center API and frontend contract. | `GET /reports/center` |

## CLI / 命令

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli check-project-gate --project-root workspace
$env:PYTHONPATH="src"; python -m pc_system.cli plan-batch-run --project-root workspace
```

## API / 接口

```text
GET /project-gate
GET /reports/center
```

The report center scans `reports/` and `delivery/` for JSON, Markdown, and HTML outputs.

The project gate uses the most severe asset status: `blocked` > `review_required` / `missing` > `passed`.

This version provides the minimal production contract. Later iterations can add real batch execution, project-level trend history, and richer viewer overlays.
