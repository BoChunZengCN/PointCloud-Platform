# Phase 15C 刚性配准实施计划

> **面向执行代理：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐任务实施本计划。步骤使用复选框跟踪。

**目标：** 从 Phase 15B-2 冻结候选生成可审计、可恢复且经过三态质量门禁的刚性配准结果，不自动建立对象—模型绑定。

**架构：** 引擎无关核心负责证据冻结、配置、刚性验证、指标、门禁、幂等和审计；Open3D 可选适配器负责 FPFH、RANSAC/FGR、多尺度 point-to-plane ICP 与近邻证据。最终 4×4 矩阵始终把模型表达坐标变换到对象点云坐标。

**技术栈：** Python 3.11+、NumPy 2.x、可选 Open3D 0.19+、pytest、FastAPI、现有 Phase 15 哈希链审计与不可变 JSON 工件。

**规格：** `docs/superpowers/specs/2026-08-31-phase15c-rigid-registration-design.md`

## 全局约束

- 所有项目资料以中文为主；代码标识符和稳定错误码使用英文。
- Python 最低版本保持 `>=3.11`。
- 基础数值依赖固定为 `numpy>=2,<3`；生产适配器单独放入 `registration` 可选依赖 `open3d>=0.19,<1`。
- 核心测试不得要求安装 Open3D；Open3D 集成测试使用 `pytest.importorskip("open3d")`。
- 所有坐标单位为米，角度配置为弧度。
- 所有 `rigid_transform_4x4` 都是“模型坐标 → 对象坐标”。
- 配准结果只能是建议；Phase 15C 不创建、修改或恢复模型绑定。
- 禁止猜测旧检索报告缺失的模型表达；`schema_version: "1.0"` 只能查看，不能正式配准。
- 配置、运行和工件均使用规范 JSON、内容指纹、可信主体、幂等操作和哈希链审计。
- 不自动递归删除、移动或接管来源不明目录；发布状态不通过 `path.exists()` 猜测。
- 每个任务只暂存列出的文件；聚焦测试失败时不得进入更广验证。

## 文件职责

### 新建生产文件

- `src/pc_system/model_registration_config.py`：严格配置、不可变发布和读取。
- `src/pc_system/model_registration_engine.py`：引擎协议、结果类型和能力加载。
- `src/pc_system/model_registration_transform.py`：初始姿态与刚性矩阵验证。
- `src/pc_system/model_registration_metrics.py`：由原始近邻证据计算残差指标。
- `src/pc_system/model_registration_gate.py`：无副作用三态门禁。
- `src/pc_system/model_registration_input.py`：检索候选、对象和模型表达证据冻结。
- `src/pc_system/model_registration.py`：运行编排、审计、工件发布和重放。
- `src/pc_system/model_registration_open3d.py`：Open3D 生产适配器。

### 修改生产文件

- `src/pc_system/model_retrieval.py`：候选证据契约升级到 1.1。
- `src/pc_system/model_matching_errors.py`：继续复用统一异常类型，不增加并行异常体系。
- `src/pc_system/api.py`：配准配置、执行、读取路由和错误映射。
- `src/pc_system/cli_parser.py`：四个 Phase 15C CLI 子命令。
- `src/pc_system/cli.py`：CLI 分发。
- `src/pc_system/commands/phase15.py`：CLI 处理器和 Open3D 引擎解析。
- `pyproject.toml`：NumPy 基础依赖、Open3D 可选依赖和测试依赖。

### 新建测试文件

- `tests/phase15c_support.py`：固定配置、合成点云和确定性测试引擎。
- `tests/test_phase15c_registration_config.py`
- `tests/test_phase15c_registration_engine.py`
- `tests/test_phase15c_registration_transform.py`
- `tests/test_phase15c_registration_metrics.py`
- `tests/test_phase15c_registration_gate.py`
- `tests/test_phase15c_registration_input.py`
- `tests/test_phase15c_registration.py`
- `tests/test_phase15c_open3d.py`
- `tests/test_phase15c_open3d_integration.py`
- `tests/test_phase15c_cli_api.py`
- `tests/test_phase15c_e2e.py`

### 文档文件

- `docs/phase15c-rigid-registration.md`
- `README.md`
- `docs/current-development-inventory.md`
- `docs/system-function-module-inventory.md`

---

### Task 1：冻结 Phase 15B-2 候选表达证据

**Files:**

- Modify: `src/pc_system/model_retrieval.py`
- Modify: `tests/test_phase15b2_retrieval.py`
- Modify: `tests/test_phase15b2_e2e.py`

**Interfaces:**

- Consumes: Phase 15B-2 索引条目中的 `release_id`、`representation_id`、`representation_fingerprint`、`feature_id`、`feature_vector_fingerprint`。
- Produces: 新检索报告和 `candidates.json` 使用 `schema_version: "1.1"`；每个候选原样冻结上述五个证据字段。
- Produces: `load_model_retrieval` 可验证并读取 1.0/1.1，但返回原始版本，不把 1.0 自动升级为 1.1。

