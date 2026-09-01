# Phase 15D 人工决策、不可变模型绑定与双界面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 从 Phase 15C 有效配准报告自动投影人工待办，提供不可变确认/拒绝/无匹配决定、模型绑定替换与恢复，以及业务和专业双页面。

**架构：** 继续使用当前文件持久化、内核资源锁和哈希链审计。`model_match_decision` 负责不可变提交包与写编排，`model_binding` 负责绑定结构和历史链，`model_decision_queue` 负责从 Phase 14/15B-2/15C/15D 权威工件动态投影清单；决定与可选绑定同目录发布，`commit.json` 最后决定可见性。

**技术栈：** Python 3.11+、FastAPI、pytest、现有规范 JSON/no-replace 文件原语、原生 HTML/CSS/JavaScript、Node.js 行为探针。

**规格：** `docs/superpowers/specs/2026-09-01-phase15d-human-decisions-bindings-design.md`

## 全局约束

- 系统不得自动确认或自动建立生产绑定。
- `operator` 只能确认 `passed`，`expert` 才能确认 `review_required`、重新配准、替换和恢复；`auditor` 只读。
- `rejected` 或 `failed` 配准永远不能确认；候选级拒绝不关闭事项，只有确认或无匹配关闭事项。
- 所有写操作必须绑定可信主体、操作编号、请求编号和幂等键。
- 标识符必须通过既有 `validate_identifier`；只接受普通目录和普通文件，拒绝链接、junction 和重解析点。
- 决定、绑定、owner 和提交清单使用规范 JSON 原始字节与 SHA-256；`commit.json` 最后发布。
- 没有有效提交清单和已完成审计快照的提交包不公开。
- 同一对象写入必须在 `model_resource_lock(project_root, "model-decision", asset_id, source_id, instance_id)` 内重新验证事项修订。
- 不创建可修改队列文件或 `current.json`；当前状态从不可变提交投影。
- 不自动删除、移动、隔离或递归清理异常路径。
- 核心测试使用 Phase 15C 确定性引擎，不要求安装 Open3D。
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

- [ ] **Step 1：建立 Phase 15D 测试夹具**

在 `tests/phase15d_support.py` 中复用 `phase15c_support.prepare_phase15c_case`，定义：

```python
from pc_system.model_matching_identity import Principal
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

- [ ] **Step 2：编写失败的身份、确定性和权限测试**

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

- [ ] **Step 3：运行红灯测试**

Run:

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_contracts.py --basetemp "$env:TEMP\pc-phase15d-contracts" -p no:cacheprovider
```

Expected: FAIL，缺少 `model_match_decision` 及上述公共函数。

- [ ] **Step 4：实现最小纯契约**

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

- [ ] **Step 5：运行绿灯和既有身份测试**

Run:

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_contracts.py tests/test_phase15a_identity.py --basetemp "$env:TEMP\pc-phase15d-contracts-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 6：提交**

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

- [ ] **Step 1：编写失败的绑定结构测试**

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

- [ ] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_binding.py --basetemp "$env:TEMP\pc-phase15d-binding" -p no:cacheprovider
```

Expected: FAIL，模块不存在。

- [ ] **Step 3：实现绑定构造与链投影**

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

- [ ] **Step 4：运行绿灯和矩阵回归测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_binding.py tests/test_phase15c_registration_transform.py --basetemp "$env:TEMP\pc-phase15d-binding-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 5：提交**

```powershell
git add -- src/pc_system/model_binding.py src/pc_system/model_match_decision.py tests/test_phase15d_binding.py
git commit -m "feat: add immutable model binding chains"
```

---

### Task 3：决定提交包、安全读取与最后可见性清单

**文件：**

- Modify: `src/pc_system/model_match_decision.py`
- Create: `tests/test_phase15d_decision_store.py`

**接口：**

- Consumes: Task 1/2 的决定与绑定结构；`model_sampling._canonical_json_bytes`、`model_sampling._publish_exact_json`、审计验证函数。
- Produces:
  - `load_decision_bundle(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, decision_id: str) -> dict`
  - `list_decision_bundles(project_root: Path, *, asset_id: str | None = None, source_id: str | None = None, instance_id: str | None = None) -> list[dict]`
  - `load_decision_context(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str) -> dict`
  - 私有 `_publish_decision_bundle_locked(project_root: Path, *, operation: dict, decision: dict, binding: dict | None, audit_events: list[dict]) -> dict`，仅供同模块对象锁事务调用。

- [ ] **Step 1：编写提交清单红灯测试**

测试提交目录固定为：

```text
reports/model_match_decisions/<asset>/<source>/<instance>/<decision>/
  owner.json
  decision.json
  binding.json     # confirmed only
  commit.json      # last
