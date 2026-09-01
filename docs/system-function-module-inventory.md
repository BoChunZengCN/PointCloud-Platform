# 点云平台功能模块总览 / Point Cloud Platform Module Inventory

本文档整理当前系统已规划和已实现的功能模块，供后续开发、验收和交付沟通使用。

This document summarizes the current functional modules of the point-cloud platform for future development, acceptance, and delivery communication.

## 交付门禁是什么 / What Is Delivery Gate

交付门禁是交付包导出前的质量放行控制。它不是用来发现点云问题的模块；发现问题由 Phase 6/7 的点云分析与 Phase 8 的质量门禁完成。交付门禁负责读取质量门禁结果，然后决定是否允许执行 `export-delivery-package`。

Delivery Gate is the release-control layer before exporting a delivery package. It does not discover point-cloud quality issues by itself; Phase 6/7 analysis and Phase 8 quality gates do that. Delivery Gate reads the quality-gate result and decides whether `export-delivery-package` is allowed.

### 当前规则 / Current Rules

| 质量门禁状态 / Quality Gate Status | 交付导出行为 / Delivery Export Behavior | 说明 / Notes |
| --- | --- | --- |
| `passed` | 允许导出 / Allowed | 资产通过质量门禁，可正常导出交付包。 |
| `review_required` | 默认阻止，显式放行后允许 / Blocked by default, allowed with explicit override | 需要人工复核；使用 `--allow-review-required` 后可导出。 |
| `blocked` | 阻止导出 / Blocked | 即使使用 override 也不允许导出。 |
| missing gate | 阻止导出 / Blocked | 缺少质量门禁报告时，必须先生成 gate。 |

### 典型流程 / Typical Flow

```text
analyze-asset
  -> point_cloud_analysis.json
  -> check-quality-gate
  -> quality_gate.json
  -> export-delivery-package
  -> delivery_manifest.json / zip
```

## 总体功能链路 / Overall Workflow

```text
LAS/LAZ or FLS source
  -> asset metadata
  -> QA / preview / slicing / segmentation
  -> Potree / Gaussian Splatting / viewer manifests
  -> production plan / job / API / frontend dashboard
  -> point-cloud analysis
  -> quality gate
  -> delivery gate
  -> delivery package
```

## Phase 1: 已处理 LAS/LAZ 基础处理 / Processed LAS/LAZ Foundation

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| M1 项目骨架 / Project skeleton | 已完成 / Done | 初始化标准 workspace 目录和配置入口。 | `data/`, `reports/`, `previews/`, `logs/` |
| M2 LAS 资产元数据 / LAS asset metadata | 已完成 / Done | 读取 LAS/LAZ 元数据，生成资产记录。 | `data/assets/<asset_id>/asset.json` |
| M3 QA 报告 / QA report | 已完成 / Done | 输出基础质量检查结果。 | `quality_report.json`, `quality_report.html` |
| M4 预览与 Potree 发布 / Preview and Potree publish | 已完成 / Done | 生成预览清单，必要时调用 PotreeConverter。 | `preview_manifest.json`, `potree_manifest.json` |
| M5 切片计划与执行 / Slice planning and execution | 已完成 / Done | 创建空间切片计划，并支持占位/PDAL 执行。 | `slice_plan.json`, sliced point files |
| M6 规则分割 / Rule segmentation | 已完成 / Done | 对切片结果做规则分割，支持 Open3D 脚本适配。 | rule plan, labels, segmentation summary |
| M7 模块状态 / Module status | 已完成 / Done | 输出 Phase 1 模块完成状态。 | `module_status.json`, `module_status.md` |

## Phase 2: FLS 与高级渲染路线 / FLS and Advanced Rendering Route

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| FLS 接入 / FLS ingest | 已完成 / Done | 为原始 FLS 文件创建转换计划，并调用外部转换器边界。 | FLS ingest plan/report |
| Gaussian Splatting | 已完成 / Done | 生成并执行 3DGS 训练计划，训练器外置。 | Gaussian Splatting plan/output manifest |
| 统一查看器 / Unified viewer | 已完成 / Done | 汇总 Potree、Splat、报告入口。 | `phase2_viewer_manifest.json` |
| Phase 2 状态报告 / Phase 2 status | 已完成 / Done | 输出 Phase 2 模块状态。 | `phase2_status.json`, `phase2_status.md` |

