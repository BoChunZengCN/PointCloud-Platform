# 点云转网格第一里程碑：安装、操作与验收边界

## 能力与适用范围

本里程碑把 Phase 14 已发布且已确认的单对象纠正点云，或 Phase 15E 已完成专家核验的扫描参考版本，冻结为米制快照，再按已发布配置运行 Poisson 或 Ball Pivoting 网格重建。系统保存原始/修复网格、预览、指标、质量三态、决定、不可变发布历史与审计。专家工作台为 [`frontend/scan-to-mesh.html`](../frontend/scan-to-mesh.html)。

工程夹具证明输入、状态机和发布契约；它不证明真实工厂扫描的尺寸精度或泛化能力。第二层点云与 Mesh 双证据到 B-Rep/STEP 的 CAD 重建尚未实现。网格发布不会自动进入模型库索引，也不会改变既有对象—模型绑定。

## 安装与部署管理员配置

在可信部署环境安装平台与工作器依赖：

```powershell
python -m pip install -e ".[api,mesh-reconstruction]"
```

首次使用还须初始化项目目录：`python -m pc_system.cli init --project-root .\workspace`。

管理员把下面两个环境变量设置为**绝对、已存在的普通文件路径**。工作器脚本须单独部署；仓库检出的 [`worker.py`](../workers/open3d_mesh/worker.py) 可作为该文件。Python 必须在自己的环境中装有 `open3d`。例如在项目检出目录启动服务前：

```powershell
$env:PC_SYSTEM_MESH_WORKER_PYTHON = (Resolve-Path "C:\Python311\python.exe").Path
$env:PC_SYSTEM_MESH_WORKER_SCRIPT = (Resolve-Path ".\workers\open3d_mesh\worker.py").Path
$env:PC_SYSTEM_PROJECT_ROOT = (Resolve-Path ".\workspace").Path
python -m uvicorn pc_system.api:app --host 127.0.0.1 --port 8000
```

将示例 Python 路径改为实际安装了 Open3D 的解释器。CLI 运行命令也读取同一对环境变量；宿主程序可以显式注入受控 `mesh_worker`。未配置时创建运行返回 `mesh_worker_unavailable`；缺一项、非绝对、缺失、链接或非普通文件会在解析时失败。命令由管理员配置固定，业务 API 与 CLI 参数均不能提供可执行文件、脚本或 Shell 文本。外部进程使用 `shell=False`，运行超时取自已发布配置。

生产 API 还须配置 `PC_SYSTEM_RUN_MODE=production`、`PC_SYSTEM_API_KEY` 和 `PC_SYSTEM_PRINCIPALS_JSON`。后者把访问令牌映射为服务端可信主体，例如 `{"<专家令牌>":{"actor_id":"alice","roles":["expert"]}}`；令牌只能由部署管理员管理，不能把示例字面量当作真实凭据。开发模式可使用 `X-Actor-ID` 与 `X-Actor-Roles`，生产模式忽略这两个自报身份头。

## 发布配置与质量策略

专家先准备两个 JSON 文件，再分别调用 `mesh-config-publish` 和 `mesh-policy-publish`。文件只由本地 CLI 读取；API 业务请求仅引用发布 ID。下例为**工程夹具**，不可作为现场生产精度阈值：

```json
{
  "schema_version": "1.0",
  "algorithm": "poisson",
  "worker_version": "open3d-mesh-0.19-v1",
  "normal_estimation": {"radius_m": 0.05, "max_nn": 30, "orientation": "consistent_tangent_plane", "orientation_k": 20},
  "voxel_size_m": 0.0,
  "poisson": {"depth": 8, "density_quantile": 0.02},
  "ball_pivoting": null,
  "repair": {"remove_duplicate_vertices": true, "remove_degenerate_triangles": true, "remove_non_manifold_duplicates": true, "minimum_component_triangles": 0, "maximum_removed_component_fraction": 0.02, "fill_holes": false},
  "preview": {"target_triangles": 100},
  "limits": {"maximum_input_points": 1000, "maximum_vertices": 100000, "maximum_triangles": 200000, "maximum_artifact_bytes": 10485760, "timeout_seconds": 120},
  "random_seed": 7
}
```

保存为 `mesh-poisson.json`。Ball Pivoting 配置须显式改为 `"algorithm":"ball_pivoting"`、`"poisson":null`、`"ball_pivoting":{"radii_m":[0.02,0.04]}`，使用另一个 `config_id` 发布；失败时不会自动切换算法。

质量策略示例保存为 `mesh-quality.json`：

```json
{
  "schema_version": "1.0",
  "policy_kind": "engineering_fixture",
  "production_context": null,
  "requires_watertight": false,
  "hard_limits": {"maximum_non_finite_vertices": 0, "maximum_non_finite_faces": 0, "maximum_degenerate_face_fraction": 0.02, "maximum_non_manifold_edge_fraction": 0.05, "maximum_component_count": 20, "minimum_largest_component_fraction": 0.80, "minimum_point_coverage": 0.75, "maximum_point_to_mesh_p95_m": 0.05, "maximum_repair_displacement_m": 0.01, "maximum_triangles": 200000},
  "review_limits": {"maximum_boundary_edge_fraction": 0.10, "maximum_component_count": 3, "minimum_largest_component_fraction": 0.95, "minimum_point_coverage": 0.90, "maximum_point_to_mesh_p95_m": 0.02, "maximum_repair_displacement_m": 0.005},
  "decisions": {"hard_failure": "rejected", "review_threshold": "review_required", "all_passed": "passed", "auto_publish_passed": false}
}
```