```

断言：没有 `commit.json` 不枚举；确认缺少 `binding.json` 不公开；拒绝/无匹配出现额外绑定文件失败；owner、原始字节哈希或审计引用篡改返回 `artifact_integrity_failed`；符号链接、junction、文件代替目录被拒绝。

- [ ] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_store.py --basetemp "$env:TEMP\pc-phase15d-store" -p no:cacheprovider
```

Expected: FAIL，读取/发布接口不存在。

- [ ] **Step 3：实现严格读取和规范提交包**

固定发布顺序：

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
_publish_exact_json(
    directory / "commit.json", commit,
    conflict_code="artifact_integrity_failed",
    conflict_message="Decision commit conflicts.",
)
```

`commit` 必须绑定 `decision_sha256`、可空 `binding_sha256`、`case_id`、`object_fingerprint`、`evidence_fingerprint`、`operation_id`、业务审计事件哈希和 `result_fingerprint`。读取使用 `lstat`/`fstat` 或现有严格原语验证普通文件、路径身份、重复键和规范原始字节。

`load_decision_context` 聚合当前对象指纹、同一检索运行的有效 Phase 15C 配准、有效决定提交包和绑定链，返回 `case_id`、`evidence_fingerprint`、两个头指纹及 `case_revision`。它不执行分页或角色字段裁剪，供 Task 4 锁内重检和 Task 5 队列投影共同复用。

- [ ] **Step 4：实现审计完成绑定**

公开读取调用 `read_verified_operation_snapshot`，要求：

```python
snapshot["operation"]["status"] == "completed"
snapshot["operation"]["result"]["result_fingerprint"] == commit["result_fingerprint"]
```

且提交清单中业务事件哈希能在已验证事件链中精确找到。

- [ ] **Step 5：运行绿灯和 Phase 15C 工件回归**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_store.py tests/test_phase15c_registration.py -k "published or bundle or audited or integrity" --basetemp "$env:TEMP\pc-phase15d-store-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 6：提交**

```powershell
git add -- src/pc_system/model_match_decision.py tests/test_phase15d_decision_store.py
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

- [ ] **Step 1：编写决定与审计红灯测试**

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

- [ ] **Step 2：编写并发和故障注入红灯测试**

使用两个线程基于同一 `expected_case_revision` 提交不同决定，断言一个成功、一个 `decision_conflict`。对 owner、decision、binding、业务事件、commit、complete 六个边界注入一次异常：相同请求重试原位完成，事件不重复；不同操作不得接管。

- [ ] **Step 3：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decisions.py --basetemp "$env:TEMP\pc-phase15d-decisions" -p no:cacheprovider
```

Expected: FAIL，业务编排函数不存在。

- [ ] **Step 4：实现对象锁内检查与提交**

`decide_model_match` 固定执行：

```python
operation, replayed = start_operation(
    root,
    operation_id=operation_id,
    operation_type="model_match.decision",
    principal=principal,
    request_id=request_id,
    idempotency_key=idempotency_key,
    request_payload=request,
)
snapshot = read_verified_operation_snapshot(root, operation_id)
operation_context = {
    **operation,
    "started_event_at": snapshot["events"][0]["timestamp"],
}
with model_resource_lock(root, "model-decision", asset_id, source_id, instance_id):
    current = load_decision_context(
        root,
        asset_id=asset_id,
        source_id=source_id,
        instance_id=instance_id,
        retrieval_run_id=retrieval_run_id,
    )
    if current["case_revision"] != expected_case_revision:
        raise ModelMatchingError("decision_conflict", "Decision item changed; refresh required.")
    registration = (
        None if decision == "no_match"
        else current["registrations_by_id"].get(registration_id)
    )
    if decision != "no_match" and registration is None:
        raise ModelMatchingError("registration_not_eligible", "Registration is not eligible.")
    require_decision_allowed(
        principal,
        decision=decision,
        gate_status=None if registration is None else registration["gate_status"],
        verification_scope=verification_scope,
    )
    if decision == "confirmed" and binding_id is None:
        raise ModelMatchingError("decision_not_allowed", "Confirmed decision requires a binding ID.")
    decision_payload = build_match_decision(
        request=request,
        context=current,
        operation=operation_context,
        principal=principal,
        previous_decision_id=current["decision_head_id"],
    )
    binding_payload = (
        build_model_binding(
            binding_id=binding_id,
            decision=decision_payload,
            registration=registration,
            transition="create",
            current_binding=None,
            restores_binding=None,
        )
        if decision == "confirmed" else None
    )
    decision_event = ensure_operation_event(
        root,
        operation_id,
        {
            "confirmed": "match.decision_confirmed",
            "rejected": "match.decision_rejected",
            "no_match": "match.decision_no_match",
        }[decision],
        {
            "case_id": current["case_id"],
            "decision_id": decision_id,
            "evidence_fingerprint": current["evidence_fingerprint"],
        },
    )
    audit_events = [decision_event]
    if binding_payload is not None:
        audit_events.append(
            ensure_operation_event(
                root,
                operation_id,
                "model_binding.created",
                {"binding_id": binding_id, "decision_id": decision_id},
            )
        )
    committed = _publish_decision_bundle_locked(
        root,
        operation=operation_context,
        decision=decision_payload,
        binding=binding_payload,
        audit_events=audit_events,
    )
