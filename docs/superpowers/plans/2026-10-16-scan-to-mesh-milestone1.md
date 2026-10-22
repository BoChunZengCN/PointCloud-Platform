# 点云到网格第一里程碑实施计划

> **执行要求：** 使用 `superpowers:executing-plans` 在当前会话按任务顺序实施，并在每项生产变更中使用 `superpowers:test-driven-development`。遵守项目“一名实施者、一名独立最终复审者”的效率协议；本轮不启用子代理，最终复审在实现完成后按批准范围单独执行。

**目标：** 将 Phase 14 已发布纠正对象或 Phase 15E 已通过专家核验的单对象点云，转换为可验证、可审批、可发布和可追溯的不可变三角网格版本。

**架构：** 平台核心负责可信来源、版本配置、状态机、审计、审批和发布；Open3D 仅作为受控外部工作器执行法向估计、Poisson/Ball Pivoting 重建和几何统计。核心与工作器通过版本化 JSON 请求及结果清单通信，不通过目录猜测结果，也不在 API 进程执行任意命令。

**技术栈：** Python 3.11+、NumPy、Open3D 0.19 可选工作器、现有文件工件/内核锁/审计服务、FastAPI、原生 HTML/CSS/JavaScript、pytest 与 Playwright。

**规格：** [点云到 CAD 两层逆向建模设计规格](../specs/2026-10-14-scan-to-cad-two-stage-design.md)。本计划只执行规格的第一里程碑；第二层 B-Rep/STEP 不在本计划范围。

## 全局约束

- 只接受两类可信来源：Phase 14 `published` 纠正发布中的一个非噪声 `instance_id`；Phase 15E 完整提交且专家最终决定为 `approved` 的扫描版本。
- 坐标单位统一为米；来源坐标与局部坐标变换、点数、包围盒、来源及点集 SHA-256 必须冻结。禁止从任意 API 路径、URL、压缩包或 Shell 命令读取输入。
- 首版算法仅允许显式 `poisson` 或 `ball_pivoting`。算法、法向、采样、修复和质量参数全部来自已发布版本配置；运行中不得静默回退或改参。
- 工作器使用项目外部署配置解析，不接受请求传入可执行文件、脚本路径或参数模板。核心测试使用确定性假工作器；真实 Open3D 仅在可选集成测试执行。
- 工件只允许普通文件和受控相对标识；拒绝绝对路径、路径穿越、符号链接、Windows 重解析点、设备文件、非有限数、超大文件、超量顶点/面和哈希不符。
- 运行、人工决定和发布均使用现有可信身份、幂等审计与 `model_resource_lock`。完成态必须同时满足不可变工件、业务事件和 completed 审计，不根据目录存在性猜测成功。
- 中断或归属不确定时保留原操作恢复资格，返回 `publication_recovery_required` 或 `operation_busy`；不得自动递归删除、移动或隔离未知目录。
- 网格门禁为 `passed`、`review_required`、`rejected`。`rejected` 永远不能发布；`review_required` 必须由专家批准；`passed` 仅在已发布策略明确允许时自动批准并发布。
- 当前发布头使用期望值比较；失败、驳回、冲突和恢复均不得改变当前发布头。历史发布不可变，可由专家显式回滚为新发布记录。
- 工程夹具阈值只证明契约和状态机，不声明生产精度。真实样本验收另列第三里程碑，不在本计划伪造结论。
- 本计划不实现 CAD/B-Rep/STEP、自由曲面、整场景批处理、自动训练、自动索引、模型匹配或现有绑定修改。
- 每项任务先观察聚焦测试失败，再实现到绿灯；只有里程碑就绪时运行一次全仓非浏览器测试和一次浏览器门禁。只精确暂存本任务文件。

## 文件结构与稳定接口

新增核心模块：

