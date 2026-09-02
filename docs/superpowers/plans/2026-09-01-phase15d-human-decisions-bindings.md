# Phase 15D 人工决策、不可变模型绑定与双界面实施计划

> **执行要求：** 用户已选择方式 2，在当前会话使用 `superpowers:executing-plans` 按任务推进，不启动子代理。步骤以复选框（`- [ ]`）跟踪。

**目标：** 从 Phase 15C 有效配准报告自动投影人工待办，提供不可变确认/拒绝/无匹配决定、模型绑定替换与恢复，以及业务和专业双页面。

**架构：** 继续使用当前文件持久化、内核资源锁和哈希链审计。`model_match_decision` 负责不可变提交包与写编排，`model_binding` 负责绑定结构和历史链，`model_decision_queue` 负责从 Phase 14/15B-2/15C/15D 权威工件动态投影清单；决定与可选绑定同目录发布，`commit.json` 最后决定可见性。

**技术栈：** Python 3.11+、FastAPI、pytest、现有规范 JSON/no-replace 文件原语、原生 HTML/CSS/JavaScript、Node.js 行为探针、Playwright Python/Chromium 真实浏览器验收。

**规格：** `docs/superpowers/specs/2026-09-01-phase15d-human-decisions-bindings-design.md`

**实施状态：** 2026-09-02 已完成任务 1–10 的本地实施、验证与复审，尚未推送、合并或发布。

## 实施记录与验收证据

| 任务 | 产出 | 本地提交 |
| --- | --- | --- |
| 1 | 决策身份、指纹与权限契约 | `6de237c` |
| 2 | 不可变绑定与历史链 | `3c04792` |
| 3 | 提交包、冻结上下文与可见性 | `7497f21` |
| 4 | 审计编排、决定、替换与恢复 | `f6c72a6` |
| 5 | 自动清单、筛选、分页与角色裁剪 | `b3302fe` |
| 6 | 查询与写入 API | `e10bf7d` |
| 7 | 六个 CLI 命令 | `b31a003` |
| 8 | 业务决策工作台与共享状态模块 | `75fb30d` |
| 9 | 专业工作台、技术详情与版本操作 | `8bc8965` |
| 10 | 浏览器/端到端验收、中文资料、CI 与最终复审修订 | 见本文件所在阶段完成提交 |

验证按比例执行：阶段全仓门禁一次，结果为 **1153 passed、1 skipped、1 warning**（367.36 秒，包含浏览器）。全仓运行后对事项定位失败的恢复规则进一步收紧，最终受影响的决定服务、提交包和 API/CLI 集共 **41 passed**（109.27 秒）；没有重复运行无关全仓测试。先前 Phase 15D 聚焦集为 118 passed；另外补充了失败锁边界和上游对象快照变化回归。

真实页面场景 **6 passed**（53.37 秒）：确认并核对服务端绑定、候选拒绝/无匹配/筛选、专家复核/重新配准/替换/恢复/审计员只读、两个独立用户页面冲突与刷新、空列表/网络失败、加载期间控件禁用。没有伪造业务 API 响应；截图已做视觉核验。

本地新下载 Chromium 启动出现 `spawn UNKNOWN`，验收使用已有 Google Chrome 的独立无界面上下文（`--browser chromium --browser-channel chrome`）；CI 采用 Linux 标准 Chromium，远端执行结果待后续推送验证。编译检查和 `git diff --check` 通过。既有 Starlette/httpx 弃用提示未扩大到本阶段修复。

复审结论：已修复确认的重要问题——加载期间控件可点击但请求被忽略，以及失败终结未完全受 owner/对象锁边界约束。按下节统一不变量收敛恢复语义，未增加数据库、清理事务或新的绑定分支机制。角色、不可变提交、单链接续、重放、崩溃恢复和页面闭环均有测试证据。对象变化测试在上游权威快照读取边界注入新指纹，不把篡改历史文件当作正常更新。

后续事项：Phase 15E 实物参考点云模板；大型项目的清单扫描成本需要真实数据基准评估；跨浏览器会话恢复收件箱未纳入本阶段；不在此阶段实现自动训练、自动推广或统一三维查看器。


## 本次复审问题与验收对应

| 问题 | 实施落点 | 必须补充的证据 |
| --- | --- | --- |
| 锁外审计完成与崩溃后绕过 | 任务 3、4 | commit 后暂停并发测试、owner 后进程退出恢复测试 |
| 重放被最新修订拒绝 | 任务 4 | 完成重放、失败重放、冻结快照恢复 |
| 普通确认新建第二条根链 | 任务 2、4、5 | 跨事项/检索运行、重新待办和陈旧绑定测试 |
| 仅有 case_id 无法选择对象锁 | 任务 3、4、6、7 | 历史事项定位与锁内身份复验 |
| 页面验收缺少真实浏览器 | 任务 8、9、10 | Chromium 实际页面/API 闭环、独立 CI 门禁 |

## 全局约束

### 失败终结边界复核（2026-09-02）

最终复审将失败终结统一为一个不变量：**只有已定位对象、持有对象锁且确认本操作没有 owner 时，才允许写入 failed。** 事项定位失败时，不能把“尚未找到 owner”当作“不存在 owner”；只返回稳定错误并保留 running，待权威证据恢复后使用同一请求重试。owner 已存在的操作只能原位恢复或保持阻断。

该规则沿用已批准的不可变提交包和对象锁，不改变数据结构、权限、绑定链或正常用户流程；它同时覆盖锁外失败终结与重试时定位依赖不可用两个入口。验收由锁持有断言、已有 owner 下定位失败后仍可恢复、原有崩溃/并发/幂等测试共同证明。

- 系统不得自动确认或自动建立生产绑定。
- `operator` 只能确认 `passed`，`expert` 才能确认 `review_required`、重新配准、替换和恢复；`auditor` 只读。
- `rejected` 或 `failed` 配准永远不能确认；候选级拒绝不关闭事项，只有确认或无匹配关闭事项。
- 所有写操作必须绑定可信主体、操作编号、请求编号和幂等键。
- 标识符必须通过既有 `validate_identifier`；只接受普通目录和普通文件，拒绝链接、junction 和重解析点。
- 决定、绑定、owner 和提交清单使用规范 JSON 原始字节与 SHA-256；`commit.json` 最后发布。
- 没有有效提交清单和已完成审计快照的提交包不公开。
- 同一对象写入必须在 `model_resource_lock(project_root, "model-decision", asset_id, source_id, instance_id)` 内重新验证事项修订。
- 对象锁覆盖 owner、决定、绑定、业务事件、commit、审计完成和公开读取验证；扫描包括该对象所有检索运行及未完成提交。别的操作有未完成 owner 时返回 `publication_recovery_required`，不得绕过。
- 已完成重放先返回原结果；有 owner 的未完成操作按冻结历史快照恢复，不重新比较当前证据修订；恢复不能越过非法后继。此锁不提供 Phase 14/15C 跨阶段事务，冻结后上游变化由 `stale`/`pending` 反映。
- 普通确认只有在对象完全没有绑定头时才能 create；有效头返回 `binding_exists`，陈旧头返回 `binding_stale`。替换/恢复接续原链，不新建根链。
- 不创建可修改队列文件或 `current.json`；当前状态从不可变提交投影。
- 不自动删除、移动、隔离或递归清理异常路径。
- 核心测试使用 Phase 15C 确定性引擎，不要求安装 Open3D。
- 浏览器测试使用独立 `browser-test` 依赖组；阶段和 CI 浏览器门禁不得因依赖/Chromium 缺失而 skip。
- 页面采用现有原生前端风格，不引入构建框架；后端权限不能依赖按钮隐藏。
- 项目资料和用户文案以中文为主，稳定错误码、API、CLI 和代码标识符保留英文。
- 按 TDD 运行新增测试、聚焦模块测试，最终就绪时运行一次全仓测试。

