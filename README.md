# 点云平台 / Point Cloud Platform

面向脚架式三维扫描点云数据的处理、质检、切片、分割、Potree 发布、FLS 原始数据接入、Gaussian Splatting 训练编排与生产工具检查系统。

A workflow platform for tripod-based 3D scanning point clouds, covering processed LAS/LAZ ingestion, QA, slicing, rule-based segmentation, Potree publishing, raw FLS ingest planning, Gaussian Splatting orchestration, and production tool checks.

## 项目定位 / Project Purpose

本项目优先支持“已经处理好的 LAS/LAZ 点云数据”路线：假设点云已完成拼接、去噪、坐标统一，并带有颜色信息。系统围绕该资产生成可追溯的元数据、质检报告、预览清单、切片计划、分割结果和模块状态报告。

The first supported route is the processed LAS/LAZ workflow: point clouds are assumed to be registered, denoised, coordinate-aligned, and colorized before entering the system. The platform then produces auditable metadata, QA reports, preview manifests, slice plans, segmentation outputs, and module status reports.

第二条路线面向未处理的 FLS 原始扫描数据。当前实现提供 FLS 接入计划、外部转换器调用边界、Gaussian Splatting 训练计划、统一查看器清单和 Phase 2 状态报告。实际 FLS 转换、PDAL 处理、PotreeConverter、Open3D 和 3DGS 训练器仍作为外部工具接入，避免把重型依赖固化到核心系统中。

The second route targets raw FLS scan files. The current implementation provides FLS ingest planning, external converter boundaries, Gaussian Splatting planning, a unified viewer manifest, and Phase 2 status reports. Real FLS conversion, PDAL processing, PotreeConverter, Open3D, and 3DGS training remain external adapter dependencies so the core workflow stays testable and lightweight.

## 当前能力 / Current Capabilities

| 阶段 / Phase | 模块 / Module | 状态 / Status | 说明 / Notes |
| --- | --- | --- | --- |
| Phase 1 | M1 项目骨架 / Project skeleton | 已完成 / Done | 创建标准项目目录和配置入口。 |
| Phase 1 | M2 LAS 资产元数据 / LAS asset metadata | 已完成 / Done | 支持演示元数据和真实 LAS/LAZ 读取边界。 |
| Phase 1 | M3 QA 报告 / QA report | 已完成 / Done | 输出 JSON 与 HTML 质量报告。 |
| Phase 1 | M4 预览与 Potree 发布 / Preview and Potree publish | 已完成 / Done | 生成预览清单，并可调用 PotreeConverter。 |
| Phase 1 | M5 切片计划与执行 / Slice planning and execution | 已完成 / Done | 支持占位执行器和 PDAL 适配器。 |
| Phase 1 | M6 规则分割 / Rule segmentation | 已完成 / Done | 支持规则计划、占位执行、Open3D 脚本适配和汇总报告。 |
| Phase 1 | M7 模块状态 / Module status | 已完成 / Done | 输出 Phase 1 模块完成状态报告。 |
| Phase 2 | FLS 接入 / FLS ingest | 已完成 / Done | 生成并执行 FLS 转换计划，真实转换器外置。 |
| Phase 2 | Gaussian Splatting | 已完成 / Done | 生成并执行 3DGS 训练计划，训练器外置。 |
| Phase 2 | 统一查看器 / Unified viewer | 已完成 / Done | 汇总 Potree、Splat 和报告入口清单。 |
| Phase 2 | Phase 2 状态报告 / Phase 2 status | 已完成 / Done | 输出 Phase 2 模块状态。 |
| Phase 3 | P3-M1 生产工具检查 / Production tool check | 已完成 / Done | 检查外部工具路径并输出检查报告。 |
| Phase 3 | P3-M2 生产运行计划 / Production run plan | 已完成 / Done | 串联 Phase 1/2/3 命令，生成可审计计划。 |
| Phase 3 | P3-M3 生产运行报告 / Production run report | 已完成 / Done | 根据计划输出运行状态报告。 |
| Phase 3 | P3-M4 部署包检查 / Deployment package checklist | 已完成 / Done | 检查交付所需关键产物是否齐备。 |
| Phase 3 | P3-M5 交付包导出 / Delivery package export | 已完成 / Done | 复制 ready 文件并输出交付 manifest。 |
| Phase 4 | P4-M1 任务生命周期 / Job lifecycle CLI | 已完成 / Done | 从生产计划创建 job，并更新 step 状态。 |
| Phase 4 | P4-M2 Job 状态 API 与驾驶舱 / Job status API and dashboard | 已完成 / Done | API 汇总 job 状态，并在前端驾驶舱展示最新生产任务。 |