- [ ] **Step 1: 写入 1.1 候选证据失败测试**

```python
def test_retrieval_freezes_candidate_representation_evidence(tmp_path):
    _prepare_project(tmp_path)
    report = _retrieve(tmp_path)
    candidate = report["candidates"][0]
    assert report["schema_version"] == "1.1"
    assert {
        "release_id",
        "representation_id",
        "representation_fingerprint",
        "feature_id",
        "feature_vector_fingerprint",
    } <= candidate.keys()
```

- [ ] **Step 2: 写入旧报告只读兼容测试**

```python
def test_contract_validator_accepts_schema_1_0_for_read_only():
    report = {"schema_version": "1.0"}
    candidates = {
        "schema_version": "1.0",
        "candidates": [{"model_id": "pump-a", "version_id": "v1"}],
    }
    assert _validate_retrieval_contract_version(report, candidates) == "1.0"
```

- [ ] **Step 3: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15b2_retrieval.py tests/test_phase15b2_e2e.py -k "representation_evidence or schema_1_0"`

Expected: FAIL，原因是新候选缺少证据字段或报告仍为 1.0。

- [ ] **Step 4: 最小实现候选透传和双版本读取**

将 `score_candidate()` 返回值扩展为：

```python
return {
    "model_id": candidate["model_id"],
    "version_id": candidate["version_id"],
    "release_id": candidate["release_id"],
    "representation_id": candidate["representation_id"],
    "representation_fingerprint": candidate["representation_fingerprint"],
    "feature_id": candidate["feature_id"],
    "feature_vector_fingerprint": candidate["feature_vector_fingerprint"],
    "score": _round(total),
    "components": components,
    "effective_weights": effective,
    "risks": sorted(risks),
}
```

新增 `_validate_retrieval_contract_version(report: dict, candidates: dict) -> str`，新写入的 `candidates_artifact` 与 `report` 使用 1.1；读取验证根据实际版本选择严格字段集合，禁止自动补字段。`load_model_retrieval()` 必须调用该验证器，因此单元测试覆盖真实读取路径的版本判断。

- [ ] **Step 5: 运行 Phase 15B-2 聚焦回归**

Run: `uv run --extra test pytest -q tests/test_phase15b2_retrieval.py tests/test_phase15b2_e2e.py tests/test_phase15b2_cli_api.py tests/test_phase15b2_recovery.py`

Expected: PASS。

- [ ] **Step 6: 精确提交**

```bash
git add src/pc_system/model_retrieval.py tests/test_phase15b2_retrieval.py tests/test_phase15b2_e2e.py
git commit -m "feat: freeze retrieval candidate evidence"
```

---

### Task 2：版本化配准配置

**Files:**

- Create: `src/pc_system/model_registration_config.py`
- Create: `tests/phase15c_support.py`
- Create: `tests/test_phase15c_registration_config.py`

**Interfaces:**

- Produces: `build_registration_config(config_id: str, value: object) -> dict`。
- Produces: `publish_registration_config(project_root: Path, *, config_id: str, config: object, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`。
- Produces: `load_registration_config(project_root: Path, config_id: str) -> dict`。
- Produces: `list_registration_configs(project_root: Path) -> list[dict]`。
- Produces: `phase15c_support.REGISTRATION_V1`、`MODEL_POINTS`、`OBJECT_POINTS` 和 `IDENTITY_TRANSFORM`，供后续任务共享。

- [ ] **Step 1: 创建完整固定测试配置**

`REGISTRATION_V1` 必须明确包含：

```python
REGISTRATION_V1 = {
    "schema_version": "1.0",
    "engine_name": "deterministic-test",
    "preprocessing": {
        "voxel_sizes_m": [0.08, 0.04, 0.02],
        "normal_radius_multiplier": 2.5,
        "fpfh_radius_multiplier": 5.0,
        "normal_max_nn": 30,
        "fpfh_max_nn": 100,
        "minimum_points": 8,
        "maximum_points": 2_000_000,
    },
    "initial_hypotheses": {
        "include_identity": True,
        "include_principal_axes": True,
        "maximum_hypotheses": 24,
        "rotation_dedup_tolerance_rad": 0.001,
        "translation_dedup_tolerance_m": 0.001,
    },
    "coarse_registration": {
        "method": "ransac",
        "fgr_enabled": False,
        "ransac_n": 4,
        "maximum_iterations": 100_000,
        "confidence": 0.999,
        "distance_multiplier": 1.5,
        "edge_length_ratio": 0.9,
        "normal_angle_rad": 0.5235987755982988,
        "top_n": 4,
        "random_seed": 20260831,
    },
    "fine_registration": {
        "levels": [
            {"voxel_size_m": 0.08, "max_correspondence_distance_m": 0.12, "maximum_iterations": 40},
            {"voxel_size_m": 0.04, "max_correspondence_distance_m": 0.06, "maximum_iterations": 30},
            {"voxel_size_m": 0.02, "max_correspondence_distance_m": 0.03, "maximum_iterations": 20},
        ],
        "relative_fitness": 1e-6,
        "relative_rmse": 1e-6,
    },
    "transform_validation": {
        "homogeneous_tolerance": 1e-8,
        "orthogonality_tolerance": 1e-6,
        "determinant_tolerance": 1e-6,
        "singular_value_tolerance": 1e-6,
        "maximum_translation_m": 1000.0,
        "maximum_rotation_rad": 3.141592653589793,
    },
    "residual_metrics": {"inlier_distance_m": 0.03, "normal_consistency_minimum": 0.8},
    "quality_gates": {
        "passed_observed_coverage": 0.85,
        "passed_model_coverage": 0.70,
        "review_observed_coverage": 0.70,
        "review_model_coverage": 0.30,
        "maximum_inlier_rmse_m": 0.02,
        "maximum_chamfer_m": 0.04,
        "maximum_dimension_relative_error": 0.10,
        "minimum_pose_score_margin": 0.05,
        "maximum_fine_regression_ratio": 1.05,
    },
    "category_overrides": {},
}
```

- [ ] **Step 2: 写入严格字段、数值边界、幂等和篡改测试**

```python
def test_registration_config_rejects_unbounded_iterations():
    value = copy.deepcopy(REGISTRATION_V1)
    value["coarse_registration"]["maximum_iterations"] = 0
    with pytest.raises(ModelMatchingError, match="invalid") as captured:
        build_registration_config("registration-v1", value)
    assert captured.value.code == "registration_config_invalid"
```

同时覆盖 NaN、空层级、负距离、未知字段、不同内容复用编号、审计缺失和规范 JSON 篡改。

- [ ] **Step 3: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_config.py`

Expected: FAIL，模块尚不存在。

- [ ] **Step 4: 复用 Phase 15 配置发布模式实现最小模块**

实现必须使用 `validate_identifier`、`require_any_role(principal, {"expert"})`、`start_operation`、`complete_operation`、`fail_operation`、`model_resource_lock` 和 no-replace 规范 JSON 发布；不得复制新的身份或审计体系。

- [ ] **Step 5: 验证配置测试**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_config.py tests/test_phase15b2_retrieval_config.py`

Expected: PASS。

- [ ] **Step 6: 精确提交**

```bash
git add src/pc_system/model_registration_config.py tests/phase15c_support.py tests/test_phase15c_registration_config.py
git commit -m "feat: add versioned registration configs"
```

---

### Task 3：引擎协议、初始姿态与刚性矩阵验证

**Files:**

- Create: `src/pc_system/model_registration_engine.py`
- Create: `src/pc_system/model_registration_transform.py`
- Create: `tests/test_phase15c_registration_engine.py`
- Create: `tests/test_phase15c_registration_transform.py`
- Modify: `tests/phase15c_support.py`
- Modify: `pyproject.toml`

**Interfaces:**

- Produces: `EngineDescription(name: str, version: str, production: bool)`。
- Produces: `RegistrationEngine` 协议：`describe()`、`preprocess()`、`coarse_register()`、`fine_register()`、`nearest_neighbor_evidence()`。
- Produces: `generate_initial_hypotheses(model_points: np.ndarray, object_points: np.ndarray, symmetry_transforms: list[object], config: dict) -> list[dict]`。
- Produces: `validate_rigid_transform(matrix: object, policy: dict) -> dict`，返回规范矩阵和诊断量；非法输入抛出 `ModelMatchingError("non_rigid_transform", "Registration engine returned a non-rigid transform.")`。

- [ ] **Step 1: 添加 NumPy 直接依赖并写协议测试**

`pyproject.toml`：

```toml
dependencies = ["numpy>=2,<3"]
```

在 `tests/phase15c_support.py` 中新增 `DeterministicRegistrationEngine`；协议测试确认其能力说明和规范化结果字段，且 `describe().production is False`。

- [ ] **Step 2: 写入刚性矩阵失败测试**

```python
@pytest.mark.parametrize(
    "matrix",
    [
        [[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]],
        [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        [[1, 0.1, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    ],
)
def test_transform_rejects_scale_reflection_and_shear(matrix):
    with pytest.raises(ModelMatchingError) as captured:
        validate_rigid_transform(matrix, REGISTRATION_V1["transform_validation"])
    assert captured.value.code == "non_rigid_transform"
```

- [ ] **Step 3: 写入矩阵方向和有限假设测试**

测试用模型点 `[0, 0, 0]` 经平移矩阵变成对象点 `[1, 2, 3]`，并断言假设数量不超过 24、身份姿态稳定排第一、重复对称姿态被消除。

- [ ] **Step 4: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_engine.py tests/test_phase15c_registration_transform.py`

Expected: FAIL，模块尚不存在。

- [ ] **Step 5: 实现协议和纯 NumPy 刚性验证**

`RegistrationEngine` 的核心签名固定为：

```python
@dataclass(frozen=True)
class EngineDescription:
    name: str
    version: str
    production: bool


class RegistrationEngine(Protocol):
    def describe(self) -> EngineDescription:
        raise NotImplementedError

    def preprocess(self, model_points: np.ndarray, object_points: np.ndarray, config: dict) -> dict:
        raise NotImplementedError

    def coarse_register(self, prepared: dict, hypotheses: list[dict], config: dict) -> list[dict]:
        raise NotImplementedError

    def fine_register(self, prepared: dict, coarse_results: list[dict], config: dict) -> list[dict]:
        raise NotImplementedError

    def nearest_neighbor_evidence(self, prepared: dict, transform: np.ndarray, config: dict) -> dict:
        raise NotImplementedError
```

实现使用 `R.T @ R`、`np.linalg.det`、`np.linalg.svd`、轴角和位移范数验证矩阵，并将 NumPy 标量转换为普通 `float`。

- [ ] **Step 6: 验证核心测试和依赖安装**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_engine.py tests/test_phase15c_registration_transform.py`

Expected: PASS。

- [ ] **Step 7: 精确提交**

```bash
git add pyproject.toml src/pc_system/model_registration_engine.py src/pc_system/model_registration_transform.py tests/phase15c_support.py tests/test_phase15c_registration_engine.py tests/test_phase15c_registration_transform.py
git commit -m "feat: define rigid registration core"
```

---

### Task 4：残差指标和三态质量门禁

**Files:**

- Create: `src/pc_system/model_registration_metrics.py`
- Create: `src/pc_system/model_registration_gate.py`
- Create: `tests/test_phase15c_registration_metrics.py`
- Create: `tests/test_phase15c_registration_gate.py`

**Interfaces:**

- Consumes: 引擎提供的原始 `observed_to_model_distances_m`、`model_to_observed_distances_m`、可选 `normal_cosines`，以及模型/对象点云和合法矩阵。
- Produces: `compute_registration_metrics(model_points: np.ndarray, object_points: np.ndarray, transform: np.ndarray, evidence: dict, config: dict) -> dict`。
- Produces: `evaluate_registration_gate(metrics: dict, *, coarse_metrics: dict, fine_metrics: dict, pose_score_margin: float | None, symmetry_equivalent: bool, config: dict) -> dict`。

- [ ] **Step 1: 写入双向覆盖率与分位距离测试**

```python
def test_metrics_keep_directional_coverage_separate():
    evidence = {
        "observed_to_model_distances_m": [0.01, 0.02, 0.50],
        "model_to_observed_distances_m": [0.01, 0.50, 0.50, 0.50],
        "normal_cosines": None,
    }
    metrics = compute_registration_metrics(
        MODEL_POINTS,
        OBJECT_POINTS,
        IDENTITY_TRANSFORM,
        evidence,
        REGISTRATION_V1["residual_metrics"],
    )
    assert metrics["observed_to_model_coverage"] == pytest.approx(2 / 3)
    assert metrics["model_to_observed_coverage"] == pytest.approx(1 / 4)
    assert metrics["p95_distance_m"] >= metrics["p50_distance_m"]
```

- [ ] **Step 2: 写入 passed/review_required/rejected 测试**

覆盖：完整高质量匹配、部分遮挡、双向低覆盖、尺寸冲突、精配准退化、非等价对称歧义和声明等价对称姿态。

```python
def test_partial_occlusion_requires_review_instead_of_rejection():
    result = evaluate_registration_gate(
        {
            "observed_to_model_coverage": 0.90,
            "model_to_observed_coverage": 0.45,
            "inlier_rmse_m": 0.014,
            "chamfer_distance_m": 0.025,
            "maximum_dimension_relative_error": 0.04,
        },
        coarse_metrics={"rmse_m": 0.018},
        fine_metrics={"rmse_m": 0.014},
        pose_score_margin=0.2,
        symmetry_equivalent=False,
        config=REGISTRATION_V1,
    )
    assert result["status"] == "review_required"
    assert "partial_observation" in result["reasons"]
```

- [ ] **Step 3: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_metrics.py tests/test_phase15c_registration_gate.py`

Expected: FAIL，模块尚不存在。

- [ ] **Step 4: 实现无副作用指标和门禁**

指标使用 `np.quantile`、有限值验证和稳定舍入；Chamfer 定义为两个方向平均距离的算术平均。门禁按拒绝、复核、通过顺序判定，`reasons` 排序并去重；`registration_gate_rejected` 只进入原因数组，不抛传输异常。

- [ ] **Step 5: 运行聚焦测试**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_metrics.py tests/test_phase15c_registration_gate.py tests/test_phase15c_registration_transform.py`

Expected: PASS。

- [ ] **Step 6: 精确提交**

```bash
git add src/pc_system/model_registration_metrics.py src/pc_system/model_registration_gate.py tests/test_phase15c_registration_metrics.py tests/test_phase15c_registration_gate.py
git commit -m "feat: add registration quality gates"
```

---

### Task 5：配准输入冻结与陈旧检测

**Files:**

- Create: `src/pc_system/model_registration_input.py`
- Create: `tests/test_phase15c_registration_input.py`
- Modify: `tests/phase15c_support.py`

**Interfaces:**

- Consumes: `load_model_retrieval`、`load_retrieval_object`、`load_sampled_representation` 和候选排名（从 1 开始）。
- Produces: `load_registration_input(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str, candidate_rank: int, principal: Principal) -> dict`。
- Produces: JSON 快照字段 `retrieval_evidence`、`candidate_evidence`、`object_fingerprint`、`model_points`、`object_points`、`symmetry_transforms`、`coordinate_unit`；点和矩阵保持普通列表，由编排层在调用引擎前转换为 NumPy 数组。
- Produces: `phase15c_support.prepare_schema_1_1_retrieval(project_root: Path) -> dict`，返回可直接传给输入加载器的检索报告。

- [ ] **Step 1: 写入成功冻结和矩阵方向测试**

```python
def test_registration_input_loads_exact_ranked_representation(tmp_path):
    retrieval = prepare_schema_1_1_retrieval(tmp_path)
    frozen = load_registration_input(
        tmp_path,
        asset_id=retrieval["asset_id"],
        source_id=retrieval["source_id"],
        instance_id=retrieval["instance_id"],
        retrieval_run_id=retrieval["retrieval_run_id"],
        candidate_rank=1,
        principal=EXPERT,
    )
    assert frozen["candidate_evidence"]["representation_id"] == retrieval["candidates"][0]["representation_id"]
    assert frozen["coordinate_unit"] == "m"
```

- [ ] **Step 2: 写入失败关闭测试**

覆盖旧 1.0 报告、排名越界、对象指纹变化、表达清单篡改、表达点云篡改、候选字段与索引证据不一致和非 expert 主体。

旧报告断言：

```python
with pytest.raises(ModelMatchingError) as captured:
    load_registration_input(
        tmp_path,
        asset_id="scan-legacy",
        source_id="release-legacy",
        instance_id="obj-001",
        retrieval_run_id="retrieval-legacy",
        candidate_rank=1,
        principal=EXPERT,
    )
assert captured.value.code == "registration_input_incomplete"
```

- [ ] **Step 3: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_input.py`

Expected: FAIL，模块尚不存在。

- [ ] **Step 4: 实现严格证据链加载**

加载顺序固定为检索报告 → 当前对象重算 → 候选排名 → 不可变模型表达 → 特征/表达指纹交叉验证。任何缺失或不一致均在加载点返回稳定错误，不能传给引擎。

- [ ] **Step 5: 运行输入和上游回归**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_input.py tests/test_phase15b2_retrieval.py tests/test_phase15b2_retrieval_input.py tests/test_phase15b1_sampling_publication.py`

Expected: PASS。

- [ ] **Step 6: 精确提交**

```bash
git add src/pc_system/model_registration_input.py tests/phase15c_support.py tests/test_phase15c_registration_input.py
git commit -m "feat: freeze registration inputs"
```

---

### Task 6：配准编排、不可变工件和审计恢复

**Files:**

- Create: `src/pc_system/model_registration.py`
- Create: `tests/test_phase15c_registration.py`
- Modify: `tests/phase15c_support.py`

**Interfaces:**

- Consumes: Task 2–5 的配置、输入、引擎、矩阵、指标和门禁接口。
- Produces: `register_model_candidate(project_root: Path, *, registration_id: str, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str, candidate_rank: int, config_id: str, engine_resolver: Callable[[str], RegistrationEngine], principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`。
- Produces: `load_model_registration(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, registration_id: str) -> dict`。
- Produces: 规格第 13 节定义的七个不可变工件。
- Produces: `phase15c_support.prepare_phase15c_case(project_root: Path) -> dict`，返回 `asset_id`、`source_id`、`instance_id`、`retrieval_run_id` 和 `config_id`。
- Internal: `_select_final_hypothesis(coarse_results: list[dict], fine_results: list[dict], config: dict) -> tuple[dict, float | None, bool]`，按稳定分数返回最佳结果、姿态分差和对称等价标志。
- Internal: `_build_registration_artifacts(*, frozen: dict, config: dict, description: EngineDescription, hypotheses: list[dict], coarse: list[dict], fine: list[dict], validated: dict, metrics: dict, gate: dict, operation: dict) -> tuple[dict, dict]`，返回报告和具名工件映射。
- Internal: `_publish_registration_artifacts(project_root: Path, *, report: dict, artifacts: dict, operation: dict) -> dict`，在资源锁内验证 owner 并 no-replace 发布完整工件。

- [ ] **Step 1: 写入通过、复核和拒绝的编排测试**

```python
def test_registration_publishes_audited_completed_report(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    report = register_model_candidate(
        tmp_path,
        registration_id="registration-1",
        candidate_rank=1,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(mode="passed"),
        principal=EXPERT,
        operation_id="op-registration-1",
        request_id="req-registration-1",
        idempotency_key="idem-registration-1",
        **prepared,
    )
    assert report["status"] == "completed"
    assert report["gate_status"] == "passed"
    assert report["rigid_transform_4x4"][0][3] == pytest.approx(1.0)
    assert read_verified_operation_snapshot(tmp_path, "op-registration-1")["operation"]["status"] == "completed"
```

- [ ] **Step 2: 写入幂等、并发、失败和恢复测试**

覆盖：

- completed 同幂等重放不再次调用引擎。
- 相同键不同候选或配置返回 `idempotency_conflict`。
- 同一配准资源并发只有一个 owner。
- 引擎不可用生成诊断性 `failed` 报告且矩阵为空。
- 非刚性矩阵在指标前被拒绝。
- 工件中断不产生可确认结果。
- 发布后确认失败返回 `publication_recovery_required`，同请求原位完成。
- 任一规范工件被篡改时读取返回 `artifact_integrity_failed`。

- [ ] **Step 3: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration.py`

Expected: FAIL，编排模块尚不存在。

- [ ] **Step 4: 实现单向有界编排**

执行顺序固定为：

```python
request_payload = {
    "registration_id": registration_id,
    "asset_id": asset_id,
    "source_id": source_id,
    "instance_id": instance_id,
    "retrieval_run_id": retrieval_run_id,
    "candidate_rank": candidate_rank,
    "config_id": config_id,
}
operation, replayed = start_operation(
    project_root,
    operation_id=operation_id,
    operation_type="model_registration.run",
    principal=principal,
    request_id=request_id,
    idempotency_key=idempotency_key,
    request_payload=request_payload,
)
frozen = load_registration_input(
    project_root,
    asset_id=asset_id,
    source_id=source_id,
    instance_id=instance_id,
    retrieval_run_id=retrieval_run_id,
    candidate_rank=candidate_rank,
    principal=principal,
)
config = load_registration_config(project_root, config_id)
engine = engine_resolver(config["engine_name"])
ensure_operation_event(
    project_root,
    operation_id,
    "model_registration.engine_resolved",
    asdict(engine.describe()),
)
model_points = np.asarray(frozen["model_points"], dtype=np.float64)
object_points = np.asarray(frozen["object_points"], dtype=np.float64)
prepared = engine.preprocess(model_points, object_points, config)
hypotheses = generate_initial_hypotheses(
    model_points,
    object_points,
    frozen["symmetry_transforms"],
    config["initial_hypotheses"],
)
coarse = engine.coarse_register(prepared, hypotheses, config)
fine = engine.fine_register(prepared, coarse, config)
selected, pose_score_margin, symmetry_equivalent = _select_final_hypothesis(coarse, fine, config)
validated = validate_rigid_transform(selected["matrix"], config["transform_validation"])
evidence = engine.nearest_neighbor_evidence(prepared, validated["matrix"], config)
metrics = compute_registration_metrics(
    model_points,
    object_points,
    validated["matrix"],
    evidence,
    config["residual_metrics"],
)
gate = evaluate_registration_gate(
    metrics,
    coarse_metrics=selected["coarse_metrics"],
    fine_metrics=selected["fine_metrics"],
    pose_score_margin=pose_score_margin,
    symmetry_equivalent=symmetry_equivalent,
    config=config,
)
report, artifacts = _build_registration_artifacts(
    frozen=frozen,
    config=config,
    description=engine.describe(),
    hypotheses=hypotheses,
    coarse=coarse,
    fine=fine,
    validated=validated,
    metrics=metrics,
    gate=gate,
    operation=operation,
)
published = _publish_registration_artifacts(
    project_root,
    report=report,
    artifacts=artifacts,
    operation=operation,
)
complete_operation(project_root, operation_id, {"report_fingerprint": published["report_fingerprint"]})
```

`replayed` 且操作已完成时调用 `load_model_registration`；已失败时重放原错误；running 状态只允许按原 owner 和请求指纹进入恢复。引擎 resolver 必须在 `start_operation` 之后调用，因此缺失依赖也会进入失败审计。每一阶段先验证再写审计事件。粗结果为空时直接构造 `completed/rejected`，原因 `coarse_registration_failed`；粗结果存在但精结果为空时构造 `completed/rejected`，原因 `fine_registration_failed`，两条路径都不调用 `_select_final_hypothesis`。不得使用无界重试；恢复只接受原 owner、原请求指纹和完整一致工件。

- [ ] **Step 5: 运行编排及审计聚焦测试**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration.py tests/test_phase15a_audit.py tests/test_phase15b1_resource_lock.py`

Expected: PASS。

- [ ] **Step 6: 精确提交**

```bash
git add src/pc_system/model_registration.py tests/phase15c_support.py tests/test_phase15c_registration.py
git commit -m "feat: orchestrate audited model registration"
```

---

### Task 7：Open3D 生产适配器

**Files:**

- Create: `src/pc_system/model_registration_open3d.py`
- Create: `tests/test_phase15c_open3d.py`
- Create: `tests/test_phase15c_open3d_integration.py`
- Modify: `pyproject.toml`

**Interfaces:**

- Consumes: `RegistrationEngine` 协议和规范化配置。
- Produces: `Open3DRegistrationEngine`，`describe()` 返回实际 `open3d.__version__` 和 `production=True`。
- Produces: `load_open3d_registration_engine() -> RegistrationEngine`，导入缺失或版本不兼容时抛 `registration_engine_unavailable`。
- Produces: `resolve_registration_engine(name: str) -> RegistrationEngine`，首版只接受 `open3d`；未知名称返回 `registration_engine_unavailable`。

- [ ] **Step 1: 增加独立可选依赖**

```toml
[project.optional-dependencies]
registration = ["open3d>=0.19,<1"]
```

保留既有 `las`、`api`、`models` 和 `test` extra；不得把 Open3D 加入核心或测试默认依赖。

- [ ] **Step 2: 写入无 Open3D 的能力失败测试**

通过 monkeypatch 导入器模拟缺失：

```python
def test_open3d_loader_fails_closed_when_dependency_is_missing(monkeypatch):
    monkeypatch.setattr(registration_open3d, "_import_open3d", lambda: None)
    with pytest.raises(ModelMatchingError) as captured:
        registration_open3d.load_open3d_registration_engine()
    assert captured.value.code == "registration_engine_unavailable"
```

- [ ] **Step 3: 写入真实 Open3D 独立集成测试**

只在 `tests/test_phase15c_open3d_integration.py` 顶层使用：

```python
o3d = pytest.importorskip("open3d")
```

测试平移和小角度旋转的非对称合成点云，断言输出矩阵通过核心刚性验证，且从模型点映射到对象点。`tests/test_phase15c_open3d.py` 不调用 `importorskip`，因此缺失依赖的失败关闭测试始终执行。

- [ ] **Step 4: 运行测试并确认按预期失败或跳过**

Run: `uv run --extra test pytest -q tests/test_phase15c_open3d.py tests/test_phase15c_open3d_integration.py`

Expected: 无依赖测试 FAIL；真实适配器测试在未安装环境 SKIP。

- [ ] **Step 5: 实现 Open3D 0.19+ 适配器**

生产实现使用：

```python
source_fpfh = o3d.pipelines.registration.compute_fpfh_feature(source_down, fpfh_search)
target_fpfh = o3d.pipelines.registration.compute_fpfh_feature(target_down, fpfh_search)
ransac = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
    source_down,
    target_down,
    source_fpfh,
    target_fpfh,
    True,
    max_distance,
    o3d.pipelines.registration.TransformationEstimationPointToPoint(False),
    ransac_n,
    checkers,
    ransac_criteria,
)
fgr = o3d.pipelines.registration.registration_fgr_based_on_feature_matching(
    source_down,
    target_down,
    source_fpfh,
    target_fpfh,
    fgr_options,
)
o3d.pipelines.registration.registration_icp(
    source,
    target,
    max_distance,
    init,
    o3d.pipelines.registration.TransformationEstimationPointToPlane(),
    criteria,
)
```

`source` 始终是模型，`target` 始终是对象。RANSAC 显式设置 `with_scaling=False`、对应检查器、有界迭代和置信度。每一级 ICP 重新估计/验证法向并保留结果。近邻接口返回原始距离数组，最终门禁仍由核心计算。

- [ ] **Step 6: 运行核心和可选集成测试**

Run: `uv run --extra test pytest -q tests/test_phase15c_registration_engine.py tests/test_phase15c_registration_transform.py tests/test_phase15c_open3d.py tests/test_phase15c_open3d_integration.py`

Expected: PASS；未安装 Open3D 时只跳过标记的真实集成用例。

安装 Open3D 的独立验证命令：`uv run --extra test --extra registration pytest -q tests/test_phase15c_open3d.py tests/test_phase15c_open3d_integration.py`

- [ ] **Step 7: 精确提交**

```bash
git add pyproject.toml src/pc_system/model_registration_open3d.py tests/test_phase15c_open3d.py tests/test_phase15c_open3d_integration.py
git commit -m "feat: add Open3D registration adapter"
```

---

### Task 8：API、CLI、端到端闭环和中文文档

**Files:**

- Modify: `src/pc_system/api.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Modify: `src/pc_system/commands/phase15.py`
- Create: `tests/test_phase15c_cli_api.py`
- Create: `tests/test_phase15c_e2e.py`
- Create: `docs/phase15c-rigid-registration.md`
- Modify: `README.md`
- Modify: `docs/current-development-inventory.md`
- Modify: `docs/system-function-module-inventory.md`

**Interfaces:**

- Produces API: `POST/GET /model-matching/registration-configs`。
- Produces API: `POST /model-matching/registrations`。
- Produces API: `GET /model-matching/registrations/{asset_id}/{source_id}/{instance_id}/{registration_id}`。
- Produces CLI: `publish-model-registration-config`、`list-model-registration-configs`、`register-model-candidate`、`show-model-registration`。
- Consumes: CLI/API 生产执行向领域函数传入 `resolve_registration_engine`；领域函数在审计操作开始后解析配置中的引擎名称。测试通过应用工厂或 monkeypatch 注入确定性 resolver。

- [ ] **Step 1: 写入 API 正常与错误映射测试**

```python
def test_api_registration_returns_completed_gate_report(client, prepared_case):
    response = client.post(
        "/model-matching/registrations",
        json={
            **prepared_case,
            "registration_id": "registration-api-1",
            "candidate_rank": 1,
            "config_id": "registration-v1",
            "operation_id": "op-registration-api-1",
            "request_id": "req-registration-api-1",
            "idempotency_key": "idem-registration-api-1",
        },
        headers={"Authorization": "Bearer expert-token"},
    )
    assert response.status_code == 200
    assert response.json()["gate_status"] in {"passed", "review_required", "rejected"}
```

测试文件定义 `_production_client()` 与 `prepared_case` fixture，并使用 `headers={"Authorization": "Bearer expert-token"}`。同时断言：`registration_input_incomplete` 为 400、`object_fingerprint_stale` 为 409、`registration_engine_unavailable` 和 `registration_engine_failed` 为 503、资源不存在为 404；门禁 rejected、无合法粗配准和无合法精配准仍为 200。

- [ ] **Step 2: 写入 CLI 契约测试**

确认所有必填身份和幂等参数，配置文件采用严格 JSON；成功输出规范报告 JSON，执行错误沿用现有 `code: message` stderr 和非零退出码。

- [ ] **Step 3: 写入端到端测试**

端到端流程固定为：Phase 14 已发布对象 → Phase 15B-2 1.1 检索 → Phase 15C 确定性配准 → 三态报告 → 审计快照。断言运行目录中不存在任何 `model_binding` 工件。

- [ ] **Step 4: 运行测试并确认按预期失败**

Run: `uv run --extra test pytest -q tests/test_phase15c_cli_api.py tests/test_phase15c_e2e.py`

Expected: FAIL，路由和命令尚不存在。

- [ ] **Step 5: 实现 API/CLI 和错误映射**

将新错误码加入现有集合：

```python
_PHASE15_CONFLICT |= {"object_fingerprint_stale"}
_PHASE15_SERVICE_UNAVAILABLE |= {
    "registration_engine_unavailable",
    "registration_engine_failed",
    "non_rigid_transform",
}
_PHASE15_BAD_REQUEST |= {
    "registration_config_invalid",
    "registration_input_incomplete",
}
```

`coarse_registration_failed`、`fine_registration_failed` 和 `registration_gate_rejected` 只能出现在 200 报告的 `gate_reasons`，不能逃逸为 HTTP 异常。`artifact_integrity_failed` 按现有完整性错误策略映射为 409；`publication_recovery_required` 保持 503。API 请求体继续受 `MAX_PHASE15_REQUEST_BODY_BYTES` 限制。

- [ ] **Step 6: 编写中文操作与阶段边界文档**

文档必须包含：可选依赖安装、配置发布、执行配准、三态解释、旧报告重跑、Open3D 不可用、发布恢复、审计查询、矩阵方向以及“Phase 15C 不自动绑定”。README 和两份清单把 Phase 15C 标记为已完成，下一目标为 Phase 15D。

- [ ] **Step 7: 运行 Phase 15C 聚焦套件**

Run: `uv run --extra test pytest -q tests/test_phase15b2_retrieval.py tests/test_phase15c_registration_config.py tests/test_phase15c_registration_engine.py tests/test_phase15c_registration_transform.py tests/test_phase15c_registration_metrics.py tests/test_phase15c_registration_gate.py tests/test_phase15c_registration_input.py tests/test_phase15c_registration.py tests/test_phase15c_open3d.py tests/test_phase15c_open3d_integration.py tests/test_phase15c_cli_api.py tests/test_phase15c_e2e.py`

Expected: PASS；未安装 Open3D 时真实适配器测试允许 SKIP。

- [ ] **Step 8: 执行阶段完成门禁**

Run: `uv run --extra test pytest -q`

Expected: 全量 PASS。

Run: `python -m compileall -q src tests`

Expected: exit 0。

Run: `git diff --check`

Expected: 无输出、exit 0。

- [ ] **Step 9: 精确提交**

```bash
git add src/pc_system/api.py src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/commands/phase15.py tests/test_phase15c_cli_api.py tests/test_phase15c_e2e.py docs/phase15c-rigid-registration.md README.md docs/current-development-inventory.md docs/system-function-module-inventory.md
git commit -m "feat: complete Phase 15C rigid registration"
```

---

## 最终复审与合并门禁

完成 Task 1–8 后只执行一次独立最终复审，范围为 `main...HEAD`：

1. 规格符合性：矩阵方向、旧报告失败关闭、三态门禁和不自动绑定。
2. Critical/Important 缺陷：安全、并发、持久化、恢复、证据篡改和错误映射。
3. 测试证据：Phase 15C 聚焦套件、全量 pytest、compileall 和 `git diff --check`。
4. 提交范围：不包含生成目录、临时点云、测试缓存或其他分支文件。

只修复确认的 Critical/Important 问题；同一缺陷类型最多两轮。第二轮仍存在同类重要问题时停止补丁叠加，回到架构层调整。