```powershell
python -m pc_system.cli mesh-config-publish --project-root .\workspace --config-id mesh-poisson-v1 --config .\mesh-poisson.json --actor alice --operation-id config-op-1 --request-id config-req-1 --idempotency-key config-idem-1
python -m pc_system.cli mesh-policy-publish --project-root .\workspace --policy-id mesh-quality-v1 --policy .\mesh-quality.json --actor alice --operation-id policy-op-1 --request-id policy-req-1 --idempotency-key policy-idem-1
```

生产策略须另行记录扫描设备、点间距、预处理、对象族和审批人，并以真实样本标定阈值。配置/策略发布后不可覆盖；更新必须使用新 ID。

## 创建运行、复核、发布与回滚

Phase 14 来源用 `asset_id`、`release_id`、`instance_id`；Phase 15E 来源用 `model_id`、`version_id`。不可传入未审查草稿、任意本机路径或 URL。以下为 Phase 14 CLI 示例：

```powershell
python -m pc_system.cli scan-to-mesh-run --project-root .\workspace --run-id mesh-run-001 --source-kind phase14_correction_release --asset-id plant-scan --source-release-id release-1 --instance-id pipe-1 --config-id mesh-poisson-v1 --policy-id mesh-quality-v1 --actor alice --operation-id run-op-1 --request-id run-req-1 --idempotency-key run-idem-1
python -m pc_system.cli scan-to-mesh-show --project-root .\workspace --run-id mesh-run-001 --actor alice --role expert
```

Phase 15E 运行把来源参数换成 `--source-kind phase15e_scanned_reference --model-id scan-pump --version-id v1`。API 对应 `POST /scan-to-mesh/runs`，请求体必须包含 `run_id`、`source_kind`、`source_ref`、`config_id`、`policy_id`、`operation_id`、`request_id` 和 `idempotency_key`；例如 `source_ref` 为 `{"model_id":"scan-pump","version_id":"v1"}`。`GET /scan-to-mesh/runs`、`GET /scan-to-mesh/runs/{run_id}`、`GET /scan-to-mesh/runs/{run_id}/preview` 分别提供目录、角色裁剪详情和有界预览。

门禁结果是 `passed`、`review_required` 或 `rejected`。策略允许时，`passed` 可由系统主体自动批准并发布；否则由专家确认。`review_required` 必须逐项确认质量报告中的风险码；`rejected` 永远不能批准或发布。专家决定及发布分开提交：

```powershell
python -m pc_system.cli scan-to-mesh-decide --project-root .\workspace --run-id mesh-run-001 --decision approve --reason "已核对来源与质量证据" --actor alice --operation-id decide-op-1 --request-id decide-req-1 --idempotency-key decide-idem-1
python -m pc_system.cli mesh-release --project-root .\workspace --object-key <详情中的object_key> --run-id mesh-run-001 --release-id mesh-release-001 --action activate --reason "发布已核验网格" --actor alice --operation-id release-op-1 --request-id release-req-1 --idempotency-key release-idem-1
```

复核风险时对 `scan-to-mesh-decide` 追加一次或多次 `--acknowledgement <风险码>`。`replace` 和 `rollback` 必须传入 `--expected-current-release-id <当前版本>`；回滚引用历史运行并创建新发布记录，不改写历史。API 使用 `POST /scan-to-mesh/runs/{run_id}/decision`、`POST /scan-to-mesh/objects/{object_key}/releases` 与 `GET /scan-to-mesh/objects/{object_key}/releases`；发布请求体始终包含 `expected_current_release_id`，首次激活为 `null`。

## 错误、恢复与资源边界

| 错误码 | 处理方式 |
| --- | --- |
| `mesh_source_not_reviewed`、`mesh_source_integrity_error` | 返回上游核验/发布流程检查来源证据；不要修改已发布工件。 |
| `mesh_config_not_found`、`mesh_policy_not_found` | 确认使用已发布且未损坏的 ID。 |
| `mesh_worker_unavailable`、`mesh_worker_failed` | 管理员检查两个绝对路径、Open3D 安装和工作器进程；失败不会自动改用另一算法。 |
| `mesh_decision_acknowledgement_required`、`mesh_decision_not_allowed` | 核对风险码和三态门禁；硬拒绝不能强制放行。 |
| `mesh_release_conflict`、`operation_busy` | 重新读取当前头或等待原所有者，不使用新身份接管。 |
| `publication_recovery_required` | 仅用**原** `operation_id`、`request_id`、`idempotency_key` 与完全相同的业务参数重试对应命令；仍无法证明完整性时交由管理员检查审计与工件。 |

不要因运行目录存在就推断完成，也不要在恢复期间手工删除、移动或隔离未知工作目录。审计员可只读检查运行、决定、发布及其事件；操作员只获得业务摘要。

已发布配置限制输入点数、顶点/三角面、单工件字节和工作器超时。部署还应对工作器进程设置独立的并发、CPU、内存和磁盘配额；当前核心的配置字段不能代替操作系统资源隔离。预览只用于局部单对象核验，不是整厂漫游器。

## 验证方式与未完成项

核心工程验收使用真实 Phase 14/15E 冻结工件、两种显式算法配置与确定性假工作器覆盖三态、审批、发布、历史和回滚，并对上游文件做 SHA-256 前后比对。可选 Open3D 测试使用小球面点云执行真实 Poisson/Ball Pivoting；GitHub Actions 的 `workflow_dispatch` 提供独立 `mesh-open3d` 作业。浏览器任务执行专家工作台流程。工程通过后仍须另做真实管道、法兰、罐体和梁柱样本的人工真值及误差验收。