```text
src/pc_system/
  scan_to_mesh_errors.py          # 稳定错误码与领域异常
  scan_to_mesh_source.py          # Phase 14/15E 可信来源解析、单对象提取、快照
  mesh_reconstruction_config.py   # 版本化算法配置与质量策略
  mesh_worker_contract.py         # 请求/结果清单、工件安全验证、工作器协议
  mesh_open3d_worker.py           # 可替换 Open3D 工作器入口
  mesh_quality.py                 # 指标规范化、修复边界与三态门禁
  mesh_reconstruction.py          # 运行所有权、不可变工件、恢复和清单读取
  mesh_release.py                 # 人工决定、发布、当前头、历史和回滚
  scan_to_mesh_catalog.py         # 页面/API 使用的角色裁剪投影与稳定分页
  commands/scan_to_mesh.py        # CLI 适配层
frontend/
  scan-to-mesh.html
  scan-to-mesh.js
  scan-to-mesh.css
workers/open3d_mesh/
  worker.py                       # JSON 请求到 JSON 结果清单的独立进程入口
```

稳定领域接口：

```python
freeze_mesh_source(
    root: Path,
    *,
    source_kind: str,
    source_ref: dict,
) -> dict
# Phase 14 source_ref: {asset_id, release_id, instance_id}
# Phase 15E source_ref: {model_id, version_id}
# 返回 schema_version、source_kind、source_identity、coordinate_unit、
# points、point_count、bounds_m、source_to_local_4x4、source_evidence、fingerprint。

publish_mesh_config(root: Path, *, config_id: str, config: dict,
                    principal: Principal, operation_id: str,
                    request_id: str, idempotency_key: str) -> dict
publish_mesh_quality_policy(root: Path, *, policy_id: str, policy: dict,
                            principal: Principal, operation_id: str,
                            request_id: str, idempotency_key: str) -> dict

class MeshWorker(Protocol):
    def __call__(self, request: dict, workspace: Path) -> dict: ...

create_mesh_run(root: Path, *, run_id: str, source_kind: str,
                source_ref: dict, config_id: str, policy_id: str,
                principal: Principal, operation_id: str,
                request_id: str, idempotency_key: str,
                worker: MeshWorker) -> dict
load_mesh_run(root: Path, run_id: str, *, principal: Principal) -> dict
list_mesh_runs(root: Path, *, principal: Principal, status: str | None = None,
               cursor: str | None = None, limit: int = 50) -> dict

decide_mesh_run(root: Path, *, run_id: str, decision: str, reason: str,
                acknowledgements: list[str], principal: Principal,
                operation_id: str, request_id: str,
                idempotency_key: str) -> dict
release_mesh_run(root: Path, *, object_key: str, run_id: str,
                 release_id: str, action: str, reason: str,
                 expected_current_release_id: str | None,
                 principal: Principal, operation_id: str,
                 request_id: str, idempotency_key: str) -> dict
```

工件根目录：

```text
scan_to_cad/
  mesh_configs/{config_id}/
  mesh_quality_policies/{policy_id}/
  mesh_runs/{run_id}/
    owner.json
    source_snapshot.json
    worker_request.json
    worker_result.json
    raw_mesh.ply
    repaired_mesh.ply
    preview_mesh.glb
    mesh_metrics.json
    quality_report.json
    run_manifest.json
    commit.json
  mesh_reviews/{run_id}/
  mesh_objects/{object_key}/
    releases/{release_id}/
    current_release.json
```

`object_key` 由规范化的来源类型与来源身份计算，不使用用户路径或展示名称。所有清单只保存上述根目录内的相对工件标识。

## 最终复审重点

以下五项是本里程碑最可能发生的严重或重要失败，每一项必须在所属任务先写失败回归：