## Phase 3: 生产化与交付 / Production and Delivery

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P3-M1 生产工具检查 / Production tool check | 已完成 / Done | 检查 FLS/PDAL/Potree/3DGS/Open3D 等工具路径。 | `phase3_tool_check.json`, `.md` |
| P3-M2 生产运行计划 / Production run plan | 已完成 / Done | 串联 Phase 1/2/3 命令，形成可审计计划。 | `production_run_plan.json`, `.md` |
| P3-M3 生产运行报告 / Production run report | 已完成 / Done | 根据计划输出运行状态报告。 | `production_run_report.json`, `.md` |
| P3-M4 部署包检查 / Deployment package checklist | 已完成 / Done | 检查交付所需关键产物是否齐备。 | `deployment_checklist.json`, `.md` |
| P3-M5 交付包导出 / Delivery package export | 已完成 / Done | 复制 ready 文件，生成交付 manifest 和 zip。 | `delivery_manifest.json`, `.md`, `.zip` |

## Phase 4: 生产 Job Runner / Production Job Runner

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P4-M1 Job 生命周期 CLI / Job lifecycle CLI | 已完成 / Done | 从生产计划创建 job，更新 step 状态。 | job JSON files |
| P4-M2 Job 状态 API 与驾驶舱 / Job status API and dashboard | 已完成 / Done | API 汇总 job 状态，并在前端展示最新任务。 | `/runs/<asset_id>/jobs` |
| P4-M3 受控 Job 写入 API / Controlled Job Write API | 已完成 / Done | 通过 API 创建 job、更新 step。 | POST/PATCH job endpoints |
| P4-EX1 前端 Job 操作面板 / Frontend job operation panel | 已完成 / Done | 在驾驶舱创建 job、更新 step 状态。 | frontend controls |
| P4-EX2 Job 操作审计 / Job audit event log | 已完成 / Done | 记录 job 创建和状态更新事件。 | JSONL audit events |
| P4-EX3 Job 详情 API / Job detail API | 已完成 / Done | 返回单 job 和审计事件。 | job detail endpoint |
| P4-EX4 Retry/Block/Fail 语义 / Retry, block, fail semantics | 已完成 / Done | 记录 attempt、last_error、updated_at。 | richer job state |
| P4-EX5 轻量执行适配器 / Lightweight execution adapter | 已完成 / Done | 提供不执行 shell 的本地状态推进适配器。 | local execution adapter |
| P4-EX6 队列接口预留 / Async queue interface reservation | 已完成 / Done | 以 JSONL 形式预留 enqueue/list 队列接口。 | queue JSONL contract |

## Phase 5: API 生产加固 / API Production Hardening

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P5-M1 API 写入保护 / API write protection | 已完成 / Done | 写入路由支持 API Key 保护。 | API key guard |
| P5-M2 运行模式 / Run modes | 已完成 / Done | 支持 development / production 模式。 | run-mode config |
| P5-M3 API 启动 CLI / API service CLI | 已完成 / Done | 新增 `serve-api` 命令。 | API start command |
| P5-M4 前端 API 状态 / Frontend API health status | 已完成 / Done | 驾驶舱显示 API 在线状态和写入保护。 | API status bar |
| P5-M5 Workspace 一致性检查 / Workspace consistency report | 已完成 / Done | 检查 asset/plan/job/events 一致性。 | consistency report |
| P5-M6 部署文档 / Deployment docs | 已完成 / Done | 新增最小生产部署说明。 | `phase5-production-hardening.md` |