---

## 文件结构

### 新增生产文件

- `src/pc_system/model_match_decision.py`：决策请求规范化、候选资格、提交包安全读写、审计写编排、幂等恢复和业务动作。
- `src/pc_system/model_binding.py`：绑定构造、刚性变换复核、绑定链验证、当前/陈旧/替换状态投影。
- `src/pc_system/model_decision_queue.py`：事项身份、证据集合、事项修订、候选级拒绝和分页清单投影。
- `frontend/model-matching-workbench.js`：双页面共享状态标签、权限动作矩阵、筛选和安全文本帮助函数。
- `frontend/model-decisions.html`、`frontend/model-decisions.js`、`frontend/model-decisions.css`：业务决策工作台。
- `frontend/model-matching-lab.html`、`frontend/model-matching-lab.js`、`frontend/model-matching-lab.css`：专业匹配工作台。
- `docs/phase15d-human-decisions-bindings.md`：中文操作、恢复、权限和页面说明。

### 修改生产文件

- `src/pc_system/api.py`：Phase 15D 查询、决定、替换和恢复路由及错误映射。
- `src/pc_system/cli_parser.py`：Phase 15D CLI 参数。
- `src/pc_system/cli.py`：命令分派。
- `src/pc_system/commands/phase15.py`：Phase 15D CLI 适配器。
- `frontend/index.html`：增加两个工作台入口。
- `README.md`、`docs/current-development-inventory.md`、`docs/system-function-module-inventory.md`：阶段完成状态与后续目标。
- `pyproject.toml`：新增 `browser-test` 可选依赖组（`pytest-playwright`、`uvicorn`），不改变生产依赖。
- `.github/workflows/test.yml`：增加真实浏览器验收作业，保留现有后端测试。
- `.gitignore`：忽略 `test-results/` 浏览器失败产物，不忽略测试源文件。

### 新增测试文件

- `tests/phase15d_support.py`：确定性 Phase 15D 场景构造、主体和配准帮助函数。
- `tests/test_phase15d_decision_contracts.py`
- `tests/test_phase15d_binding.py`
- `tests/test_phase15d_decision_store.py`
- `tests/test_phase15d_decisions.py`
- `tests/test_phase15d_queue.py`
- `tests/test_phase15d_cli_api.py`
- `tests/test_phase15d_frontend.py`
- `tests/test_phase15d_e2e.py`
- `tests/test_phase15d_docs.py`
- `tests/browser/conftest.py`：真实同源 HTTP 服务、临时数据及可信测试主体夹具。
- `tests/browser/test_phase15d_workbenches.py`：业务/专业页面真实浏览器闭环。

---

### Task 1：决策身份、证据指纹与权限契约

**文件：**

- Create: `src/pc_system/model_match_decision.py`
- Create: `tests/phase15d_support.py`
- Create: `tests/test_phase15d_decision_contracts.py`

**接口：**

- Consumes: `Principal`、`require_any_role`、`validate_identifier`、Phase 15C `load_model_registration` 返回结构。
- Produces:
  - `compute_case_id(asset_id: str, source_id: str, instance_id: str, object_fingerprint: str, retrieval_run_id: str) -> str`
  - `compute_evidence_fingerprint(registrations: list[dict]) -> str`
  - `compute_case_revision(object_fingerprint: str, evidence_fingerprint: str, decision_head_fingerprint: str | None, binding_head_fingerprint: str | None) -> str`
  - `normalize_decision_request(*, decision_id: str, case_id: str, decision: str, decision_reason: str, verification_scope: str, registration_id: str | None, candidate_rank: int | None, expected_case_revision: str, binding_id: str | None) -> dict`
  - `require_decision_allowed(principal: Principal, *, decision: str, gate_status: str | None, verification_scope: str) -> None`
  - `build_match_decision(*, request: dict, context: dict, operation: dict, principal: Principal, previous_decision_id: str | None) -> dict`

- [x] **Step 1：建立 Phase 15D 测试夹具**

在 `tests/phase15d_support.py` 中复用 `phase15c_support.prepare_phase15c_case`，定义：

```python
from pc_system.model_matching_identity import Principal
from pc_system.model_registration import register_model_candidate
from phase15c_support import DeterministicRegistrationEngine, prepare_phase15c_case

OPERATOR = Principal("operator-a", frozenset({"operator"}), "cli")
EXPERT = Principal("expert-a", frozenset({"expert"}), "cli")
AUDITOR = Principal("auditor-a", frozenset({"auditor"}), "cli")

def publish_registration(project_root, *, sequence=1, mode="passed") -> dict:
    prepared = prepare_phase15c_case(project_root)
    return register_model_candidate(
        project_root,
        registration_id=f"registration-{sequence}",
        candidate_rank=1,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(mode),
        principal=EXPERT,
        operation_id=f"op-registration-{sequence}",
        request_id=f"req-registration-{sequence}",
        idempotency_key=f"idem-registration-{sequence}",
        **prepared,
    )
```

- [x] **Step 2：编写失败的身份、确定性和权限测试**

在 `tests/test_phase15d_decision_contracts.py` 覆盖：

```python
def test_case_and_evidence_fingerprints_are_order_independent():
    one = {"registration_id": "r-1", "report_fingerprint": "a" * 64}
    two = {"registration_id": "r-2", "report_fingerprint": "b" * 64}
    assert compute_evidence_fingerprint([one, two]) == compute_evidence_fingerprint([two, one])

def test_operator_cannot_confirm_review_required():
    with pytest.raises(ModelMatchingError) as captured:
        require_decision_allowed(
            OPERATOR,
            decision="confirmed",
            gate_status="review_required",
            verification_scope="operational_pose",
        )
    assert captured.value.code == "decision_not_allowed"
```

同时断言：`operator` 可确认 `passed`；`expert` 可确认 `review_required` 和 `expert_pose`；任何角色不能确认 `rejected`/`failed`；`auditor` 不能写；`no_match` 不得携带配准；原因超过 1000 个 Unicode 码点或含 NUL 时返回 `decision_reason_invalid`；未知字段、非精确字符串、无效 ID 被拒绝。

- [x] **Step 3：运行红灯测试**

Run:

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_contracts.py --basetemp "$env:TEMP\pc-phase15d-contracts" -p no:cacheprovider
```

Expected: FAIL，缺少 `model_match_decision` 及上述公共函数。

- [x] **Step 4：实现最小纯契约**

使用规范 JSON 字节计算 SHA-256；候选按 `(candidate_rank, registration_id, report_fingerprint)` 排序后计算证据指纹。`normalize_decision_request` 只返回固定字段：

```python
{
    "decision_id": decision_id,
    "case_id": case_id,
    "decision": decision,
    "decision_reason": decision_reason.strip(),
    "verification_scope": verification_scope,
    "registration_id": registration_id,
    "candidate_rank": candidate_rank,
    "expected_case_revision": expected_case_revision,
    "binding_id": binding_id,
}
```

权限矩阵固定为：

```python
if decision == "confirmed":
    if gate_status == "passed":
        require_any_role(principal, {"operator", "expert"})
    elif gate_status == "review_required":
        require_any_role(principal, {"expert"})
    else:
        raise ModelMatchingError("decision_not_allowed", "Registration cannot be confirmed.")