## 技术路线 / Technical Routes

### 已处理 LAS/LAZ 路线 / Processed LAS/LAZ Route

1. 初始化项目工作区。
2. 读取 LAS/LAZ 元数据，生成资产记录。
3. 运行 QA，输出 JSON/HTML 报告。
4. 生成预览清单，按需发布 Potree 目录。
5. 创建并执行空间切片计划。
6. 对切片结果执行规则分割，生成标签和统计报告。
7. 输出模块状态报告，形成可追溯交付物。

1. Initialize the project workspace.
2. Read LAS/LAZ metadata and create an asset record.
3. Run QA and write JSON/HTML reports.
4. Create preview manifests and optionally publish Potree output.
5. Plan and execute spatial slices.
6. Run rule-based segmentation and write labels plus summary reports.
7. Write module status reports for auditable delivery.

### 原始 FLS 路线 / Raw FLS Route

1. 为 FLS 原始文件创建接入计划。
2. 通过外部转换器生成 LAS/LAZ 中间资产。
3. 复用 LAS/LAZ 路线进行 QA、预览、切片和分割。
4. 为 Gaussian Splatting 创建训练计划。
5. 调用外部训练器生成 Splat 结果。
6. 发布统一查看器清单，串联 Potree、Splat 和报告入口。
7. 进入 Phase 3 生产工具检查与交付前校验。

1. Create an ingest plan for raw FLS files.
2. Use an external converter to produce LAS/LAZ intermediate assets.
3. Reuse the LAS/LAZ workflow for QA, preview, slicing, and segmentation.
4. Create a Gaussian Splatting training plan.
5. Call an external trainer to generate Splat outputs.
6. Publish a unified viewer manifest connecting Potree, Splat, and report entries.
7. Move into Phase 3 production tool checks and pre-delivery validation.

## 安装与验证 / Setup and Verification

本项目的核心测试不要求安装点云重型依赖。真实 LAS/LAZ 读取需要 `laspy`，生产切片、分割、Potree 发布和 3DGS 训练需要相应外部工具。

The core test suite does not require heavy point-cloud dependencies. Real LAS/LAZ reading needs `laspy`; production slicing, segmentation, Potree publishing, and 3DGS training require their respective external tools.

```powershell
python -m pytest tests -q -p no:cacheprovider
```

可选依赖 / Optional dependency:

```powershell
python -m pip install laspy
```


Job Runner / 本地作业状态:

Job Runner 当前以文件方式记录生产计划的执行状态，适合作为后续异步任务队列的轻量地基。API 会从 `reports/jobs/<asset_id>/*.json` 读取 job 状态，并返回 `job_count`、`latest_job`、`status_summary` 给前端驾驶舱。

The Job Runner currently records production-plan execution state as local JSON files. The API exposes `job_count`, `latest_job`, and `status_summary` so the dashboard can show read-only production progress before a later async queue is added.
## 前端工作台 / Frontend Workbench

FE-M1 已提供静态点云项目工作台，入口文件位于 `frontend/index.html`。第一版使用 `frontend/data/sample-project.json` 作为样例项目数据，并在浏览器限制本地 JSON 读取时回退到脚本内置样例数据。

FE-M1 provides a static point-cloud project workbench at `frontend/index.html`. The first version uses `frontend/data/sample-project.json` as sample project data and falls back to embedded sample data when a browser blocks local JSON loading.

打开方式 / Open directly:

```powershell
.\frontend\index.html
```

当前工作台包含项目概览、资产总览、canvas 点云预览、Phase 1/2/3 流程轨道和报告入口。FE-M2 已优先读取 FastAPI `/assets`，API 不可用时再读取 `workspace/data/assets/asset_index.json`，没有真实索引时回退到样例数据。

The current workbench includes project summary, asset facts, a canvas point-cloud preview, Phase 1/2/3 workflow steps, and report links. FE-M2 now prefers the FastAPI `/assets` endpoint, falls back to `workspace/data/assets/asset_index.json`, and finally uses sample data when no real registry exists.

展示型查看器入口 / Showcase viewer:

```powershell
.\frontend\viewer.html
```

最小 API 服务 / Minimal API service:

```powershell
$env:PC_SYSTEM_PROJECT_ROOT=".\workspace"; python -m uvicorn pc_system.api:app
```
## 常用命令 / Common Commands

初始化工作区 / Initialize workspace:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli init --project-root .\workspace
```

运行 Phase 1 演示流程 / Run the Phase 1 demo workflow:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli demo-phase1 --project-root .\workspace --las-path .\sample.las
```

读取真实 LAS/LAZ / Ingest a real LAS/LAZ file:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli ingest --project-root .\workspace --las-path .\sample.las
```

发布 Potree / Publish Potree output:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli publish-potree `
  --project-root .\workspace `
  --asset-id sample `
  --converter-path C:\tools\PotreeConverter.exe
```

创建切片计划 / Create a slice plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-slice `
  --project-root .\workspace `
  --asset-id sample `
  --name room-a `
  --min 0 0 0 `
  --max 10 10 3 `
  --voxel-size 0.05 `
  --output-format ply
```

执行切片计划 / Execute a slice plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-slice `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a
```

使用 PDAL 执行切片 / Execute a slice plan with PDAL:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-slice `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --engine pdal `
  --pdal-path C:\tools\pdal.exe
```

创建规则分割计划 / Create a rule segmentation plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline
```

执行规则分割 / Execute rule segmentation:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline
```

使用 Open3D 脚本执行规则分割 / Execute rule segmentation with an Open3D script:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline `
  --engine open3d `
  --python-path C:\Python\python.exe `
  --script-path .\scripts\open3d_rule_segment.py
```

生成分割汇总报告 / Create a segmentation summary report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli report-rule-segment `
  --project-root .\workspace `
  --asset-id sample `
  --slice-name room-a `
  --name baseline
```

生成 Phase 1 状态报告 / Write the Phase 1 status report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli module-status --project-root .\workspace
```

## Phase 2 命令 / Phase 2 Commands

规划 FLS 接入 / Plan FLS ingest:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-fls-ingest `
  --project-root .\workspace `
  --asset-id site-a `
  --raw-files C:\scan\a.fls C:\scan\b.fls `
  --output-las C:\out\site-a.las
```

执行 FLS 接入 / Execute FLS ingest:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-fls-ingest `
  --project-root .\workspace `
  --asset-id site-a `
  --converter-path C:\tools\fls2las.exe
```

规划 Gaussian Splatting / Plan Gaussian Splatting:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-gaussian-splat `
  --project-root .\workspace `
  --asset-id site-a `
  --name baseline `
  --source-las C:\out\site-a.las `
  --iterations 3000
```

执行 Gaussian Splatting / Execute Gaussian Splatting:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli execute-gaussian-splat `
  --project-root .\workspace `
  --asset-id site-a `
  --name baseline `
  --trainer-path C:\tools\train_3dgs.py
```

发布统一查看器 / Publish the unified viewer manifest:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli publish-phase2-viewer `
  --project-root .\workspace `
  --asset-id site-a `
  --potree-path previews/site-a/potree/metadata.json `
  --splat-path previews/site-a/splats/baseline/point_cloud.ply `
  --report reports/site-a/quality_report.html
```