complete_operation(root, operation_id, {"result_fingerprint": committed["result_fingerprint"]})
return load_decision_bundle(
    root,
    asset_id=asset_id,
    source_id=source_id,
    instance_id=instance_id,
    decision_id=decision_id,
)
```

任何 `operation_busy` 或 `publication_recovery_required` 保持操作可恢复；其他领域失败使用 `fail_operation` 固化稳定错误。耐久化 `commit.json` 优先于调用栈异常。

- [ ] **Step 5：实现替换与恢复**

两个函数都只允许 `expert`，在同一对象锁内重新加载当前绑定链。`supersede` 要求 `current_binding_id` 等于当前头；`restore` 还要求 `restores_binding_id` 是同一历史链成员。两者均创建新的 `confirmed` 决定和绑定，不修改旧文件。

- [ ] **Step 6：运行绿灯和审计/锁聚焦集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decisions.py tests/test_phase15a_audit.py tests/test_phase15b1_resource_lock.py --basetemp "$env:TEMP\pc-phase15d-decisions-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 7：提交**

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

- [ ] **Step 1：编写队列红灯测试**

覆盖：

```python
assert list_items(status="pending")["items"][0]["case_id"] == case_id
assert decide_confirmed_then_list(status="processed")["items"][0]["status"] == "processed"
assert add_registration_to_same_case()["status"] == "pending"
assert mutate_object_release_then_list()["items"][0]["status"] == "stale"
```

还要覆盖候选级拒绝保持 `pending`、全部门禁 `rejected` 只允许 `rerun/no_match`、稳定排序、1–100 `limit`、不透明游标、篡改失败关闭和角色字段裁剪。

- [ ] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_queue.py --basetemp "$env:TEMP\pc-phase15d-queue" -p no:cacheprovider
```

Expected: FAIL，队列模块不存在。

- [ ] **Step 3：实现事项聚合与修订**

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

- [ ] **Step 4：实现游标与筛选**

游标编码最后一项的规范排序键和筛选指纹；不同筛选复用游标返回 `decision_conflict` 或稳定无效请求错误。先过滤再截断，`next_cursor` 只在仍有更多结果时返回。

- [ ] **Step 5：运行绿灯和 Phase 15C 聚焦集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_queue.py tests/test_phase15c_registration.py tests/test_phase15c_e2e.py --basetemp "$env:TEMP\pc-phase15d-queue-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 6：提交**

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

- [ ] **Step 1：编写 API 红灯测试**

使用以下应用注册 operator/expert/auditor token：

```python
client = TestClient(
    create_app(
        tmp_path,
        run_mode="production",
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

- [ ] **Step 2：运行 API 红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py -k "api" --basetemp "$env:TEMP\pc-phase15d-api" -p no:cacheprovider
```

Expected: FAIL，路由不存在。

- [ ] **Step 3：实现查询路由**

新增：

```text
GET /model-matching/decision-items
GET /model-matching/decision-items/{case_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}
GET /model-matching/bindings/{asset_id}/{source_id}/{instance_id}/history
```

允许 `operator`、`expert`、`auditor`，把可信 `Principal` 传入领域层进行字段裁剪。

- [ ] **Step 4：实现写路由**

新增：

```text
POST /model-matching/decisions
POST /model-matching/bindings/{binding_id}/supersede
POST /model-matching/bindings/{binding_id}/restore
```

决定允许 `operator`/`expert`，替换和恢复仅 `expert`。请求体使用精确字段集合；可空 `registration_id`、`binding_id` 仍需精确类型检查。

- [ ] **Step 5：加入稳定错误映射并运行绿灯**