1. **Phase 14 对象提取越界：** 发布存在但 `instance_id` 不存在、为空、混入其他实例或噪声；任务 1 必须证明只冻结指定非噪声对象且来源证据匹配。
2. **Phase 15E 信任绕过：** 导入已完成但专家未通过、质量硬拒绝或核验证据被篡改；任务 1 必须拒绝并保持无新运行工件。
3. **工作器结果逃逸或伪造：** 相对路径穿越、链接/重解析点、工件哈希不符、NaN/Infinity、资源上限超出；任务 3 必须逐项拒绝，且不把结果标为 completed。
4. **发布中断恢复错误：** 工件可见后、审计完成前进程中断，重试错误地重跑算法或接管他人运行；任务 4 必须证明同一身份只补齐确定步骤，其他身份得到 `operation_busy`。
5. **并发发布头或门禁绕过：** 两个发布/回滚同时更新头，或 `rejected` 被强制发布；任务 5 必须证明期望头比较只有一个赢家，失败方不改历史和当前头。

## 任务 1：可信单对象来源快照

**文件：** 新增 `src/pc_system/scan_to_mesh_errors.py`、`src/pc_system/scan_to_mesh_source.py`、`tests/test_scan_to_mesh_source.py`；只在需要导出稳定辅助函数时微调 `segmentation_correction_releases.py` 或 `reference_review.py`，不得改旧发布语义。

**实现边界：**

- Phase 14 读取 `correction_release.json`、`labels.json`、`provenance.json` 和 `object_review_evidence.json`，验证发布状态、资产/发布身份、来源指纹和完整点标签；按精确 `instance_id` 取 `is_noise == false` 的点。
- Phase 15E 调用 `load_bundle(..., require_completed=True)` 与 `approved_review_evidence(...)`，使用 `reference_points.json` 的米制规范点和固定坐标变换。
- 快照规范排序、有限双精度、至少 3 个唯一非共线点；计算点集、证据和总快照指纹。调用者不得传路径。
- 本任务只返回内存快照，不写运行目录、不调用工作器。

- [x] 红灯：上述来源测试首次收集因 `scan_to_mesh_errors` 模块不存在而失败，确认测试确实约束新功能；随后补齐缺失/空/噪声/未确认/混合对象分支。
- [x] 绿灯：实现两类显式适配器和统一快照；稳定错误码包含 `mesh_source_not_found`、`mesh_source_not_reviewed`、`mesh_source_object_invalid`、`mesh_source_integrity_error`、`mesh_source_limit_exceeded`。
- [x] 验证：新增测试 9 项通过；连同 Phase 14 发布与 Phase 15E 核验聚焦集共 `40 passed in 7.86s`。定向 compileall 与 `git diff --check` 通过。
- [x] 提交：`feat: freeze reviewed scan-to-mesh sources`；提交 SHA 以 Git 历史为准。

## 任务 2：版本化网格配置与质量策略

**文件：** 新增 `src/pc_system/mesh_reconstruction_config.py`、`tests/test_mesh_reconstruction_config.py`。

**接口与约束：**

- `publish_mesh_config(...)` 发布不可变 `1.0` 配置，支持 `poisson` 与 `ball_pivoting`，包括法向邻域、方向、深度或半径、密度裁剪、保守修复、预览简化、随机种子和资源上限。
- `publish_mesh_quality_policy(...)` 发布不可变 `1.0` 策略，包含硬拒绝、专家复核和自动通过阈值；策略明确 `auto_publish_passed` 布尔值。
- 配置/策略 ID 不可覆盖；只允许专家写，操作必须审计。读取时重新规范化和比对指纹，不接受 NaN、未知字段、布尔伪装数字或无界阈值。
- 首版工程默认配置与策略通过显式种子命令发布，不在代码中隐式替换用户选定版本。

- [x] 红灯：配置模块缺失时新增测试收集失败；随后所有者—审计绑定与生产策略上下文分别观察到精确失败。覆盖两算法互斥、非有限/超限、幂等、同 ID 冲突、权限审计和硬门禁不可改写。
- [x] 绿灯：实现规范化、指纹、不可变写入、所有者绑定及审计事件 `mesh.config_published`、`mesh.quality_policy_published`；生产策略额外固定设备、点间距、预处理、对象族和审批人。
- [x] 验证：新增测试 15 项与可信身份/审计聚焦集共 `141 passed in 3.46s`；定向 compileall 和差异检查通过。
- [x] 提交：`feat: publish versioned mesh reconstruction policies`；提交 SHA 以 Git 历史为准。