## Phase 6: 点云分析与质量洞察 / Point-Cloud Analysis and Quality Insights

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P6-M1 点云深度统计模型 / Point-cloud analysis model | 已完成 / Done | 统计点数、边界、密度、RGB 覆盖率、分类分布。 | analysis JSON fields |
| P6-M2 采样与空间网格统计 / Sampling and spatial grid stats | 已完成 / Done | 生成空间网格和单元统计。 | `grid` stats |
| P6-M3 质量异常检测报告 / Quality findings report | 已完成 / Done | 输出 RGB 缺失、高程跨度、低密度网格提示。 | `findings` |
| P6-M4 分析 CLI / Analysis CLI | 已完成 / Done | 新增 `analyze-point-cloud`。 | `point_cloud_analysis.json`, `.md` |
| P6-M5 分析 API / Analysis API | 已完成 / Done | 读取单资产分析结果。 | `GET /analysis/<asset_id>` |
| P6-M6 前端质量洞察 / Frontend quality insights | 已完成 / Done | 驾驶舱展示 RGB、网格、质量提示。 | quality insight panel |

## Phase 7: 真实 LAS/LAZ 分析接入 / Real LAS/LAZ Analysis Integration

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P7-M1 LAS/LAZ 采样适配器 / LAS/LAZ sampling adapter | 已完成 / Done | 从轻量 JSON 或真实 LAS/LAZ 源采样点记录。 | sampled point records |
| P7-M2 analyze-asset CLI | 已完成 / Done | 按 workspace 资产 ID 直接生成分析报告。 | `analyze-asset` |
| P7-M3 资产索引分析状态 / Asset registry analysis status | 已完成 / Done | asset_index 增加 `analysis_status` 和报告路径。 | asset registry fields |
| P7-M4 分析概览 API / Analysis overview API | 已完成 / Done | 汇总所有分析报告。 | `GET /analysis` |
| P7-M5 前端分析概览 / Frontend analysis overview | 已完成 / Done | 驾驶舱展示已分析资产数量。 | analysis overview panel |
| P7-M6 文档与回归 / Docs and regression | 已完成 / Done | README 与 Phase 7 文档同步。 | `phase7-real-las-analysis.md` |

## Phase 8: 质量门禁 / Quality Gates

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P8-M1 Findings 规则映射 / Findings rule mapping | 已完成 / Done | 将 findings 映射成 `passed`、`review_required`、`blocked`。 | gate decision |
| P8-M2 质量门禁报告 / Quality gate report | 已完成 / Done | 写出质量门禁 JSON 与 Markdown。 | `quality_gate.json`, `.md` |
| P8-M3 check-quality-gate CLI | 已完成 / Done | 从分析报告生成质量门禁。 | `check-quality-gate` |
| P8-M4 质量门禁 API / Quality gate API | 已完成 / Done | 返回单资产质量门禁。 | `GET /quality-gates/<asset_id>` |
| P8-M5 前端质量门禁状态条 / Frontend quality gate status bar | 已完成 / Done | 驾驶舱展示可交付、需复核、阻塞。 | quality gate status bar |
| P8-M6 文档与回归 / Docs and regression | 已完成 / Done | README 与 Phase 8 文档同步。 | `phase8-quality-gates.md` |

## Phase 9: 交付门禁 / Delivery Gates

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P9-M1 交付门禁策略 / Delivery gate policy | 已完成 / Done | 判断质量门禁是否允许导出交付包。 | `evaluate_delivery_gate` |
| P9-M2 阻止 blocked 导出 / Block blocked delivery export | 已完成 / Done | `blocked` 时 `export-delivery-package` 返回 2，不生成交付包。 | blocked export guard |
| P9-M3 复核态显式放行 / Review override | 已完成 / Done | `review_required` 默认阻止，`--allow-review-required` 后允许。 | CLI override |
| P9-M4 部署检查门禁状态 / Deployment checklist gate status | 已完成 / Done | deployment checklist 增加 required 的 `quality_gate` 项。 | checklist item |
| P9-M5 前端交付提示 / Frontend delivery gate notice | 已完成 / Done | 驾驶舱展示可导出、需复核、不可导出。 | delivery gate notice |
| P9-M6 文档与回归 / Docs and regression | 已完成 / Done | README 与 Phase 9 文档同步。 | `phase9-delivery-gates.md` |