elif decision in {"rejected", "no_match"}:
    require_any_role(principal, {"operator", "expert"})
```

`build_match_decision` 从 `context` 复制 `case_id`、对象/证据指纹、检索运行、候选排名和两个当前头，从 `operation["started_event_at"]` 取得 `decided_at`，从可信 `Principal` 取得 `decided_by`/排序后的 `decider_roles`。它设置 `previous_decision_id` 和 `previous_decision_head_fingerprint`，不得接受调用方覆盖这些字段。

- [x] **Step 5：运行绿灯和既有身份测试**

Run:

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_contracts.py tests/test_phase15a_identity.py --basetemp "$env:TEMP\pc-phase15d-contracts-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 6：提交**

```powershell
git add -- src/pc_system/model_match_decision.py tests/phase15d_support.py tests/test_phase15d_decision_contracts.py
git commit -m "feat: define Phase 15D decision contracts"
```

---

### Task 2：不可变绑定结构与历史链投影

**文件：**

- Create: `src/pc_system/model_binding.py`
- Create: `tests/test_phase15d_binding.py`
- Modify: `src/pc_system/model_match_decision.py`

**接口：**

- Consumes: Task 1 规范请求与 Phase 15C 配准报告；`validate_rigid_transform`。
- Produces:
  - `build_model_binding(*, binding_id: str, decision: dict, registration: dict, transition: str, current_binding: dict | None, restores_binding: dict | None) -> dict`
  - `project_binding_chain(bindings: list[dict], *, current_object_fingerprint: str) -> dict`
  - `binding_head_fingerprint(projection: dict) -> str | None`

- [x] **Step 1：编写失败的绑定结构测试**

覆盖首次绑定复制模型、表达、配准、矩阵和验证范围：

```python
def test_create_binding_copies_only_authoritative_registration_fields():
    binding = build_model_binding(
        binding_id="binding-1",
        decision=confirmed_decision(),
        registration=passed_registration(),
        transition="create",
        current_binding=None,
        restores_binding=None,
    )
    assert binding["registration_id"] == "registration-1"
    assert binding["rigid_transform_4x4"] == passed_registration()["rigid_transform_4x4"]
    assert binding["supersedes_binding_id"] is None
```

同时覆盖非刚性矩阵、跨对象引用、`create` 携带前驱、`supersede` 缺少当前绑定、`restore` 缺少历史目标、循环、分叉和两个当前头。

新增断言：绑定头从对象全部提交投影，不因换检索运行或对象指纹变化而丢失；陈旧头仍是链头，只能由专家显式接续，不能再 create。

- [x] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_binding.py --basetemp "$env:TEMP\pc-phase15d-binding" -p no:cacheprovider
```

Expected: FAIL，模块不存在。

- [x] **Step 3：实现绑定构造与链投影**

`project_binding_chain` 必须：

```python
{
    "current_binding": current_or_none,
    "current_status": "active" | "stale" | None,
    "binding_head_fingerprint": digest_or_none,
    "history": newest_first_entries,
}
```

验证每个 `supersedes_binding_id` 只被一个后继引用；`restore` 同时验证 `restores_binding_id` 属于同一链，但新头仍只通过 `supersedes_binding_id` 连接当前头。对象指纹不等于当前指纹时只投影 `stale`，不修改旧绑定。

- [x] **Step 4：运行绿灯和矩阵回归测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_binding.py tests/test_phase15c_registration_transform.py --basetemp "$env:TEMP\pc-phase15d-binding-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 5：提交**

```powershell
git add -- src/pc_system/model_binding.py src/pc_system/model_match_decision.py tests/test_phase15d_binding.py
git commit -m "feat: add immutable model binding chains"
```

---

### Task 3：决定提交包、安全读取与最后可见性清单

**文件：**

- Modify: `src/pc_system/model_match_decision.py`
- Create: `tests/test_phase15d_decision_store.py`
- Modify: `tests/phase15d_support.py`

**接口：**

- Consumes: Task 1/2 的决定与绑定结构；`model_sampling._canonical_json_bytes`、`model_sampling._publish_exact_json`、审计验证函数。
- Produces:
  - `load_decision_bundle(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, decision_id: str) -> dict`
  - `list_decision_bundles(project_root: Path, *, asset_id: str | None = None, source_id: str | None = None, instance_id: str | None = None) -> list[dict]`
  - `load_decision_context(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str) -> dict`
  - `resolve_decision_case_identity(project_root: Path, case_id: str) -> dict`：返回对象三标识、对象指纹、检索编号；历史工件参与定位。
  - `load_operation_decision_result(project_root: Path, operation: dict) -> dict`：通过已验证 owner/commit 定位操作原提交，不依赖当前事项投影。
  - 私有 `_inspect_object_decision_writes_locked(project_root: Path, *, identity: dict, operation: dict) -> dict | None`：扫描整个对象，返回本操作未完成 owner；其他操作有 owner 则阻断，异常工件失败关闭。
  - 私有 `_prepare_decision_owner_locked(project_root: Path, *, request: dict, context: dict, operation: dict, principal: Principal, transition: str | None, restores_binding: dict | None) -> dict`：冻结规格 9.1 的输入快照并 no-replace 发布 owner。
  - 私有 `_load_frozen_decision_context_locked(project_root: Path, *, owner: dict, operation: dict) -> dict`：复验请求指纹、历史证据和前驱，重建冻结上下文，不要求最新上游指纹等于旧快照。
  - 私有 `_publish_decision_bundle_locked(project_root: Path, *, owner: dict, operation: dict, decision: dict, binding: dict | None) -> dict`：依次发布决定/可选绑定、幂等业务事件、最后 commit；仅供同模块对象锁事务调用。

- [x] **Step 1：编写提交清单红灯测试**

测试提交目录固定为：

```text
reports/model_match_decisions/<asset>/<source>/<instance>/<decision>/
  owner.json
  decision.json
  binding.json     # confirmed only
  commit.json      # last
```

断言：没有 `commit.json` 不枚举；确认缺少 `binding.json` 不公开；拒绝/无匹配出现额外绑定文件失败；owner、原始字节哈希或审计引用篡改返回 `artifact_integrity_failed`；符号链接、junction、文件代替目录被拒绝。

另测公开读取与写入检查不同：未公开的 owner 必须仍阻断不同 `decision_id`/检索运行的写入；空目录不阻断但不清理；工件无 owner、失败审计有 owner、多个未完成 owner 均失败关闭。定位测试覆盖事项不再 pending、对象已更新以及伪造 case_id。`case_id` 是 SHA-256，不允许切割字符串反解标识。