## 任务 3：工作器契约、Open3D 适配器与结果完整性

**文件：** 新增 `src/pc_system/mesh_worker_contract.py`、`src/pc_system/mesh_open3d_worker.py`、`workers/open3d_mesh/worker.py`、`tests/test_mesh_worker_contract.py`、`tests/test_mesh_open3d_worker.py`；修改 `pyproject.toml` 增加 `mesh-reconstruction = ["open3d>=0.19,<1"]` 可选依赖。

**工作器契约：**

- 输入 `worker_request.json` 固定来源快照指纹、配置指纹、工作器 schema、资源上限和工作目录内输入点工件；不含可执行路径或 Shell 文本。
- 工作器写入唯一尝试目录，返回 `worker_result.json`，列出原始/修复 PLY、预览 GLB 及指标的固定相对标识、字节数、SHA-256、顶点数、面数、算法和工作器版本。
- Open3D 适配器显式执行所选算法；保守修复仅移除重复/退化元素、小型孤立组件及非流形重复，不做无证据大范围补洞。算法失败不切换。
- 核心通过打开后的普通文件句柄验证大小与 SHA-256，并验证 PLY/GLB/JSON 声明、有限坐标和顶点/面上限；验证期间不信任路径再次解析结果。

- [x] 红灯：确定性假工作器的有效清单；路径穿越、绝对路径、符号链接/重解析点、非普通文件、声明字节/hash 不符、NaN 指标、超限顶点/面、未知工件、算法不一致全部拒绝；额外验证来源点工件被改写、重新计算请求指纹混合算法参数、GLB 头/长度/访问器计数不一致（覆盖最终复审重点 3）。
- [x] 红灯：可选 Open3D 小球面 Poisson 与 BPA 冒烟用例已建立；缺依赖时稳定 `mesh_worker_unavailable`，不得静默改用假工作器或另一算法。有界小组件删除先观察到辅助函数缺失，再实现确定性预算规则。
- [x] 绿灯：实现版本化请求/结果协议、独立工作器入口、受控子进程适配器、Poisson/BPA 显式分支、保守修复、固定 GLB 2.0 预览及核心完整性验证；核心模块不导入 Open3D。
- [x] 验证：工作器契约、Open3D 适配层及配置聚焦集 `34 passed, 2 skipped in 0.65s`；跳过项为当前环境未安装 Open3D 的两算法真实冒烟，缺依赖错误路径已实际通过。定向编译与差异检查通过。
- [x] 提交：`feat: add bounded Open3D mesh worker contract`。

## 任务 4：网格运行、质量门禁与确定性恢复

**文件：** 新增 `src/pc_system/mesh_quality.py`、`src/pc_system/mesh_reconstruction.py`、`tests/test_mesh_quality.py`、`tests/test_mesh_reconstruction.py`。

**运行状态机：** `requested -> running -> quality_checked -> passed|review_required|rejected`；完成响应只在 `commit.json`、全部业务事件和 completed 审计一致后返回。

**质量指标：** 点云到网格 P50/P95/最大距离、覆盖率、边界边、非流形边、退化面、重复顶点、小组件、连通组件、修复前后变化比例、包围盒差和有限性。工作器计算几何统计，核心验证指标字段并按已发布策略决定状态。

**恢复边界：**

- 同一 `run_id` 的来源、配置、策略、actor、request 和幂等键组成所有者信封；任何不一致均冲突。
- 工作器开始前持久化 owner、来源快照、请求和审计开始事件；工作器成功后先验证工件，再发布清单/commit，最后补齐业务事件和 completed 审计。
- 工件完整且 worker 结果已提交时，同一操作重试不得重新执行工作器，只补齐质量、清单或审计；结果是否完整无法证明时返回 `publication_recovery_required`，不删除、不接管、不猜测。