## Phase 10: 物体分割 / Object Segmentation

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P10-M1 物体候选模型 / Object candidate model | 已完成 / Done | 从点记录按三维距离聚类，生成物体候选。 | `segment_object_candidates` |
| P10-M2 分割报告写出 / Segmentation report output | 已完成 / Done | 写出可审计 JSON 与 Markdown 报告。 | `object_segments.json`, `.md` |
| P10-M3 segment-objects CLI | 已完成 / Done | 从轻量 points JSON 生成物体分割报告。 | `segment-objects` |
| P10-M4 物体分割 API / Object segmentation API | 已完成 / Done | 返回单资产物体分割结果。 | `GET /segments/<asset_id>/objects` |
| P10-M5 前端物体分割面板 / Frontend object segmentation panel | 已完成 / Done | 驾驶舱展示对象数量、噪声点和候选对象摘要。 | object segmentation panel |
| P10-M6 文档与回归 / Docs and regression | 已完成 / Done | README 与 Phase 10 文档同步。 | `phase10-object-segmentation.md` |
| P10-EX1 资产源直分割 / Asset source segmentation | 已完成 / Done | 按 asset_id 读取资产源点云并直接分割。 | `segment-asset-objects` |
| P10-EX2 分割配置 / Segmentation config | 已完成 / Done | 用 JSON 配置控制距离阈值、最小点数、最大采样数和 engine。 | config JSON |
| P10-EX3 Open3D 适配边界 / Open3D adapter boundary | 已完成 / Done | 预留 Open3D DBSCAN runner 注入点，保持输出 schema 稳定。 | `open3d_dbscan` |
| P10-EX4 分割质量指标 / Segmentation quality metrics | 已完成 / Done | 生成噪声比例、对象数量检查和 findings。 | `segmentation_quality` |

## Phase 11: 项目级工作流闭环 / Project Workflow Loop

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P11-M1 项目级门禁 / Project gate | 已完成 / Done | 汇总所有资产质量门禁，生成项目级状态。 | `project_gate.json` |
| P11-M2 check-project-gate CLI | 已完成 / Done | 从资产索引和质量门禁报告写出项目门禁。 | `check-project-gate` |
| P11-M3 交付审计增强 / Delivery manifest audit | 已完成 / Done | 在交付 manifest 中记录放行决策。 | `delivery_gate_decision` |
| P11-M4 Job 门禁联动 / Job gate link | 已完成 / Done | 将 quality gate 状态同步到 job step。 | blocked/completed step |
| P11-M5 批处理计划 / Batch run plan | 已完成 / Done | 为多资产生成 analyze/gate/segment/delivery 批处理计划。 | `batch_run_plan.json` |
| P11-M6 报告中心 / Report center | 已完成 / Done | API 与前端读取项目报告索引。 | `GET /reports/center` |
## 关键 CLI 命令 / Key CLI Commands

## Phase 13A: 分割质量基础 / Segmentation Foundation

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P13A-M1 分割运行 / Segmentation run | 已完成 / Done | 保存数据版本、配置指纹和运行生命周期。 | `segmentation_run.json` |
| P13A-M2 保守预处理 / Conservative preprocessing | 已完成 / Done | 校验、去重、可选体素采样和点保留率。 | preprocessing summary |
| P13A-M3 引擎真实性 / Engine truth | 已完成 / Done | 分别记录请求、实际引擎和回退原因。 | execution metadata |
| P13A-M4 对象成员工件 / Membership artifacts | 已完成 / Done | 为每个对象写出独立点记录。 | `artifacts/*.points.json` |
| P13A-M5 运行质量代理 / Operational quality | 已完成 / Done | 识别噪声、疑似粘连、碎片和回退风险。 | `segmentation_quality.json`, `.md` |
| P13A-M6 公共接口 / Public surfaces | 已完成 / Done | CLI、只读 API、前端摘要和文档。 | `run-segmentation`, API |