映射：权限 403；not found 404；`decision_conflict`、`binding_exists`、`binding_stale`、`object_fingerprint_stale`、`artifact_integrity_failed` 为 409；输入和资格错误 400；审计/发布恢复 503。

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py -k "api" tests/test_phase15a_api.py --basetemp "$env:TEMP\pc-phase15d-api-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 6：提交**

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

- [ ] **Step 1：编写 CLI 红灯测试**

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

- [ ] **Step 2：运行 CLI 红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py -k "cli" --basetemp "$env:TEMP\pc-phase15d-cli" -p no:cacheprovider
```

Expected: FAIL，解析器不识别命令。

- [ ] **Step 3：实现解析器、命令适配器和分派**

CLI 写命令构造：

```python
Principal(actor, frozenset({"operator"}), "cli")
```

`decide-model-match` 默认业务角色为 `operator`，增加 `--expert` 时改为 `expert`；替换和恢复始终构造 `expert`。不得从输入 JSON 接受角色集合。

- [ ] **Step 4：运行绿灯和旧 Phase 15 CLI 回归**

```powershell
uv run --extra test pytest -q tests/test_phase15d_cli_api.py tests/test_phase15c_cli_api.py tests/test_phase15b2_cli_api.py --basetemp "$env:TEMP\pc-phase15d-cli-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 5：提交**

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

- [ ] **Step 1：编写 HTML 与 Node 红灯测试**

断言业务页包含：`pending`、`processed`、`all` 页签，筛选区，列表区，候选区，原因输入，确认/拒绝/无匹配按钮，加载/空/错误/冲突提示容器。Node 探针：

```javascript
const item = {status:"pending", candidates:[{gate_status:"passed"}]};
console.log(JSON.stringify(m.availableActions(item, "operator")));
```

期望包含 `confirm`、`reject`、`no_match`，不包含 `rerun`、`supersede`、`restore`。

- [ ] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py -k "business or shared" --basetemp "$env:TEMP\pc-phase15d-frontend-business" -p no:cacheprovider
```

Expected: FAIL，页面和共享模块不存在。

- [ ] **Step 3：实现共享纯函数**

使用 UMD 风格导出，所有用户文本通过 `textContent` 或转义函数插入。动作矩阵必须以服务端 `available_actions` 为上限，再叠加角色 UI 限制；不得仅根据 `gate_status` 扩大权限。

- [ ] **Step 4：实现业务页面**

页面读取 URL 参数中的 API 地址和开发主体，调用列表/详情/决定 API。提交携带当前 `case_revision`；收到 `decision_conflict` 时禁用旧按钮、显示“记录已被其他用户处理，请刷新”，并提供刷新按钮。

候选级拒绝后保持当前事项并重新加载；确认或无匹配成功后切换到已处理详情。业务页不渲染完整矩阵和原始审计事件。

- [ ] **Step 5：运行绿灯和 Phase 14 前端回归**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py -k "business or shared" tests/test_phase14_correction_frontend.py --basetemp "$env:TEMP\pc-phase15d-frontend-business-green" -p no:cacheprovider
```

Expected: PASS；Node 不可用时仅相关行为探针 skip。

- [ ] **Step 6：提交**

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

- [ ] **Step 1：编写专业页面红灯测试**

HTML 必须包含候选解释、配准配置、引擎、矩阵、覆盖率、残差、尺寸、门禁原因、决策历史、绑定链和审计区域，以及重新配准、替换、恢复控件。

Node 探针断言：`expert` 在服务端允许时看到 `rerun/supersede/restore`；`auditor` 的动作数组为空；矩阵只接受 4×4 有限数字结构后进入视图模型。

- [ ] **Step 2：运行红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py -k "professional or auditor" --basetemp "$env:TEMP\pc-phase15d-frontend-lab" -p no:cacheprovider
```

Expected: FAIL，专业页面不存在。

- [ ] **Step 3：实现专业详情与只读模式**

`model-matching-lab.js` 根据可信 API 返回的角色/动作渲染。`auditor` 始终只读。完整矩阵使用表格展示，指标明确单位；审计区域显示 operation ID、状态和事件摘要，不渲染未转义 JSON。

- [ ] **Step 4：实现专家动作**

- 重新配准：选择已有发布配置，调用 `/model-matching/registrations`；成功后刷新事项修订。
- 替换：要求选择新配准和填写原因，调用 `/supersede`。
- 恢复：选择历史绑定，展示“将创建新版本，不修改旧绑定”，调用 `/restore`。
- 所有动作处理 loading、稳定错误和 `decision_conflict`。

- [ ] **Step 5：运行绿灯和完整前端集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_frontend.py tests/test_frontend_workbench.py tests/test_frontend_api_and_embed.py tests/test_phase14_correction_frontend.py --basetemp "$env:TEMP\pc-phase15d-frontend-green" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 6：提交**

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

**接口：**

- Consumes: Tasks 1–9。
- Produces: Phase 14 → 15B-2 → 15C → 15D 闭环、用户操作资料和阶段完成证据。

- [ ] **Step 1：编写端到端红灯测试**

通过真实领域服务/API 和确定性配准引擎构造：

```python
def test_phase15d_e2e_confirm_replace_restore_and_stale(tmp_path):
    # Phase 14 已发布对象 -> 检索 -> passed 配准 -> pending
    # operator 确认 -> processed + active binding
    # expert 新配准并 supersede -> 新 active、旧 superseded
    # expert restore 历史绑定 -> 再创建新 active 版本
    # 对象指纹变化 -> 当前绑定投影 stale
    # 所有提交均能读取已验证审计快照