- [x] 红灯：覆盖 passed/review_required/rejected、显式算法失败、来源/配置/策略快照篡改、质量报告发布中断、结果落盘后的审计中断、completed 响应丢失、同操作重放、不同操作接管、无所有者非空目录和已提交工件篡改；额外验证质量报告与工作器原始指标指纹完全一致（覆盖最终复审重点 4）。
- [x] 绿灯：实现内核资源锁、不可变 owner/来源/配置/策略快照、固定工作器请求与工件、质量报告、run manifest、commit 完成边界，以及 `mesh.run_started`、`mesh.worker_completed`、`mesh.quality_checked`、`mesh.run_completed` 事件和安全读取；同一所有者恢复不重复执行工作器。
- [x] 验证：质量、运行恢复、来源与工作器契约聚焦集 `62 passed in 2.23s`；定向编译与差异检查通过。
- [x] 提交：`feat: create recoverable scan-to-mesh runs`。

## 任务 5：专家决定、不可变网格发布与回滚

**文件：** 新增 `src/pc_system/mesh_release.py`、`tests/test_mesh_release.py`。

**审批与发布：**

- `passed` 在策略 `auto_publish_passed == true` 时允许系统主体创建批准证据；否则与 `review_required` 一样要求专家决定。`review_required` 的批准必须确认全部风险码；`rejected` 不提供批准或发布绕过。
- 每个运行只有一份不可变最终决定；驳回必须有原因。发布记录固定 run 清单、决定、来源、配置、策略和网格工件指纹。
- `activate`、`replace`、`rollback` 均创建新发布记录；当前头按 `expected_current_release_id` 比较更新。回滚指向历史已验证发布，不改写历史记录。

- [x] 红灯：覆盖 passed 的自动/人工策略、review_required 缺少风险确认、rejected 强制批准、一次最终决定、错误 expected head、activate/replace/rollback、运行中发布候选恢复、completed 响应丢失，以及已完成但未投影后继阻断旧头（覆盖最终复审重点 5）。
- [x] 绿灯：实现不可变决定与发布 bundle、`mesh.review_recorded`、`mesh.release_created`、`mesh.release_head_changed`、对象级内核锁和 CAS 当前头；回滚创建新记录并指向唯一历史非回滚发布，任何 stale 或未确定状态都不改写当前头。
- [x] 验证：决定/发布、网格运行及既有模型发布聚焦集 `48 passed in 9.39s`；定向编译与差异检查通过。
- [x] 提交：`feat: review and release immutable mesh versions`。

## 任务 6：目录投影、API 与 CLI

**文件：** 新增 `src/pc_system/scan_to_mesh_catalog.py`、`src/pc_system/commands/scan_to_mesh.py`、`tests/test_scan_to_mesh_api.py`、`tests/test_scan_to_mesh_cli.py`；修改 `src/pc_system/api.py`、`src/pc_system/cli.py`、`src/pc_system/cli_parser.py`。

**API：**

```text
GET  /scan-to-mesh/session
GET  /scan-to-mesh/configs
GET  /scan-to-mesh/quality-policies
POST /scan-to-mesh/runs
GET  /scan-to-mesh/runs
GET  /scan-to-mesh/runs/{run_id}
GET  /scan-to-mesh/runs/{run_id}/preview
POST /scan-to-mesh/runs/{run_id}/decision
POST /scan-to-mesh/objects/{object_key}/releases
GET  /scan-to-mesh/objects/{object_key}/releases
```

**CLI：** `mesh-config-publish`、`mesh-policy-publish`、`scan-to-mesh-run`、`scan-to-mesh-list`、`scan-to-mesh-show`、`scan-to-mesh-decide`、`mesh-release`。CLI/API 只传来源身份、配置 ID、策略 ID、操作身份和业务决定。