- [x] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_store.py --basetemp "$env:TEMP\pc-phase15d-store" -p no:cacheprovider
```

Expected: FAIL，读取/发布接口不存在。

- [x] **Step 3：实现严格读取和规范提交包**

固定发布顺序：

下列第一步在 `_prepare_decision_owner_locked` 执行；其余步骤在 `_publish_decision_bundle_locked` 执行。所有步骤及之后的审计完成均在同一对象锁内。owner 的精确字段见规格 9.1；请求哈希与审计一致，冻结证据及前驱重算后须等于请求修订。不得仅从 owner 复制未经验证的矩阵或主体。

```python
_publish_exact_json(
    directory / "owner.json", owner,
    conflict_code="operation_busy",
    conflict_message="Decision bundle owner conflicts.",
)
_publish_exact_json(
    directory / "decision.json", decision,
    conflict_code="artifact_integrity_failed",
    conflict_message="Decision artifact conflicts.",
)
if binding is not None:
    _publish_exact_json(
        directory / "binding.json", binding,
        conflict_code="artifact_integrity_failed",
        conflict_message="Binding artifact conflicts.",
    )
# 依据决定/绑定动作调用 ensure_operation_event；事件均绑定 owner_sha256。
# 收集返回事件的哈希后，构造包含这些哈希及工件摘要的 commit。
_publish_exact_json(
    directory / "commit.json", commit,
    conflict_code="artifact_integrity_failed",
    conflict_message="Decision commit conflicts.",
)
```

`commit` 必须绑定 `owner_sha256`、`decision_sha256`、可空 `binding_sha256`、`case_id`、`object_fingerprint`、`evidence_fingerprint`、`operation_id`、业务审计事件哈希和 `result_fingerprint`。读取使用 `lstat`/`fstat` 或现有严格原语验证普通文件、路径身份、重复键和规范原始字节。

`load_decision_context` 聚合当前对象指纹、同一检索运行的有效 Phase 15C 配准、有效决定提交包和绑定链，返回 `case_id`、`evidence_fingerprint`、两个头指纹及 `case_revision`。它不执行分页或角色字段裁剪，供 Task 4 锁内重检和 Task 5 队列投影共同复用。

其中 `current_binding` 和 `binding_status` 来自该对象全部事项的绑定链，状态为 `active`、`stale` 或空；`decision_head_id` 来自当前事项。定位器只提供加锁身份，`load_decision_context` 必须在锁内确认该身份重新计算得到请求的 `case_id`，不一致即拒绝。

在本任务加入后续业务测试共用的实际夹具，避免任务 4 引用未定义帮助函数：

```python
from types import SimpleNamespace
from pc_system.model_match_decision import load_decision_context

def prepare_decision_case(project_root, *, mode="passed"):
    report = publish_registration(project_root, mode=mode)
    identity = {key: report[key] for key in (
        "asset_id", "source_id", "instance_id", "retrieval_run_id"
    )}
    context = load_decision_context(project_root, **identity)
    return SimpleNamespace(identity=identity, request_fields={
        "case_id": context["case_id"],
        "registration_id": report["registration_id"],
        "candidate_rank": report["candidate_rank"],
        "expected_case_revision": context["case_revision"],
    })