```

另测 `review_required` 只能专家确认、候选拒绝仍 pending、无匹配不创建绑定、并发冲突和页面 API 基本流程。

- [ ] **Step 2：运行端到端红灯测试**

```powershell
uv run --extra test pytest -q tests/test_phase15d_e2e.py --basetemp "$env:TEMP\pc-phase15d-e2e" -p no:cacheprovider
```

Expected: 如存在集成缺口则 FAIL；只修复 Phase 15D 范围内缺口。

- [ ] **Step 3：补齐集成并运行 Phase 15D 聚焦集**

```powershell
uv run --extra test pytest -q tests/test_phase15d_decision_contracts.py tests/test_phase15d_binding.py tests/test_phase15d_decision_store.py tests/test_phase15d_decisions.py tests/test_phase15d_queue.py tests/test_phase15d_cli_api.py tests/test_phase15d_frontend.py tests/test_phase15d_e2e.py --basetemp "$env:TEMP\pc-phase15d-focused" -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 4：编写中文操作资料和资料测试**

`docs/phase15d-human-decisions-bindings.md` 必须说明：

- 三种角色和两类页面。
- 待处理/已处理/全部/陈旧状态。
- 确认、候选拒绝、无匹配、重新配准、替换和恢复。
- 多人同时查看和第一提交者获胜。
- 决策+绑定提交清单、幂等重试和恢复方式。
- 稳定错误及人工处理建议。
- 决策只作为后续学习数据来源，本阶段不训练或自动推广。

`tests/test_phase15d_docs.py` 精确检查中文标题、关键边界、页面链接和库存状态。

- [ ] **Step 5：更新 README 和两份清单**

将 Phase 15D 标为已完成；下一目标为 Phase 15E 实物参考点云模板。明确 Phase 15F/17 才进行受控优化/训练，Phase 16 才建设统一三维查看器。

- [ ] **Step 6：运行资料、编译和最终全仓门禁**

```powershell
uv run --extra test pytest -q tests/test_phase15d_docs.py --basetemp "$env:TEMP\pc-phase15d-docs" -p no:cacheprovider
.venv\Scripts\python.exe -m compileall -q src tests
uv run --extra test pytest -q --basetemp "$env:TEMP\pc-phase15d-full" -p no:cacheprovider
```

Expected: 资料测试 PASS、compileall exit 0、全仓 0 failures。Open3D 未安装时只允许既有可选真实引擎测试 skip；既有 Starlette/httpx 弃用提示可记录但不得扩大本阶段修复范围。

- [ ] **Step 7：检查差异并提交**

```powershell
git diff --check
git status --short
git add -- tests/test_phase15d_e2e.py tests/test_phase15d_docs.py docs/phase15d-human-decisions-bindings.md README.md docs/current-development-inventory.md docs/system-function-module-inventory.md
git commit -m "feat: complete Phase 15D decisions and bindings"
```

- [ ] **Step 8：最终复审**

逐条核对规格验收标准 1–13。只修复确认的严重和重要问题；若同类持久化或并发缺陷已达第二轮，停止补丁并回到提交包架构。记录最终测试证据和提交 SHA，不重复粘贴完整日志。

---

## 执行检查点

- Task 1–3：完成不可变数据与存储原语后，执行一次设计符合性复审。
- Task 4–5：完成写编排和队列投影后，执行一次并发/恢复聚焦复审。
- Task 6–7：完成 API/CLI 后，执行契约复审。
- Task 8–9：完成双页面后，执行页面权限与可用性复审。
- Task 10：运行一次全仓门禁和最终规格复审。

每项任务以一个单意图提交为目标；最终复审修复最多增加一个提交。不得使用宽泛 `git add .`。