- [x] 红灯：已覆盖专家写、操作员/审计员裁剪读、未认证拒绝审计；HMAC 游标绑定角色与筛选；重复字段、未知字段、非法来源和不安全 JSON 拒绝；API/CLI 同错误码、同领域投影；响应不含本机路径、工作器日志或完整点集。
- [x] 绿灯：目录层统一实现角色投影与稳定分页；API/CLI 薄适配层只调用任务 2、4、5 领域接口；新增稳定错误码按 400/403/404/409/422/500/503 映射，路由不复制状态机。
- [x] 验证：Task 6 与既有 Phase 15E API/CLI 聚焦回归 `23 passed in 58.85s`；7 个直接变更 Python 文件定向编译与差异检查通过。
- [x] 提交：`feat: expose scan-to-mesh API and CLI`；提交 SHA 以 Git 历史为准。

## 任务 7：专家网格工作台与真实浏览器验收

**文件：** 新增 `frontend/scan-to-mesh.html`、`frontend/scan-to-mesh.js`、`frontend/scan-to-mesh.css`、`tests/browser/test_scan_to_mesh_workbench.py`；修改 `frontend/index.html` 增加入口，必要时只扩展 `tests/browser/conftest.py` 的静态/API 夹具。

**页面范围：** 来源类型和对象选择、配置/策略选择、创建运行、待处理/已处理列表、状态筛选、质量证据、点云/原始网格/修复网格叠加预览、风险确认、批准/驳回、发布/替换/回滚。普通角色只显示业务摘要；专家操作明确二次确认；不实现 CAD 面板。

- [x] 红灯：真实 Chrome 已覆盖 Phase 14 创建运行→review_required→批准→发布、Phase 15E passed 自动发布、rejected 不可批准、待处理/已处理筛选、活动请求禁重复提交、响应不确定后同操作身份重试、审计只读以及页面不泄露本机路径/工作器日志。
- [x] 绿灯：原生页面接入任务 6 API；新增经角色授权和完整性校验的有界点云/原始网格/修复网格预览；自动发布具备确定性决定、发布及中断恢复；页面文本以 `textContent` 安全展示。
- [x] 验证：后端聚焦集 `31 passed`，新增有界预览契约文件复验 `17 passed`，发布/API 最终复验 `15 passed`；Task 7 真实 Chrome 验收 `6 passed`；受影响 Phase 15E 页面回归当时为 `12 passed, 1 deselected`。2026-10-22 复盘确认被排除用例的索引覆盖率错误由 Windows 测试临时路径过长触发，并非生产索引阈值缺陷；`node --check`、定向 `compileall` 与 `git diff --check` 通过。
- [x] 提交：`feat: add expert scan-to-mesh workbench`；提交 SHA 以 Git 历史为准。

## 任务 8：第一里程碑集成验收、中文资料与发布就绪门禁

**文件：** 新增 `tests/test_scan_to_mesh_integration.py`、`docs/scan-to-mesh-milestone1.md`；更新 `README.md`、现有功能盘点文档、`.github/workflows/test.yml` 和本计划验证记录。

**实施前设计补充（2026-10-22）：** 任务 3 已实现外部工作器适配器，但普通 CLI 与 `uvicorn` 启动尚未解析管理员部署配置，导致工程验收通过后仍无法从常规入口创建网格运行。任务 8 增加最小部署接线：仅从进程环境读取一对管理员设置的绝对普通文件路径（Python 可执行文件、固定工作器脚本），构成无 Shell 的固定命令元组；显式注入的测试/宿主工作器优先。两项均未设置时保持 `mesh_worker_unavailable`，只设置一项或路径无效时启动失败。请求体、CLI 业务参数和项目工件不得指定命令；结果仍按既有工件完整性验证。威胁边界是部署管理员可信、业务请求不可信，工作器凭证及二进制更新属于部署管理。本补充不改输入、质量、审计或发布语义。

**验收内容：**

- 工程夹具端到端覆盖 Phase 14 和 Phase 15E 两类来源、Poisson/BPA 显式配置、三态门禁、人工决定、发布、历史和回滚；保存关键上游与发布 SHA 快照，证明旧 Phase 14/15E/模型库工件未被修改。
- CI 核心任务运行假工作器的完整契约和状态机；独立可选任务安装 `mesh-reconstruction` 并执行 Open3D 小型集成测试；浏览器任务执行工作台流程。
- 中文说明明确安装、管理员工作器配置、发布配置/策略、CLI/API 用法、错误码、恢复操作、资源限制、真实精度未验收和第二层尚未实现。