## Phase 13B: 黄金分割评估 / Golden Segmentation Evaluation

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P13B-M1 黄金 benchmark / Golden benchmark | 已完成 / Done | 导入版本化 JSON/JSONL 点标签与三维包围盒。 | `benchmarks/<benchmark_id>/` |
| P13B-M2 点对应 / Point correspondence | 已完成 / Done | 严格索引和显式 XYZ 容差对应。 | `correspondence.json` |
| P13B-M3 黄金指标 / Golden metrics | 已完成 / Done | 计算 point mIoU、实例 F1、box IoU、拆分与粘连。 | metric JSON files |
| P13B-M4 回归门禁 / Regression gate | 已完成 / Done | 比较候选与基线并阻止超阈值退化。 | `regression_gate.json` |
| P13B-M5 参数搜索 / Parameter search | 已完成 / Done | 有界网格搜索和固定种子随机搜索。 | `recommendation.json` |
| P13B-M6 公共接口 / Public surfaces | 已完成 / Done | CLI、只读 API、前端黄金评估摘要和文档。 | CLI, API, dashboard |

## Phase 14: 分割纠正闭环 / Segmentation Correction Loop

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P14-M1 会话与基线 / Session and baseline | 已完成 / Done | 从 Phase 13A 运行或历史发布创建有界、精确索引的纠正草稿。 | `correction_session.json`, `baseline_labels.json` |
| P14-M2 事件回放 / Event replay | 已完成 / Done | 处理确认、合并、拆分、改类、噪点、撤销/重做和恢复。 | `events.jsonl`, `draft_labels.json` |
| P14-M3 审查队列 / Review queue | 已完成 / Done | 区分系统建议与人工确认，输出对象级纠正差异。 | `review_queue.json`, `correction_diff.json` |
| P14-M4 不可变发布 / Immutable publication | 已完成 / Done | 冻结审查修订并生成标签版本、派生 benchmark 与谱系。 | correction release, benchmark |
| P14-M5 反馈与训练策略 / Feedback and training policy | 已完成 / Done | 输出纠正前后标签、操作、许可和训练资格，隔离黄金回归数据。 | `segmentation_feedback`, `training_policy.json` |
| P14-M6 公共接口 / Public surfaces | 已完成 / Done | 提供受保护 API、CLI、Canvas 纠正工作台和恢复文档。 | CLI, API, `correction.html` |

## Phase 15A：CAD 模型库基础 / CAD Model Library Foundation