```

- [x] **Step 4：实现审计完成绑定**

公开读取调用 `read_verified_operation_snapshot`，要求：

```python
snapshot["operation"]["status"] == "completed"
snapshot["operation"]["result"]["result_fingerprint"] == commit["result_fingerprint"]
```

且提交清单中业务事件哈希能在已验证事件链中精确找到。

- [x] **Step 5：运行绿灯和 Phase 15C 工件回归**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_store.py tests/test_phase15c_registration.py -k "published or bundle or audited or integrity" --basetemp "$env:TEMP\pc-phase15d-store-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 6：提交**

```powershell
git add -- src/pc_system/model_match_decision.py tests/test_phase15d_decision_store.py tests/phase15d_support.py
git commit -m "feat: publish immutable decision bundles"
```

---

### Task 4：可审计决定、绑定创建、替换与恢复编排

**文件：**

- Modify: `src/pc_system/model_match_decision.py`
- Modify: `src/pc_system/model_binding.py`
- Create: `tests/test_phase15d_decisions.py`

**接口：**

- Consumes: Tasks 1–3、`start_operation`、`ensure_operation_event`、`complete_operation`、`fail_operation`、`model_resource_lock`。
- Produces:
  - `decide_model_match(project_root: Path, *, decision_id: str, case_id: str, decision: str, decision_reason: str, verification_scope: str, registration_id: str | None, candidate_rank: int | None, expected_case_revision: str, binding_id: str | None, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`
  - `supersede_model_binding(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str, current_binding_id: str, registration_id: str, candidate_rank: int, decision_id: str, binding_id: str, decision_reason: str, verification_scope: str, expected_case_revision: str, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`
  - `restore_model_binding(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str, current_binding_id: str, restores_binding_id: str, decision_id: str, binding_id: str, decision_reason: str, verification_scope: str, expected_case_revision: str, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`

- [x] **Step 1：编写决定与审计红灯测试**

核心成功测试：

```python
def test_confirm_passed_creates_one_audited_decision_and_binding(tmp_path):
    case = prepare_decision_case(tmp_path, mode="passed")
    result = decide_model_match(
        tmp_path,
        decision_id="decision-1",
        binding_id="binding-1",
        decision="confirmed",
        decision_reason="现场铭牌与尺寸一致",
        verification_scope="operational_pose",
        principal=OPERATOR,
        operation_id="op-decision-1",
        request_id="req-decision-1",
        idempotency_key="idem-decision-1",
        **case.request_fields,
    )
    assert result["decision"]["decision"] == "confirmed"
    assert result["binding"]["binding_id"] == "binding-1"
```

覆盖 `review_required` 专家确认、候选拒绝仍待办、无匹配无绑定、`rejected` 禁止确认、对象/模型/配准陈旧、同幂等重放、不同请求冲突、事件结构和完成结果。

- [x] **Step 2：编写并发和故障注入红灯测试**

使用两个线程基于同一 `expected_case_revision` 提交不同决定，断言一个成功、一个 `decision_conflict`。对 owner、decision、binding、业务事件、commit、complete 六个边界注入一次异常：相同请求重试原位完成，事件不重复；不同操作不得接管。

使用事件屏障暂停在 commit 已落盘而 complete 尚未执行的间隙：第二线程此时不能成功；第一线程完成释放锁后第二线程得到 `decision_conflict`。若锁等待超时，可先返回既有锁忙错误，但再次提交必须冲突。另用子进程在 owner/commit 发布后退出，证明内核锁释放后不同决定编号仍得到 `publication_recovery_required`；不得用 sleep 随机碰竞态。

重放矩阵：成功但响应丢失返回同一结果指纹；已失败返回原错误；运行中无 owner 重新校验；有 owner 时即使新增 Phase 15C 证据也按旧快照恢复，再投影 pending；出现非法后继失败关闭；同请求并发须在锁内重读状态，不产生第二提交。

绑定保护矩阵：首次确认成功；重新待办、新检索运行、更换 binding_id 均不能另建根链；有效头返回 `binding_exists`，陈旧头返回 `binding_stale`，专家显式替换才能接续。

- [x] **Step 3：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decisions.py --basetemp "$env:TEMP\pc-phase15d-decisions" -p no:cacheprovider
```

Expected: FAIL，业务编排函数不存在。

- [x] **Step 4：实现对象锁内检查与提交**

先用任务 1 的 `normalize_decision_request` 构造 `request`，令 `root = Path(project_root)`。统一定位器从 case_id 取得对象身份，不新增调用方可覆盖的对象字段。分支顺序固定如下：

```python
operation, replayed = start_operation(
    root, operation_id=operation_id, operation_type="model_match.decision",
    principal=principal, request_id=request_id,
    idempotency_key=idempotency_key, request_payload=request,
)
if operation["status"] == "completed":
    return load_operation_decision_result(root, operation)
if operation["status"] == "failed":
    error = operation["error"]
    raise ModelMatchingError(error["code"], error["message"])
identity = resolve_decision_case_identity(root, case_id)
asset_id, source_id, instance_id = (
    identity["asset_id"], identity["source_id"], identity["instance_id"]
)
with model_resource_lock(root, "model-decision", asset_id, source_id, instance_id):
    snapshot = read_verified_operation_snapshot(root, operation_id)
    operation = snapshot["operation"]
    if operation["status"] == "completed":
        return load_operation_decision_result(root, operation)
    if operation["status"] == "failed":
        error = operation["error"]
        raise ModelMatchingError(error["code"], error["message"])
    operation_context = {
        **operation, "started_event_at": snapshot["events"][0]["timestamp"],
    }
    owner = _inspect_object_decision_writes_locked(
        root, identity=identity, operation=operation_context,
    )
    if owner is None:
        current = load_decision_context(
            root, asset_id=asset_id, source_id=source_id, instance_id=instance_id,
            retrieval_run_id=identity["retrieval_run_id"],
        )
        if current["case_id"] != case_id or current["case_revision"] != expected_case_revision:
            raise ModelMatchingError("decision_conflict", "Decision item changed; refresh required.")
        registration = (
            None if decision == "no_match"
            else current["registrations_by_id"].get(registration_id)
        )
        if decision != "no_match" and registration is None:
            raise ModelMatchingError("registration_not_eligible", "Registration is not eligible.")
        require_decision_allowed(
            principal, decision=decision, verification_scope=verification_scope,
            gate_status=None if registration is None else registration["gate_status"],
        )
        if decision == "confirmed" and binding_id is None:
            raise ModelMatchingError("decision_not_allowed", "Confirmed decision requires a binding ID.")
        if decision == "confirmed" and current["current_binding"] is not None:
            code = "binding_stale" if current["binding_status"] == "stale" else "binding_exists"
            raise ModelMatchingError(code, "Existing binding requires an explicit expert transition.")
        owner = _prepare_decision_owner_locked(
            root, request=request, context=current, operation=operation_context,
            principal=principal, transition="create" if decision == "confirmed" else None,
            restores_binding=None,
        )
    return _resume_decision_locked(
        root, owner=owner, operation=operation_context, principal=principal,
    )
```

定义私有 `_resume_decision_locked(project_root: Path, *, owner: dict, operation: dict, principal: Principal) -> dict`，只允许持对象锁调用。冻结上下文必须返回 `request`、`registrations_by_id`、`decision_head_id`、`current_binding` 和 `restores_binding`；复验本次主体与原审计主体一致，使用原开始时间。核心实现：

```python
root = Path(project_root)
context = _load_frozen_decision_context_locked(root, owner=owner, operation=operation)
request = context["request"]
decision_payload = build_match_decision(
    request=request, context=context, operation=operation, principal=principal,
    previous_decision_id=context["decision_head_id"],
)
binding_payload = None
if request["decision"] == "confirmed":
    binding_payload = build_model_binding(
        binding_id=request["binding_id"], decision=decision_payload,
        registration=context["registrations_by_id"][request["registration_id"]],
        transition=owner["transition"], current_binding=context["current_binding"],
        restores_binding=context["restores_binding"],
    )
committed = _publish_decision_bundle_locked(
    root, owner=owner, operation=operation,
    decision=decision_payload, binding=binding_payload,
)
complete_operation(root, operation["operation_id"], {
    "result_fingerprint": committed["result_fingerprint"],
})
return load_decision_bundle(
    root, asset_id=decision_payload["asset_id"],
    source_id=decision_payload["source_id"], instance_id=decision_payload["instance_id"],
    decision_id=decision_payload["decision_id"],
)
```

异常处理以 owner 是否实际存在为分界：`operation_busy`、恢复阻断保持运行状态；确定尚无 owner 的领域拒绝使用 `fail_operation` 固化错误；owner 已发布后任何失败都不得终结为普通 failed。落盘结果不确定时在锁内检查 owner/commit 后分流；完整性错误立即停止，不自动修复损坏输入。审计已完成但响应丢失以原提交结果为准。

- [x] **Step 5：实现替换与恢复**

两个函数都只允许 `expert`，在同一对象锁内重新加载当前绑定链。`supersede` 要求 `current_binding_id` 等于当前头；`restore` 还要求 `restores_binding_id` 是同一历史链成员。两者均创建新的 `confirmed` 决定和绑定，不修改旧文件。

两者复用相同的操作状态分支、对象未完成提交检查和 `_resume_decision_locked`，不得另写锁外 complete 路径。owner 的 `transition` 分别为 `supersede`/`restore`；恢复沿用历史目标的配准及检索运行，须匹配请求检索运行和当前对象指纹，否则返回 `registration_not_eligible`。目标及实际配准冻结后不得随当前头变化重新选择。

- [x] **Step 6：运行绿灯和审计/锁聚焦集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decisions.py tests/test_phase15a_audit.py tests/test_phase15b1_resource_lock.py --basetemp "$env:TEMP\pc-phase15d-decisions-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 7：提交**

```powershell
git add -- src/pc_system/model_match_decision.py src/pc_system/model_binding.py tests/test_phase15d_decisions.py
git commit -m "feat: orchestrate audited model decisions"
```

---

### Task 5：自动待办、已处理、陈旧与分页投影

**文件：**

- Create: `src/pc_system/model_decision_queue.py`
- Create: `tests/test_phase15d_queue.py`
- Modify: `tests/phase15d_support.py`

**接口：**

- Consumes: Phase 15C 公共报告读取、Task 3 提交包读取、Task 2 绑定链投影。
- Produces:
  - `list_model_decision_items(project_root: Path, *, principal: Principal, status: str = "all", asset_id: str | None = None, class_id: str | None = None, gate_status: str | None = None, decided_by: str | None = None, started_at: str | None = None, ended_at: str | None = None, limit: int = 50, cursor: str | None = None) -> dict`
  - `load_model_decision_item(project_root: Path, *, case_id: str, principal: Principal) -> dict`
  - `project_current_decision_state(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str, principal: Principal) -> dict`

- [x] **Step 1：编写队列红灯测试**

覆盖：

```python
assert list_items(status="pending")["items"][0]["case_id"] == case_id
assert decide_confirmed_then_list(status="processed")["items"][0]["status"] == "processed"
assert add_registration_to_same_case()["status"] == "pending"
assert mutate_object_release_then_list()["items"][0]["status"] == "stale"
```

还要覆盖候选级拒绝保持 `pending`、全部门禁 `rejected` 只允许 `rerun/no_match`、稳定排序、1–100 `limit`、不透明游标、篡改失败关闭和角色字段裁剪。

- [x] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_queue.py --basetemp "$env:TEMP\pc-phase15d-queue" -p no:cacheprovider
```

Expected: FAIL，队列模块不存在。

- [x] **Step 3：实现事项聚合与修订**

按 `(asset_id, source_id, instance_id, object_fingerprint, retrieval_run_id)` 分组 Phase 15C 报告；只接受 `status=completed` 和三态门禁报告。候选证据指纹使用 Task 1 函数；当前决定/绑定头来自有效提交包；事项修订组合四类指纹。

返回项至少包含：

```python
{
    "case_id": case_id,
    "status": "pending",
    "case_revision": revision,
    "object": {
        "asset_id": asset_id,
        "source_id": source_id,
        "instance_id": instance_id,
        "object_fingerprint": object_fingerprint,
    },
    "candidate_summary": [
        {
            "registration_id": "registration-1",
            "candidate_rank": 1,
            "gate_status": "passed",
        }
    ],
    "decision_summary": None,
    "binding_summary": None,
    "available_actions": ["confirm", "reject", "no_match"],
}
```

`expert`/`auditor` 详情增加 `technical`，`operator` 响应不得包含完整矩阵和原始审计事件。

- [x] **Step 4：实现游标与筛选**

游标编码最后一项的规范排序键和筛选指纹；不同筛选复用游标返回 `decision_conflict` 或稳定无效请求错误。先过滤再截断，`next_cursor` 只在仍有更多结果时返回。

- [x] **Step 5：运行绿灯和 Phase 15C 聚焦集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_queue.py tests/test_phase15c_registration.py tests/test_phase15c_e2e.py --basetemp "$env:TEMP\pc-phase15d-queue-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 6：提交**

```powershell
git add -- src/pc_system/model_decision_queue.py tests/phase15d_support.py tests/test_phase15d_queue.py
git commit -m "feat: project model decision queues"
```

---

### Task 6：Phase 15D API 与稳定错误映射

**文件：**

- Modify: `src/pc_system/api.py`
- Create: `tests/test_phase15d_cli_api.py`

**接口：**

- Consumes: Tasks 4/5 公共领域函数和既有 `_phase15_json_object`、`_require_payload_shape`、可信主体解析。
- Produces: 规格第 14 节全部 API。

- [x] **Step 1：编写 API 红灯测试**

使用以下应用注册 operator/expert/auditor token：

```python
client = TestClient(
    create_app(
        tmp_path,
        run_mode="production",
        api_key="phase15d-test-service-key",
        principal_bindings={
            "operator-token": {"actor_id": "operator-a", "roles": ["operator"]},
            "expert-token": {"actor_id": "expert-a", "roles": ["expert"]},
            "auditor-token": {"actor_id": "auditor-a", "roles": ["auditor"]},
        },
    )
)
```

覆盖：

```python
assert client.get("/model-matching/decision-items?status=pending", headers=operator).status_code == 200
assert client.post("/model-matching/decisions", json=confirmed_payload, headers=operator).status_code == 201
assert client.post(f"/model-matching/bindings/{binding_id}/restore", json=restore_payload, headers=operator).status_code == 403
```

证明写路由在读取请求体前完成授权：无权 token 搭配超限或无效 JSON 仍返回 `permission_denied`。

- [x] **Step 2：运行 API 红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py -k "api" --basetemp "$env:TEMP\pc-phase15d-api" -p no:cacheprovider
```

Expected: FAIL，路由不存在。

- [x] **Step 3：实现查询路由**

新增：

```text
GET /model-matching/decision-items
GET /model-matching/decision-items/{case_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}/history
```

允许 `operator`、`expert`、`auditor`，把可信 `Principal` 传入领域层进行字段裁剪。

- [x] **Step 4：实现写路由**

新增：

```text
POST /model-matching/decisions
POST /model-matching/bindings/{binding_id}/supersede
POST /model-matching/bindings/{binding_id}/restore
```

决定允许 `operator`/`expert`，替换和恢复仅 `expert`。请求体使用精确字段集合；可空 `registration_id`、`binding_id` 仍需精确类型检查。

- [x] **Step 5：加入稳定错误映射并运行绿灯**

映射：权限 403；not found 404；`decision_conflict`、`binding_exists`、`binding_stale`、`object_fingerprint_stale`、`artifact_integrity_failed` 为 409；输入和资格错误 400；审计/发布恢复 503。

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py -k "api" tests/test_phase15a_api.py --basetemp "$env:TEMP\pc-phase15d-api-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 6：提交**

```powershell
git add -- src/pc_system/api.py tests/test_phase15d_cli_api.py
git commit -m "feat: expose Phase 15D decision APIs"
```

---

### Task 7：Phase 15D CLI

**文件：**

- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Modify: `src/pc_system/commands/phase15.py`
- Modify: `tests/test_phase15d_cli_api.py`

**接口：**

- Consumes: Tasks 4/5 公共领域函数。
- Produces: 规格第 15 节六个 CLI 命令。

- [x] **Step 1：编写 CLI 红灯测试**

分别调用 `main(arguments)` 并解析 stdout JSON；例如清单命令使用：

```python
arguments = [
    "list-model-decision-items",
    "--project-root", str(tmp_path),
    "--status", "pending",
    "--actor", "operator-a",
]
assert main(arguments) == 0
```

覆盖：

```text
list-model-decision-items
show-model-decision-item
decide-model-match
list-model-bindings
supersede-model-binding
restore-model-binding
```

`decide-model-match` 必须通过 `--decision confirmed|rejected|no_match`、`--verification-scope`、`--expected-case-revision`、可选 `--registration-id`/`--candidate-rank`/`--binding-id` 和审计参数表达完整请求。

- [x] **Step 2：运行 CLI 红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py -k "cli" --basetemp "$env:TEMP\pc-phase15d-cli" -p no:cacheprovider
```

Expected: FAIL，解析器不识别命令。

- [x] **Step 3：实现解析器、命令适配器和分派**

CLI 写命令构造：

```python
Principal(actor, frozenset({"operator"}), "cli")
```

`decide-model-match` 默认业务角色为 `operator`，增加 `--expert` 时改为 `expert`；替换和恢复始终构造 `expert`。不得从输入 JSON 接受角色集合。

- [x] **Step 4：运行绿灯和旧 Phase 15 CLI 回归**

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py tests/test_phase15c_cli_api.py tests/test_phase15b2_cli_api.py --basetemp "$env:TEMP\pc-phase15d-cli-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 5：提交**

```powershell
git add -- src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/commands/phase15.py tests/test_phase15d_cli_api.py
git commit -m "feat: add Phase 15D decision CLI"
```

---

### Task 8：共享前端状态模块与业务决策工作台

**文件：**

- Create: `frontend/model-matching-workbench.js`
- Create: `frontend/model-decisions.html`
- Create: `frontend/model-decisions.js`
- Create: `frontend/model-decisions.css`
- Modify: `frontend/index.html`
- Create: `tests/test_phase15d_frontend.py`

**接口：**

- Consumes: Task 6 查询/决定 API。
- Produces: CommonJS 和浏览器双用 `window.modelMatchingWorkbench`：
  - `buildListViewModel(response, role) -> object`
  - `availableActions(item, role) -> string[]`
  - `buildDecisionPayload(item, form) -> object`
  - `statusLabel(status) -> string`

- [x] **Step 1：编写 HTML 与 Node 红灯测试**

断言业务页包含：`pending`、`processed`、`all` 页签，筛选区，列表区，候选区，原因输入，确认/拒绝/无匹配按钮，加载/空/错误/冲突提示容器。Node 探针：

```javascript
const item = {status:"pending", candidates:[{gate_status:"passed"}]};
console.log(JSON.stringify(m.availableActions(item, "operator")));
```

期望包含 `confirm`、`reject`、`no_match`，不包含 `rerun`、`supersede`、`restore`。

- [x] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py -k "business or shared" --basetemp "$env:TEMP\pc-phase15d-frontend-business" -p no:cacheprovider
```

Expected: FAIL，页面和共享模块不存在。

- [x] **Step 3：实现共享纯函数**

使用 UMD 风格导出，所有用户文本通过 `textContent` 或转义函数插入。动作矩阵必须以服务端 `available_actions` 为上限，再叠加角色 UI 限制；不得仅根据 `gate_status` 扩大权限。

- [x] **Step 4：实现业务页面**

页面 API 地址默认使用当前同源地址，也可读取 URL 参数中的显式 API 地址；开发主体仅用于开发模式。页面调用列表/详情/决定 API，提交携带当前 `case_revision`；收到 `decision_conflict` 时禁用旧按钮、显示“记录已被其他用户处理，请刷新”，并提供刷新按钮。

生产模式不使用 URL 中的开发主体授权；认证凭据随请求头发送，不放进 URL。为浏览器验收固定可访问名称和 `data-testid`：`decision-row`、`case-status`、`decision-reason`、`binding-id`；操作按钮使用中文名称“确认”“拒绝”“无匹配”“刷新”。任务 10 的浏览器夹具提供独立主体上下文，不在页面源码写死测试凭据。

候选级拒绝后保持当前事项并重新加载；确认或无匹配成功后切换到已处理详情。业务页不渲染完整矩阵和原始审计事件。

- [x] **Step 5：运行绿灯和 Phase 14 前端回归**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py -k "business or shared" tests/test_phase14_correction_frontend.py --basetemp "$env:TEMP\pc-phase15d-frontend-business-green" -p no:cacheprovider
```

Expected: PASS；Node 不可用时仅相关行为探针 skip。

- [x] **Step 6：提交**

```powershell
git add -- frontend/model-matching-workbench.js frontend/model-decisions.html frontend/model-decisions.js frontend/model-decisions.css frontend/index.html tests/test_phase15d_frontend.py
git commit -m "feat: add model decision workbench"
```

---

### Task 9：专业匹配工作台

**文件：**

- Create: `frontend/model-matching-lab.html`
- Create: `frontend/model-matching-lab.js`
- Create: `frontend/model-matching-lab.css`
- Modify: `frontend/model-matching-workbench.js`
- Modify: `frontend/index.html`
- Modify: `tests/test_phase15d_frontend.py`

**接口：**

- Consumes: Task 6 API、Task 8 共享模块、Phase 15C 注册 API。
- Produces: 专家/审计员专业详情、重新配准、替换和恢复页面流程。

- [x] **Step 1：编写专业页面红灯测试**

HTML 必须包含候选解释、配准配置、引擎、矩阵、覆盖率、残差、尺寸、门禁原因、决策历史、绑定链和审计区域，以及重新配准、替换、恢复控件。

Node 探针断言：`expert` 在服务端允许时看到 `rerun/supersede/restore`；`auditor` 的动作数组为空；矩阵只接受 4×4 有限数字结构后进入视图模型。

- [x] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py -k "professional or auditor" --basetemp "$env:TEMP\pc-phase15d-frontend-lab" -p no:cacheprovider
```

Expected: FAIL，专业页面不存在。

- [x] **Step 3：实现专业详情与只读模式**

`model-matching-lab.js` 根据可信 API 返回的角色/动作渲染。`auditor` 始终只读。完整矩阵使用表格展示，指标明确单位；审计区域显示 operation ID、状态和事件摘要，不渲染未转义 JSON。

- [x] **Step 4：实现专家动作**

- 重新配准：选择已有发布配置，调用 `/model-matching/registrations`；成功后刷新事项修订。
- 替换：要求选择新配准和填写原因，调用 `/supersede`。
- 恢复：选择历史绑定，展示“将创建新版本，不修改旧绑定”，调用 `/restore`。
- 所有动作处理 loading、稳定错误和 `decision_conflict`。

- [x] **Step 5：运行绿灯和完整前端集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py tests/test_frontend_workbench.py tests/test_frontend_api_and_embed.py tests/test_phase14_correction_frontend.py --basetemp "$env:TEMP\pc-phase15d-frontend-green" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 6：提交**

```powershell
git add -- frontend/model-matching-lab.html frontend/model-matching-lab.js frontend/model-matching-lab.css frontend/model-matching-workbench.js frontend/index.html tests/test_phase15d_frontend.py
git commit -m "feat: add professional model matching lab"
```

---

### Task 10：端到端闭环、中文资料与阶段门禁

**文件：**

- Create: `tests/test_phase15d_e2e.py`
- Create: `tests/test_phase15d_docs.py`
- Create: `docs/phase15d-human-decisions-bindings.md`
- Modify: `README.md`
- Modify: `docs/current-development-inventory.md`
- Modify: `docs/system-function-module-inventory.md`
- Create: `tests/browser/conftest.py`
- Create: `tests/browser/test_phase15d_workbenches.py`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/test.yml`
- Modify: `.gitignore`

**接口：**

- Consumes: Tasks 1–9。
- Produces: Phase 14 → 15B-2 → 15C → 15D 闭环、用户操作资料和阶段完成证据。

- [x] **Step 1：编写端到端红灯测试**

复用任务 3 已定义的 `prepare_decision_case`，测试文件显式导入 `OPERATOR`、`decide_model_match` 和 `load_model_decision_item`。

通过真实领域服务/API 和确定性配准引擎构造确认及重放闭环：

```python
def test_phase15d_e2e_confirm_and_replay(tmp_path):
    case = prepare_decision_case(tmp_path)
    request = dict(
        case.request_fields,
        decision_id="decision-e2e", binding_id="binding-e2e",
        decision="confirmed", decision_reason="现场身份核对一致",
        verification_scope="operational_pose", principal=OPERATOR,
        operation_id="op-e2e", request_id="req-e2e", idempotency_key="idem-e2e",
    )
    first = decide_model_match(tmp_path, **request)
    second = decide_model_match(tmp_path, **request)
    assert second == first
    item = load_model_decision_item(tmp_path, case_id=case.request_fields["case_id"], principal=OPERATOR)
    assert item["status"] == "processed"
    assert first["binding"]["binding_id"] == "binding-e2e"
```

另测专家新配准并替换后旧绑定被接续、恢复生成第三个版本、对象更新后当前头 stale；`review_required` 只能专家确认、候选拒绝仍 pending、无匹配不创建绑定、并发冲突。各提交均读取已验证审计快照；页面测试见下节，不以 TestClient 代替浏览器。

#### 真实浏览器验收的固定实施步骤

在 `pyproject.toml` 的可选依赖中增加 `browser-test = ["pytest-playwright", "uvicorn"]`。`tests/browser/conftest.py` 提供函数级 `browser_server` 夹具：

1. 在 pytest 临时项目使用 `prepare_decision_case` 创建数据；调用真实 `create_app`，传入 `run_mode="production"`、测试 `api_key`、operator/expert/auditor 的 `principal_bindings` 和确定性 `registration_engine_resolver`。
2. 将仓库 `frontend` 用 FastAPI `StaticFiles` 挂载到 `/workbench`；该挂载只属于测试夹具，不改变生产 API 架构。
3. 用预先绑定 `127.0.0.1:0` 的 socket 和 `uvicorn.Server` 在线程中运行 HTTP 服务，最多 10 秒检查启动就绪；yield 包含 `url`、`project_root` 和 `case_id` 的字典。finally 请求退出、限时 join 并关闭 socket，停止失败必须报错。
4. 每项测试用 `browser.new_context(extra_http_headers={"Authorization": "Bearer operator-token"})` 或相应专家/审计员 token 创建独立上下文。真实请求到上述服务，不拦截/伪造业务 API 响应；测试 token 不用于真实环境。

最小页面闭环代码放入 `tests/browser/test_phase15d_workbenches.py`：

```python
from playwright.sync_api import expect

def test_operator_confirms_from_real_page(browser, browser_server):
    context = browser.new_context(extra_http_headers={"Authorization": "Bearer operator-token"})
    try:
        page = context.new_page()
        page.goto(browser_server["url"] + "/workbench/model-decisions.html")
        page.get_by_test_id("decision-row").first.click()
        page.get_by_test_id("decision-reason").fill("现场核验一致")
        with page.expect_response(lambda r: r.url.endswith("/model-matching/decisions") and r.request.method == "POST") as pending:
            page.get_by_role("button", name="确认", exact=True).click()
        assert pending.value.status == 201
        expect(page.get_by_test_id("case-status")).to_have_text("已处理")
        expect(page.get_by_test_id("binding-id")).not_to_be_empty()
    finally:
        context.close()
```

必须补齐：业务三个页签/筛选/拒绝仍待办/无匹配；专家 review_required 确认、真实重新配准、替换、历史恢复；审计员只读；两个独立上下文先读同一修订，先提交成功、后提交收到 409 并显示刷新提示；空列表、加载及服务失败状态。断言 `pageerror` 为空，校验页面展示与后端工件/绑定链一致，不只检查元素存在。

Windows 本地命令：

```powershell
uv run --extra test --extra browser-test playwright install chromium
uv run --extra test --extra browser-test pytest -q tests/browser --browser chromium --tracing retain-on-failure --screenshot only-on-failure --output "$env:TEMP\pc-phase15d-browser-results" -p no:cacheprovider
```

在 `.github/workflows/test.yml` 保留原后端作业，但将其测试命令改为 `python -m pytest tests --ignore=tests/browser -q -p no:cacheprovider`；新增 `browser` 作业，复用现有 checkout/setup-python 版本，执行：

```yaml
- run: python -m pip install -e ".[test,browser-test]"
- run: python -m playwright install --with-deps chromium
- run: python -m pytest tests/browser -q --browser chromium --tracing retain-on-failure --screenshot only-on-failure --output test-results -p no:cacheprovider
```

失败时上传 `test-results/`，产物目录加入 `.gitignore`。不使用 `continue-on-error`、`importorskip` 或捕获浏览器启动异常后跳过；缺少依赖/浏览器即本门禁失败。日常后端聚焦测试不触发浏览器安装，阶段完成必须有 Chromium 运行证据。安装与 CI 命令依据 [Playwright Python 安装说明](https://playwright.dev/python/docs/intro) 和 [CI 说明](https://playwright.dev/python/docs/ci)；这里只采用测试能力，不引入前端框架。

- [x] **Step 2：运行端到端红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_e2e.py --basetemp "$env:TEMP\pc-phase15d-e2e" -p no:cacheprovider
```

Expected: 如存在集成缺口则 FAIL；只修复 Phase 15D 范围内缺口。

- [x] **Step 3：补齐集成并运行 Phase 15D 聚焦集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_contracts.py tests/test_phase15d_binding.py tests/test_phase15d_decision_store.py tests/test_phase15d_decisions.py tests/test_phase15d_queue.py tests/test_phase15d_cli_api.py tests/test_phase15d_frontend.py tests/test_phase15d_e2e.py --basetemp "$env:TEMP\pc-phase15d-focused" -p no:cacheprovider
```

Expected: PASS。

- [x] **Step 4：编写中文操作资料和资料测试**

`docs/phase15d-human-decisions-bindings.md` 必须说明：

- 三种角色和两类页面。
- 待处理/已处理/全部/陈旧状态。
- 确认、候选拒绝、无匹配、重新配准、替换和恢复。
- 多人同时查看和第一提交者获胜。
- 决策+绑定提交清单、幂等重试和恢复方式。
- 稳定错误及人工处理建议。
- 决策只作为后续学习数据来源，本阶段不训练或自动推广。

`tests/test_phase15d_docs.py` 精确检查中文标题、关键边界、页面链接和库存状态。

- [x] **Step 5：更新 README 和两份清单**

将 Phase 15D 标为已完成；下一目标为 Phase 15E 实物参考点云模板。明确 Phase 15F/17 才进行受控优化/训练，Phase 16 才建设统一三维查看器。

- [x] **Step 6：运行资料、编译和最终全仓门禁**

```powershell
uv run --extra test pytest -q tests/test_phase15d_docs.py --basetemp "$env:TEMP\pc-phase15d-docs" -p no:cacheprovider
.venv\Scripts\python.exe -m compileall -q src tests
uv run --extra test --extra browser-test pytest -q --browser chromium --tracing retain-on-failure --output "$env:TEMP\pc-phase15d-full-browser-results" --basetemp "$env:TEMP\pc-phase15d-full" -p no:cacheprovider
```

Expected: 资料测试 PASS、compileall exit 0、全仓 0 failures。Open3D 未安装时只允许既有可选真实引擎测试 skip；既有 Starlette/httpx 弃用提示可记录但不得扩大本阶段修复范围。

- [x] **Step 7：检查差异并提交**

```powershell
git diff --check
git status --short
git add -- tests/phase15d_support.py tests/test_phase15d_e2e.py tests/test_phase15d_docs.py tests/browser/conftest.py tests/browser/test_phase15d_workbenches.py pyproject.toml .github/workflows/test.yml .gitignore docs/phase15d-human-decisions-bindings.md README.md docs/current-development-inventory.md docs/system-function-module-inventory.md
git commit -m "feat: complete Phase 15D decisions and bindings"
```

- [x] **Step 8：最终复审**

逐条核对规格验收标准 1–13。只修复确认的严重和重要问题；若同类持久化或并发缺陷已达第二轮，停止补丁并回到提交包架构。记录最终测试证据和提交 SHA，不重复粘贴完整日志。

---

## 执行检查点

- Task 1–3：完成不可变数据与存储原语后，执行一次设计符合性复审。
- Task 4–5：完成写编排和队列投影后，执行一次并发/恢复聚焦复审。
- Task 6–7：完成 API/CLI 后，执行契约复审。
- Task 8–9：完成双页面后，执行页面权限与可用性复审。
- Task 10：运行一次全仓门禁和最终规格复审。

每项任务以一个单意图提交为目标；最终复审修复最多增加一个提交。不得使用宽泛 `git add .`。