生成 Phase 2 状态报告 / Write the Phase 2 status report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli phase2-status --project-root .\workspace
```

## Phase 3 命令 / Phase 3 Commands

检查生产工具路径 / Check production tool paths:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli phase3-tool-check `
  --project-root .\workspace `
  --fls-converter C:\tools\fls2las.exe `
  --pdal-path C:\tools\pdal.exe `
  --potree-converter C:\tools\PotreeConverter.exe `
  --gaussian-trainer C:\tools\train_3dgs.py `
  --open3d-script .\scripts\open3d_rule_segment.py
```


生成生产运行计划 / Create a production run plan:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli plan-production-run `
  --project-root .\workspace `
  --asset-id sample
```

生成生产运行报告 / Create a production run report:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli report-production-run `
  --project-root .\workspace `
  --asset-id sample
```

生成资产索引 / Build the asset registry:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli index-assets --project-root .\workspace
```

检查部署交付包 / Check the deployment package:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli check-deployment-package `
  --project-root .\workspace `
  --asset-id sample
```

导出交付包 / Export the delivery package:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli export-delivery-package `
  --project-root .\workspace `
  --asset-id sample `
  --zip
```

## Phase 4 命令 / Phase 4 Commands

创建生产 job / Create a production job:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli create-production-job `
  --project-root .\workspace `
  --asset-id sample `
  --job-id job-sample-prod
```

读取 job 状态 / Read job status:

```text
GET /runs/<asset_id>/jobs
``` 

更新 job step 状态 / Update a job step status:

```powershell
$env:PYTHONPATH="src"; python -m pc_system.cli update-job-step `
  --project-root .\workspace `
  --asset-id sample `
  --job-id job-sample-prod `
  --step-id ingest `
  --status completed `
  --message "LAS metadata ready"
```
## 输出结构 / Output Structure

```text
workspace/
  data/
    raw/
    assets/<asset_id>/asset.json
    assets/<asset_id>/slices/<slice_name>/slice_plan.json
    assets/<asset_id>/slices/<slice_name>/<slice_id>.<format>
    assets/<asset_id>/slices/<slice_name>/segments/<name>/rule_segmentation_plan.json
    assets/<asset_id>/slices/<slice_name>/segments/<name>/<segmentation_id>-labels.json
  previews/<asset_id>/preview_manifest.json
  previews/<asset_id>/index.html
  previews/<asset_id>/potree_manifest.json
  previews/<asset_id>/potree/metadata.json
  previews/<asset_id>/phase2_viewer_manifest.json
  reports/<asset_id>/quality_report.json
  reports/<asset_id>/quality_report.html
  reports/<asset_id>/segments/<slice_name>/<name>/segmentation_summary.json
  reports/<asset_id>/segments/<slice_name>/<name>/segmentation_summary.html
  reports/module_status.json
  reports/module_status.md
  reports/phase2_status.json
  reports/phase2_status.md
  reports/phase3_tool_check.json
  reports/phase3_tool_check.md
  reports/deployment/<asset_id>/deployment_checklist.json
  reports/deployment/<asset_id>/deployment_checklist.md
  reports/jobs/<asset_id>/<job_id>.json
  delivery/<asset_id>/delivery_manifest.json
  delivery/<asset_id>/delivery_manifest.md
  delivery/<asset_id>/files/...
  delivery/<asset_id>.zip
  logs/
```

## 设计原则 / Design Principles

- 核心流程优先可测试，外部工具通过适配器边界接入。
- 所有重要步骤先生成计划或清单，再执行并记录状态。
- 报告同时输出机器可读 JSON 和人工可读 HTML/Markdown。
- 代码注释以中文为主，便于后续维护和任务拆解。

- Keep the core workflow testable; integrate external tools through adapter boundaries.
- Generate plans or manifests before execution, then record status changes.
- Write both machine-readable JSON and human-readable HTML/Markdown reports.
- Prefer Chinese comments in code so future maintenance and task breakdown remain easy to follow.

## 项目文档 / Project Documents

- `docs/phase1-development-plan.md`
- `docs/phase2-development-plan.md`
- `docs/phase3-development-plan.md`
- `docs/phase4-development-plan.md`