| 模块 / Module | 状态 / Status | 主要职责 / Responsibility | 主要产物 / Outputs |
| --- | --- | --- | --- |
| P15-M1.1 可信身份与权限 / Trusted identity | 已完成 / Done | 生产 token 绑定主体与角色，专家写入、审计员读取验证快照。 | principal contract |
| P15-M1.2 审计操作 / Audit operations | 已完成 / Done | 原位恢复、幂等索引、哈希链事件、失败终态和完整性验证。 | `operation.json`, `events.jsonl` |
| P15-M1.3 模型资产目录 / Model asset catalog | 已完成 / Done | 创建不可变模型资产元数据并提供列表和读取。 | `model_asset.json` |
| P15-M1.4 模型版本导入 / Model version import | 已完成 / Done | 校验 STL、OBJ、PLY，统一单位，发布不可覆盖版本。 | `model_manifest.json`, `source_geometry.json` |
| P15-M1.5 API 与 CLI / API and CLI | 已完成 / Done | 创建资产、导入版本、读取模型库和验证审计。 | model-library API, CLI |
| P15-M1.6 文档与回归 / Documentation and regression | 已完成 / Done | 操作集成说明、端到端审计与失败回滚覆盖。 | `phase15-model-library.md` |
| P15-M2.1 版本发布与回滚 / Version release and rollback | 已完成 / Done | 激活不可变版本、查询历史发布、比较当前头并受审计回滚。 | `release.json`, `current_release.json` |
| P15-M2.2 确定性采样表达 / Deterministic sampled representation | 已完成 / Done | 按显式点数和种子生成、验证并复用不可变网格表面采样点云。 | `sampled_points.json`, `representation.json` |
| P15-M2.3 采样 CLI 与端到端审计 / Sampling CLI and E2E audit | 已完成 / Done | 提供采样、表达列表命令及导入—发布—采样—回滚审计链。 | CLI, audit events |
| P15-M2.4 对象审查与输入 / Object review and input | 已完成 / Done | 新 Phase 14 对象证据硬过滤、旧发布安全软评分、Phase 13A 实验输入。 | review evidence, retrieval object |
| P15-M2.5 版本化特征与配置 / Versioned features and config | 已完成 / Done | 为模型和对象生成同构几何特征，并冻结采样、评分和类别映射配置。 | retrieval config, feature artifacts |
| P15-M2.6 特征索引与发布 / Feature index and release | 已完成 / Done | 构建不可变生产/Challenger 索引，自动采样、覆盖率门禁、激活、历史、陈旧检测和回滚。 | index manifest, release history |
| P15-M2.7 可解释 Top-K 检索 / Explainable Top-K retrieval | 已完成 / Done | 混合类别、文本、厂商型号、尺寸、形状和占用评分，输出降级原因与风险。 | candidates, retrieval report |
| P15-M2.8 公共入口与恢复 / Public surfaces and recovery | 已完成 / Done | 七个 CLI、八个 API、可信身份、幂等重放、哈希链审计和端到端恢复。 | CLI, API, audit events |
| Phase 15C 刚性配准 / Rigid registration | 已完成 / Done | 候选证据冻结、FPFH/RANSAC/FGR、多尺度 ICP、双向指标、三态门禁和审计恢复；不自动绑定。 | rigid transform, quality gate, registration report |
| Phase 15D 人工决策与绑定 / Decisions and bindings | 已规划 / Planned | 确认、换候选、拒绝与不可变对象—模型绑定。 | match decision, binding |
| Phase 15E 实物参考模板 / Scanned reference templates | 已规划 / Planned | 将验证后的单对象参考点云纳入统一模板接口。 | reference template |
| Phase 15F 受控优化 / Controlled optimization | 已规划 / Planned | Champion/Challenger、审批、推广、回滚与审计。 | experiment report |

```powershell
python -m pc_system.cli init --project-root .\workspace
python -m pc_system.cli ingest --project-root .\workspace --las-path .\sample.las
python -m pc_system.cli analyze-asset --project-root .\workspace --asset-id sample
python -m pc_system.cli check-quality-gate --project-root .\workspace --asset-id sample
python -m pc_system.cli segment-objects --project-root .\workspace --asset-id sample --points-json .\workspace\samples\sample.points.json
python -m pc_system.cli segment-asset-objects --project-root .\workspace --asset-id sample
python -m pc_system.cli check-project-gate --project-root .\workspace
python -m pc_system.cli plan-batch-run --project-root .\workspace
python -m pc_system.cli export-delivery-package --project-root .\workspace --asset-id sample
python -m pc_system.cli export-delivery-package --project-root .\workspace --asset-id sample --allow-review-required
python -m pc_system.cli serve-api --project-root .\workspace --host 127.0.0.1 --port 8000
python -m pc_system.cli create-model-asset --project-root .\workspace --model-id pump-a --display-name "Pump A" --category-id pump --actor alice --operation-id op-model-001 --request-id request-model-001 --idempotency-key idem-model-001
python -m pc_system.cli import-model --project-root .\workspace --model-id pump-a --version-id v1 --source .\imports\models\pump-a.obj --unit mm --license internal --actor alice --operation-id op-import-001 --request-id request-import-001 --idempotency-key idem-import-001
python -m pc_system.cli sample-model-version --project-root .\workspace --model-id pump-a --version-id v1 --point-count 100000 --random-seed 20260828 --actor alice --operation-id op-sample-001 --request-id request-sample-001 --idempotency-key idem-sample-001
python -m pc_system.cli list-model-representations --project-root .\workspace --model-id pump-a --version-id v1
python -m pc_system.cli create-model-retrieval-config --project-root .\workspace --config-id retrieval-v1 --feature .\config\feature.json --scoring .\config\scoring.json --category-mapping .\config\mapping.json --actor alice --operation-id op-config-001 --request-id req-config-001 --idempotency-key idem-config-001
python -m pc_system.cli build-model-feature-index --project-root .\workspace --index-id index-production-001 --index-mode production --config-id retrieval-v1 --actor alice --operation-id op-index-001 --request-id req-index-001 --idempotency-key idem-index-001
python -m pc_system.cli release-model-feature-index --project-root .\workspace --index-id index-production-001 --release-id index-release-001 --action activate --reason production --actor alice --operation-id op-index-release-001 --request-id req-index-release-001 --idempotency-key idem-index-release-001
python -m pc_system.cli retrieve-model-candidates --project-root .\workspace --retrieval-run-id retrieval-001 --source-kind correction_release --asset-id scan-a --source-id release-001 --instance-id pump-001 --top-k 10 --actor alice --operation-id op-retrieval-001 --request-id req-retrieval-001 --idempotency-key idem-retrieval-001
python -m pc_system.cli show-model-retrieval --project-root .\workspace --asset-id scan-a --source-id release-001 --instance-id pump-001 --retrieval-run-id retrieval-001
```