- [x] 聚焦集成：Task 8 新增测试 `6 passed`；独立复审补强旧发布工件 SHA 复验与真实固定子进程入口后，网格受影响模块回归 `104 passed, 2 skipped, 1 warning`。随后安装 Open3D 0.20.0，真实算法小样本单独复验通过。
- [x] 全仓非浏览器门禁：最初的 `model_index_coverage_rejected` 经追踪为 Windows 测试临时路径过长，扫描特征临时文件创建失败后被聚合为覆盖率不足；用短路径复验原失败用例通过，生产阈值和持久化规则未改。短路径首轮唯一失败为本任务遗漏 Phase 15D 文档链接；恢复后聚焦 `2 passed`，安装 Open3D 后最终全仓 `1445 passed, 1 warning`，无跳过。
- [x] 浏览器门禁：本机缺少 Playwright 自带 Chromium，改用已安装 Chrome 和短测试临时路径；完整浏览器集 `25 passed`，包括新增网格工作台全部 6 个场景。
- [x] 可选 Open3D 契约：安装 Open3D 0.20.0 后，小样本真实 Poisson/Ball Pivoting 测试 `5 passed`，无跳过。
- [x] 静态门禁：`compileall`、`node --check frontend/scan-to-mesh.js`、`git diff --check` 通过；中文说明的配置/策略 JSON 经实际构建器校验通过。精确暂存差异待提交前复核。
- [x] 最终复审：独立只读复审未发现生产逻辑严重或重要缺陷；提出两项重要验收证据缺口（旧发布 SHA、真实固定子进程），已各补一项测试并通过聚焦复验。后续追踪进一步澄清：索引覆盖率报错由 Windows 临时路径长度引起，文档链接遗漏属于本任务回归并已修复。
- [x] 提交：`6e07ee9 feat: complete scan-to-mesh milestone one`。2026-10-22 用户已批准在门禁通过后推送、创建 PR 并合并；最终全仓复验已通过，提交文档修订前仍须检查精确暂存差异。

## 计划自检

- [x] 规格覆盖：第一层的可信来源、配置、两种算法、工件、质量三态、审批、发布、回滚、API/CLI/页面、安全、恢复和验收均映射到任务；第二层明确排除。
- [x] 步骤粒度：每项任务都有独立红灯、绿灯、聚焦验证和单一意图提交；全仓测试只在任务 8 执行。
- [x] 类型一致：API、CLI 和页面均调用同一领域接口；来源、配置、策略、运行、决定和发布的身份显式传递，不依赖目录猜测。
- [x] 复审重点：五类高风险失败均指定所属任务和命名测试范围。
- [x] 比例合理：先纯来源/配置，再工作器契约，再持久化状态机，最后暴露入口与全链验收；没有提前实现 CAD 或自动学习。

## 执行记录

基线：`origin/main` 的 `054c9bf`。规格提交：`1f17342`。实施分支：`codex/scan-to-cad-design`，工作树：`.worktrees/phase15e-scanned-reference`。

2026-10-22 状态：任务 1–7 已通过 PR #10 进入 `main`，任务 8 的集成、部署接线、中文资料与 CI 配置已在本地提交 `6e07ee9`。起初在 `main` 也复现的索引覆盖率错误，后经原始异常追踪确认是相同 Windows 长临时路径环境造成；缩短 pytest `--basetemp` 后原用例通过。修复本次遗漏的 Phase 15D 文档链接后，最终后端 `1445 passed`、浏览器 `25 passed`、真实 Open3D 小样本 `5 passed`；均以对应命令的退出码 0 为准。用户已授权通过门禁后推送、创建 PR 与合并，真实工业样本精度验收和第二层 CAD 重建不在本轮。