## 关键 API / Key APIs

| API | 用途 / Purpose |
| --- | --- |
| `GET /health` | API 健康状态。 |
| `GET /assets` | 资产索引。 |
| `GET /analysis` | 分析报告概览。 |
| `GET /analysis/<asset_id>` | 单资产点云分析报告。 |
| `GET /quality-gates/<asset_id>` | 单资产质量门禁。 |
| `GET /project-gate` | 项目级门禁。 |
| `GET /reports/center` | 报告中心索引。 |
| `GET /runs/<asset_id>/jobs` | 生产 job 汇总。 |
| `POST /runs/<asset_id>/jobs` | 创建生产 job。 |
| `PATCH /runs/<asset_id>/jobs/<job_id>/steps/<step_id>` | 更新 job step。 |
| `GET /deployment/<asset_id>` | 部署检查清单。 |
| `GET /delivery/<asset_id>/status` | 交付状态。 |
| `POST /model-matching/retrieval-configs` | 发布不可变检索配置。 |
| `GET /model-matching/retrieval-configs` | 查询检索配置。 |
| `POST /model-matching/feature-indexes` | 构建生产或 Challenger 特征索引。 |
| `GET /model-matching/feature-indexes` | 查询已验证索引。 |
| `POST /model-matching/feature-index-releases` | 激活或回滚生产索引。 |
| `GET /model-matching/feature-index-releases` | 查询索引发布历史。 |
| `POST /model-matching/retrievals` | 执行生产或实验 Top-K 检索。 |
| `GET /model-matching/retrievals/<asset_id>/<source_id>/<instance_id>/<retrieval_run_id>` | 读取已验证检索报告。 |

## 主要前端入口 / Frontend Entrypoints

| 页面 / Page | 文件 / File | 用途 / Purpose |
| --- | --- | --- |
| 项目驾驶舱 / Dashboard | `frontend/index.html` | 资产、任务、分析、质量门禁、交付状态总览。 |
| 展示页 / Showcase viewer | `frontend/viewer.html` | 单资产展示型查看器入口。 |
| 分割纠正 / Segmentation correction | `frontend/correction.html` | 系统建议、对象确认、点选择、纠正与送审。 |
| 设计候选 / Design options | `frontend/design-options/` | UI 风格候选页面。 |

## 当前后续建议 / Recommended Next Iterations

1. Phase 15C：完成最终门禁后统一推送、创建 PR 和合并。
2. Phase 15D：实现人工匹配决策、不可变绑定与双界面。
4. Phase 15E：接入受验证的实物参考点云模板。
5. Phase 15F：实现受控参数优化、审批、推广和回滚。



