# Phase 15B-2 版本化特征与混合模型检索实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 交付从 Phase 14 已发布分割对象到可解释 Top-K 模型候选的完整检索闭环，包括对象审查证据、确定性特征、不可变生产/Challenger 索引、索引发布回滚、CLI、API 和审计恢复。

**架构：** 以 Phase 15B-1 的不可变发布、资源锁、四态候选和哈希链审计为基础，将对象输入、特征、索引、索引发布和检索运行拆成独立领域模块。第一版使用不可变 JSON/JSONL 文件快照和精确评分，不增加数据库或数值库依赖。

**技术栈：** Python 3.11、标准库、FastAPI、pytest、可选 trimesh、现有 Phase 15 审计与资源锁。

**规格：** `docs/superpowers/specs/2026-08-28-phase15b2-hybrid-model-retrieval-design.md`

## 全局约束

- 所有新增资料以中文为主；公共错误消息可保持现有英文契约。
- 开发基线为本地提交 `8711335`，后续工作基于 `codex/phase15b1-sampling-cli` 创建 `codex/phase15b2-hybrid-retrieval`。
- 不推送、不创建 PR、不合并，直到本计划全部任务完成并通过最终门禁。
- 每个生产行为先写红灯测试并观察预期失败，再写最小实现。
- 单个 JSON 配置/清单/特征/报告上限 16 MiB；Phase 14 标签上限 512 MiB；索引上限 100,000 行、1 GiB、每行 64 KiB。
- 生产索引只含当前模型发布；Challenger 必须显式指定且没有当前投影。
- 不自动递归删除、隔离、移动或接管不确定候选目录。
- 精确暂存任务文件；`uv.lock`、测试输出和其他工作树内容不得进入提交。
- 每个任务只做直接与聚焦验证；完整仓库测试只在最终就绪门禁执行一次。

## 文件结构

### 新增生产模块

- `src/pc_system/segmentation_review_evidence.py`：生成和验证 Phase 14 对象级审查证据。
- `src/pc_system/model_retrieval_config.py`：特征、评分、类别映射配置的严格校验与不可变发布。
- `src/pc_system/model_features.py`：纯 Python 确定性几何特征算法。
- `src/pc_system/model_feature_store.py`：模型/对象特征四态发布、读取和完整性验证。
- `src/pc_system/model_retrieval_input.py`：Phase 14 生产输入与 Phase 13A 专家输入适配。
- `src/pc_system/model_feature_index.py`：生产/Challenger 索引构建、自动采样、覆盖率和索引读取。
- `src/pc_system/model_index_release.py`：生产索引发布历史、当前投影、激活与回滚。
- `src/pc_system/model_retrieval.py`：精确评分、类别降级和不可变检索运行。

### 修改公共入口

- `src/pc_system/segmentation_correction_events.py`
- `src/pc_system/segmentation_correction_releases.py`
- `src/pc_system/commands/phase15.py`
- `src/pc_system/cli_parser.py`
- `src/pc_system/cli.py`
- `src/pc_system/api.py`

### 新增测试

- `tests/test_phase15b2_review_evidence.py`
- `tests/test_phase15b2_retrieval_config.py`
- `tests/test_phase15b2_features.py`
- `tests/test_phase15b2_feature_store.py`
- `tests/test_phase15b2_retrieval_input.py`
- `tests/test_phase15b2_feature_index.py`
- `tests/test_phase15b2_index_release.py`
- `tests/test_phase15b2_retrieval.py`
- `tests/test_phase15b2_cli_api.py`
- `tests/test_phase15b2_recovery.py`
- `tests/test_phase15b2_e2e.py`
- `tests/phase15b2_support.py`：共享有效 v1 配置、确定性点集和模型/发布构造器；只保存测试数据，不复制生产算法。

---

### 任务 1：Phase 14 对象级审查证据

**文件：**

- 新建：`src/pc_system/segmentation_review_evidence.py`
- 修改：`src/pc_system/segmentation_correction_events.py`
- 修改：`src/pc_system/segmentation_correction_releases.py`
- 新建：`tests/test_phase15b2_review_evidence.py`
- 修改：`tests/test_phase14_correction_releases.py`

**接口：**

- 产生：`active_correction_events(events: list[dict]) -> list[dict]`
- 产生：`build_object_review_evidence(*, asset_id: str, release_id: str, source_fingerprint: str, draft: dict, objects: dict, active_events: list[dict]) -> dict`
- 产生：`load_object_review_evidence(project_root: Path, asset_id: str, release_id: str) -> dict | None`
- 后续任务通过 `load_object_review_evidence` 判断类别是否允许硬过滤。

- [ ] **步骤 1：编写最终确认与失效红灯测试**

```python
def test_review_evidence_uses_only_final_valid_confirmation():
    draft, objects, events = relabel_confirm_then_split_fixture()
    evidence = build_object_review_evidence(
        asset_id="scan-a",
        release_id="release-001",
        source_fingerprint="a" * 64,
        draft=draft,
        objects=objects,
        active_events=active_correction_events(events),
    )
    by_id = {item["instance_id"]: item for item in evidence["objects"]}
    assert by_id["pump-001"]["review_state"] == "unreviewed"
    assert by_id["split-0003"]["review_state"] == "unreviewed"
```

增加确认、确认后改类、合并、拆分、撤销、重做、恢复、自动对象和人工改类未确认的独立测试。

- [ ] **步骤 2：运行直接测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_review_evidence.py -p no:cacheprovider
```

预期：导入 `pc_system.segmentation_review_evidence` 失败。

- [ ] **步骤 3：提取活动事件并实现审查证据生成**

```python
def active_correction_events(events: list[dict]) -> list[dict]:
    active: list[dict] = []
    redo: list[dict] = []
    for event in events:
        kind = event["operation"]["type"]
        if kind == "undo":
            if active:
                redo.append(active.pop())
        elif kind == "redo":
            if redo:
                active.append(redo.pop())
        else:
            active.append(event)
            redo.clear()
    return active
```

`materialize_correction` 必须复用该函数；证据生成使用最终 `confirmed_instance_ids` 和活动事件中最新有效 `confirm`，并计算对象及对象列表规范 SHA-256。

- [ ] **步骤 4：把证据作为 Phase 14 必要发布工件**

`correction_release.json.artifacts` 增加：

```python
"object_review_evidence": "object_review_evidence.json"
```

正式发布前在 staging 内写入，任一步失败保持既有原子回滚语义。旧发布加载返回 `None`，不得回写历史目录。

- [ ] **步骤 5：运行 Phase 14 聚焦回归**

```powershell
python -m pytest -q tests/test_phase15b2_review_evidence.py tests/test_phase14_correction_events.py tests/test_phase14_correction_releases.py tests/test_phase14_correction_e2e.py -p no:cacheprovider
```

- [ ] **步骤 6：精确提交**

```powershell
git add -- src/pc_system/segmentation_review_evidence.py src/pc_system/segmentation_correction_events.py src/pc_system/segmentation_correction_releases.py tests/test_phase15b2_review_evidence.py tests/test_phase14_correction_releases.py
git commit -m "feat: publish object review evidence"
```

### 任务 2：版本化检索配置

**文件：**

- 新建：`src/pc_system/model_retrieval_config.py`
- 新建：`tests/test_phase15b2_retrieval_config.py`
- 新建：`tests/phase15b2_support.py`

**接口：**

- 产生：`build_retrieval_config(feature: object, scoring: object, category_mapping: object) -> dict`
- 产生：`publish_retrieval_config(project_root: Path, *, config_id: str, feature: object, scoring: object, category_mapping: object, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`
- 产生：`load_retrieval_config(project_root: Path, config_id: str) -> dict`
- 产生：`list_retrieval_configs(project_root: Path) -> list[dict]`
- 产生测试夹具：`FEATURE_V1`、`SCORING_V1`、`MAPPING_V1`、`BOX_POINTS`，以及后续测试使用的 `prepare_released_models(project_root: Path) -> None`。

- [ ] **步骤 1：编写严格配置红灯测试**

```python
def test_config_rejects_unknown_fields_and_non_finite_weights():
    with pytest.raises(ModelMatchingError) as error:
        build_retrieval_config(
            feature={**FEATURE_V1, "extra": True},
            scoring={**SCORING_V1, "weights": {**SCORING_V1["weights"], "shape": float("nan")}},
            category_mapping=MAPPING_V1,
        )
    assert error.value.code == "feature_config_invalid"
```

覆盖布尔整数、采样上限、权重总和、Top-K、覆盖率、重复类别、NFKC 冲突、未知字段和重复 JSON 键的入口测试。

`tests/phase15b2_support.py` 中的常量逐字段复制规格第 6 节有效 v1 配置；`BOX_POINTS` 使用 2×1×0.5 米长方体表面上的至少 16 个有限点，`prepare_released_models` 创建 `pump-a:v1/v2` 和 `valve-a:v1` 及对应不可变发布。

- [ ] **步骤 2：运行配置测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_retrieval_config.py -p no:cacheprovider
```

预期：模块不存在。

- [ ] **步骤 3：实现规范配置与总指纹**

```python
def config_fingerprint(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

规范结果固定包含 `feature_config`、`scoring_config`、`category_mapping` 和 `config_fingerprint`。

- [ ] **步骤 4：实现不可变、可审计配置发布**

布局：

```text
models/retrieval_configs/<config_id>/
  operation_owner.json
  feature_config.json
  scoring_config.json
  category_mapping.json
  retrieval_config.json
```

`retrieval_config.json` 最后发布；相同请求重放，相同内容新操作复用，不同内容冲突失败。

- [ ] **步骤 5：运行配置与审计聚焦测试**

```powershell
python -m pytest -q tests/test_phase15b2_retrieval_config.py tests/test_phase15a_audit.py -p no:cacheprovider
```

- [ ] **步骤 6：精确提交**

```powershell
git add -- src/pc_system/model_retrieval_config.py tests/test_phase15b2_retrieval_config.py tests/phase15b2_support.py
git commit -m "feat: add versioned retrieval configs"
```

### 任务 3：确定性几何特征内核

**文件：**

- 新建：`src/pc_system/model_features.py`
- 新建：`tests/test_phase15b2_features.py`

**接口：**

- 产生：`extract_geometric_features(points: object, feature_config: dict) -> dict`
- 产生：`feature_vector_fingerprint(features: dict) -> str`
- 不读取文件、不写审计，供模型和对象特征发布共同复用。

- [ ] **步骤 1：编写顺序、平移和旋转不变红灯测试**

```python
def test_feature_vector_is_invariant_to_order_translation_and_rotation():
    baseline = extract_geometric_features(BOX_POINTS, FEATURE_V1)
    transformed = extract_geometric_features(
        rotate_translate(list(reversed(BOX_POINTS))), FEATURE_V1
    )
    assert transformed == baseline
```

分别覆盖共线、共面、球形对称、16 点下限、非有限坐标、2,000,000 上限边界、点结构错误和零跨度。

- [ ] **步骤 2：运行特征测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_features.py -p no:cacheprovider
```

预期：模块不存在。

- [ ] **步骤 3：实现纯 Python 对称 Jacobi 分解**

```python
def _eigenvalues_symmetric_3x3(matrix: list[list[float]]) -> list[float]:
    working = [row[:] for row in matrix]
    for _ in range(32):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pair: abs(working[pair[0]][pair[1]]))
        if abs(working[p][q]) <= 1e-15:
            break
        _jacobi_rotate(working, p, q)
    return sorted((max(working[i][i], 0.0) for i in range(3)), reverse=True)
```

同时返回规范主轴；轴符号通过首个绝对值最大分量为正进行规范化，退化轴只用于质量标记，不进入占用评分。

- [ ] **步骤 4：实现特征向量和质量降级**

按规格输出：

```python
{
    "observed_spans_m": spans,
    "span_ratios": ratios,
    "observed_box_volume_m3": volume,
    "principal_value_ratios": eigen_ratios,
    "radial_histogram": radial,
    "voxel_occupancy": occupancy,
    "point_count": len(points),
    "quality": {"status": status, "reasons": reasons},
}
```

公开浮点统一舍入 12 位；所有向量和直方图重新规范化以避免舍入后和偏移。

- [ ] **步骤 5：运行特征测试与属性化固定样例**

```powershell
python -m pytest -q tests/test_phase15b2_features.py -p no:cacheprovider
```

- [ ] **步骤 6：精确提交**

```powershell
git add -- src/pc_system/model_features.py tests/test_phase15b2_features.py
git commit -m "feat: add deterministic retrieval features"
```

### 任务 4：检索输入与不可变特征工件

**文件：**

- 新建：`src/pc_system/model_retrieval_input.py`
- 新建：`src/pc_system/model_feature_store.py`
- 新建：`tests/test_phase15b2_retrieval_input.py`
- 新建：`tests/test_phase15b2_feature_store.py`

**接口：**

- 产生：`load_retrieval_object(...) -> dict`
- 产生：`publish_object_feature(...) -> dict`
- 产生：`publish_model_feature(...) -> dict`
- 产生：`load_feature(project_root: Path, *, feature_type: str, identity: dict) -> dict`
- 产生：`list_features(project_root: Path, *, feature_type: str, identity: dict) -> list[dict]`

- [ ] **步骤 1：编写生产/实验输入红灯测试**

```python
def test_production_input_uses_published_release_and_confirmed_evidence(project):
    query = load_retrieval_object(
        project,
        source_kind="correction_release",
        asset_id="scan-a",
        source_id="release-001",
        instance_id="pump-001",
    )
    assert query["category_trust"] == "human_confirmed"
    assert query["coordinate_unit"] == "m"
```

覆盖草稿拒绝、旧发布 `legacy_unknown`、对象点篡改、源指纹篡改、Phase 13 未完成运行拒绝和专家实验输入。

- [ ] **步骤 2：运行输入测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_retrieval_input.py -p no:cacheprovider
```

预期：模块不存在。

- [ ] **步骤 3：实现有界对象加载器**

生产加载器按 `source_point_index` 聚合 `labels.json` 中目标实例；验证 release、review evidence、点/对象指纹和 512 MiB/2,000,000 点上限。实验加载器只读取 Phase 13A `completed` 运行的 membership 工件，并返回 `category_trust=algorithm_only`。

- [ ] **步骤 4：编写模型/对象特征发布红灯测试**

```python
def test_feature_manifest_is_last_visibility_marker(project, monkeypatch):
    monkeypatch.setattr(feature_store, "_publish_exact_json", interrupt_after_write)
    with pytest.raises(ModelMatchingError):
        publish_model_feature(project, **MODEL_REQUEST)
    assert list_features(project, **MODEL_IDENTITY) == []
```

`MODEL_REQUEST` 和 `MODEL_IDENTITY` 是该测试文件内由 `prepare_released_models` 构造的固定 `pump-a:v2`、匹配采样表达和 `FEATURE_V1` 请求字典；`interrupt_after_write` 是测试内故障注入函数，不改变公共领域签名。

覆盖相同操作恢复、新操作复用、外部候选忙、源特征篡改、owner 篡改和符号链接拒绝。

- [ ] **步骤 5：实现四态特征发布**

特征编号由来源、配置和算法指纹派生。使用 `model_resource_lock(root, "feature", feature_type, feature_id)`；先 owner、后 `feature.json`，最终加载必须验审计链和来源证据。

- [ ] **步骤 6：运行输入/特征聚焦测试**

```powershell
python -m pytest -q tests/test_phase15b2_retrieval_input.py tests/test_phase15b2_feature_store.py tests/test_phase15b2_features.py tests/test_phase15b1_sampling_publication.py -p no:cacheprovider
```

- [ ] **步骤 7：精确提交**

```powershell
git add -- src/pc_system/model_retrieval_input.py src/pc_system/model_feature_store.py tests/test_phase15b2_retrieval_input.py tests/test_phase15b2_feature_store.py
git commit -m "feat: publish verified retrieval features"
```

### 任务 5：模型特征索引与自动采样

**文件：**

- 新建：`src/pc_system/model_feature_index.py`
- 新建：`tests/test_phase15b2_feature_index.py`

**接口：**

- 产生：`build_model_feature_index(project_root: Path, *, index_id: str, index_mode: str, config_id: str, historical_releases: list[dict] | None, principal: Principal, operation_id: str, request_id: str, idempotency_key: str, mesh_reader: MeshReader = trimesh_mesh_reader) -> dict`
- 产生：`load_model_feature_index(project_root: Path, index_id: str, *, require_current_heads: bool) -> dict`
- 产生：`list_model_feature_indexes(project_root: Path) -> list[dict]`
- 产生：`read_index_entries(project_root: Path, index_id: str) -> Iterator[dict]`

- [ ] **步骤 1：编写生产当前版本与 Challenger 隔离红灯测试**

```python
def test_production_index_contains_only_current_model_releases(project):
    index = build_model_feature_index(project, **PRODUCTION_INDEX_REQUEST)
    entries = read_index_entries(project, index["index_id"])
    assert [(row["model_id"], row["version_id"]) for row in entries] == [
        ("pump-a", "v2"),
        ("valve-a", "v1"),
    ]
```

`PRODUCTION_INDEX_REQUEST` 在该测试文件中由 `prepare_released_models` 和已发布 `retrieval-v1` 配置生成，固定 `index_mode=production`，并使用可信 expert 主体及唯一操作、请求、幂等标识。

Challenger 测试必须证明历史版本只有在 `index_mode=challenger` 且显式 release 列表中出现。

- [ ] **步骤 2：运行索引测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_feature_index.py -p no:cacheprovider
```

预期：模块不存在。

- [ ] **步骤 3：实现来源头快照与采样选择**

生产枚举 `list_model_assets → load_current_model_release`。匹配采样要求：

```python
representation["generation_config"] == {
    "schema_version": "1.0",
    "algorithm": "sha256_area_weighted_v1",
    "point_count": feature_config["sampling"]["point_count"],
    "random_seed": feature_config["sampling"]["random_seed"],
    "coordinate_unit": "m",
    "coordinate_precision_decimals": 12,
}
```

- [ ] **步骤 4：实现受审计自动采样与模型排除**

缺失表达时调用 `sample_model_version`，子操作标识由 `sha256(parent_operation_id, model_id, version_id, config_fingerprint)` 派生。单模型失败写入：

```python
{"model_id": model_id, "version_id": version_id, "code": error.code, "child_operation_id": child_id}
```

不得吞掉审计持久化错误或不确定发布；这两类错误使父索引构建保持可恢复失败，而不是普通排除。

- [ ] **步骤 5：实现确定性 JSONL 和覆盖率**

条目按 `(category_id, model_id, version_id)` 写入规范单行 JSON。`index_manifest.json` 最后发布；索引读取逐行限制 64 KiB、总行数 100,000、总字节 1 GiB。

- [ ] **步骤 6：运行索引、采样和模型发布聚焦测试**

```powershell
python -m pytest -q tests/test_phase15b2_feature_index.py tests/test_phase15b2_feature_store.py tests/test_phase15b1_sampling_publication.py tests/test_phase15b1_model_release.py -p no:cacheprovider
```

- [ ] **步骤 7：精确提交**

```powershell
git add -- src/pc_system/model_feature_index.py tests/test_phase15b2_feature_index.py
git commit -m "feat: build immutable model feature indexes"
```

### 任务 6：生产索引发布、当前投影与回滚

**文件：**

- 新建：`src/pc_system/model_index_release.py`
- 新建：`tests/test_phase15b2_index_release.py`

**接口：**

- 产生：`release_model_feature_index(project_root: Path, *, index_id: str, release_id: str, action: str, expected_current_release_id: str | None, rollback_of_release_id: str | None, reason: str, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`
- 产生：`load_current_model_feature_index_release(project_root: Path) -> dict`
- 产生：`list_model_feature_index_releases(project_root: Path) -> list[dict]`

- [ ] **步骤 1：编写激活、过期和回滚红灯测试**

```python
def test_old_index_cannot_be_rolled_back_after_model_head_changes(project):
    activate_index(project, "index-001", "index-release-001")
    activate_new_model_version(project, "pump-a", "v2")
    with pytest.raises(ModelMatchingError) as error:
        rollback_index(project, "index-release-001")
    assert error.value.code == "model_index_stale"
```

覆盖首次激活、覆盖率不足、预期头冲突、同操作重放、发布历史、当前投影篡改和模型头集合篡改。

- [ ] **步骤 2：运行发布测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_index_release.py -p no:cacheprovider
```

预期：模块不存在。

- [ ] **步骤 3：复用 Phase 15B-1 发布状态机**

布局：

```text
models/feature_index_releases/<release_id>/
  operation_owner.json
  release.json
models/current_feature_index.json
```

专用锁为 `model_resource_lock(root, "feature-index-release", "production")`。候选四状态、预期头和 `published_unconfirmed` 恢复语义与模型版本发布一致。

- [ ] **步骤 4：实现生产覆盖率和当前模型头门禁**

激活或回滚前必须：

```python
index = load_model_feature_index(root, index_id, require_current_heads=True)
if index["coverage"]["ratio"] < config["production_minimum_coverage"]:
    raise ModelMatchingError("model_index_coverage_rejected", "...")
```

Challenger 索引不得发布为生产。

- [ ] **步骤 5：运行发布与 Phase 15B-1 发布回归**

```powershell
python -m pytest -q tests/test_phase15b2_index_release.py tests/test_phase15b1_model_release.py tests/test_phase15a_audit.py -p no:cacheprovider
```

- [ ] **步骤 6：精确提交**

```powershell
git add -- src/pc_system/model_index_release.py tests/test_phase15b2_index_release.py
git commit -m "feat: version production feature indexes"
```

### 任务 7：可解释精确 Top-K 检索

**文件：**

- 新建：`src/pc_system/model_retrieval.py`
- 新建：`tests/test_phase15b2_retrieval.py`

**接口：**

- 产生：`score_candidate(query: dict, candidate: dict, config: dict) -> dict`
- 产生：`retrieve_model_candidates(project_root: Path, *, retrieval_run_id: str, source_kind: str, asset_id: str, source_id: str, instance_id: str, index_release_id: str | None, index_id: str | None, top_k: int, keywords: list[str], tags: list[str], manufacturer: str | None, model_number: str | None, hint_source: str | None, principal: Principal, operation_id: str, request_id: str, idempotency_key: str) -> dict`
- 产生：`load_model_retrieval(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str) -> dict`

- [ ] **步骤 1：编写独立评分分量红灯测试**

```python
def test_smaller_model_is_penalized_more_than_larger_model():
    smaller = score_candidate(QUERY, candidate_with_spans([0.5, 0.5, 0.5]), CONFIG)
    larger = score_candidate(QUERY, candidate_with_spans([2.0, 2.0, 2.0]), CONFIG)
    assert smaller["components"]["dimensions"]["score"] < larger["components"]["dimensions"]["score"]
```

`QUERY`、`CONFIG` 和 `candidate_with_spans` 在该测试文件中使用 `SCORING_V1` 构造；除了主轴跨度之外，两名候选的元数据、形状、占用和质量字段完全一致。

覆盖类别、加权 Jaccard、厂商/型号、精确尺寸公式、形状 L1、占用、缺失分量重归一化、同分稳定排序和 metadata-only 风险。

- [ ] **步骤 2：运行评分测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_retrieval.py -p no:cacheprovider
```

预期：模块不存在。

- [ ] **步骤 3：实现纯评分函数**

返回结构固定为：

```python
{
    "model_id": candidate["model_id"],
    "version_id": candidate["version_id"],
    "score": total,
    "components": component_details,
    "effective_weights": normalized_weights,
    "risks": sorted(risks),
}
```

总分和分量分数统一舍入 12 位。

- [ ] **步骤 4：实现类别过滤、退化和精确扫描**

只有审查证据、人类确认来源、类别映射和索引类别候选同时存在时硬过滤。过滤为空则扫描全部条目并记录 `category_filter_empty`。生产模式验证当前索引 release 和模型头；实验模式只接受显式 Challenger `index_id`。

- [ ] **步骤 5：实现不可变检索运行发布**

先发布 owner、`query_feature.json`、`candidates.json`，最后发布 `retrieval_report.json`。报告保存计数、耗时、配置、解释、风险和审计证据；相同操作原位恢复。

- [ ] **步骤 6：运行检索聚焦测试**

```powershell
python -m pytest -q tests/test_phase15b2_retrieval.py tests/test_phase15b2_retrieval_input.py tests/test_phase15b2_feature_index.py tests/test_phase15b2_index_release.py -p no:cacheprovider
```

- [ ] **步骤 7：精确提交**

```powershell
git add -- src/pc_system/model_retrieval.py tests/test_phase15b2_retrieval.py
git commit -m "feat: add explainable hybrid model retrieval"
```

### 任务 8：CLI 与受保护 API

**文件：**

- 修改：`src/pc_system/commands/phase15.py`
- 修改：`src/pc_system/cli_parser.py`
- 修改：`src/pc_system/cli.py`
- 修改：`src/pc_system/api.py`
- 新建：`tests/test_phase15b2_cli_api.py`

**接口：**

- CLI：`create-model-retrieval-config`、`build-model-feature-index`、`release-model-feature-index`、`list-model-feature-indexes`、`list-model-feature-index-releases`、`retrieve-model-candidates`、`show-model-retrieval`
- API：规格第 16.2 节的八个接口。

- [ ] **步骤 1：编写 CLI 解析与稳定退出码红灯测试**

```python
def test_retrieve_cli_requires_operation_identity_and_top_k_bounds(capsys):
    assert main(RETRIEVE_ARGS_WITH_TOP_K_51) == 2
    assert capsys.readouterr().err.startswith("invalid_retrieval_input:")
```

覆盖成功 JSON 输出、生产/实验索引参数互斥、提示来源必填、列表和读取命令。

- [ ] **步骤 2：编写 API 身份与结构红灯测试**

```python
def test_retrieval_api_ignores_body_actor_and_uses_bound_principal(client):
    response = client.post(
        "/model-matching/retrievals",
        headers={"Authorization": "Bearer expert-token"},
        json={**REQUEST, "actor": "forged"},
    )
    assert response.status_code == 201
    assert response.json()["actor_id"] == "alice"
```

覆盖 expert 写、auditor 读、未认证、请求体上限、未知字段、领域错误映射和幂等重放。

- [ ] **步骤 3：运行 CLI/API 测试并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_cli_api.py -p no:cacheprovider
```

预期：解析器拒绝新命令，API 返回 404。

- [ ] **步骤 4：实现 CLI 适配器**

命令层只构造 `Principal(actor, frozenset({"expert"}), "cli")`、加载有界配置 JSON、调用领域服务并打印最终已验证 JSON 或路径。

- [ ] **步骤 5：实现 API 适配器**

使用 `require_phase15_principal`、`_phase15_json_object`、`_capture_payload`、`_require_payload_shape` 和 `phase15_action`。请求体 `actor`、`roles`、`source` 不进入领域参数。

- [ ] **步骤 6：运行公共入口与既有 Phase 15 回归**

```powershell
python -m pytest -q tests/test_phase15b2_cli_api.py tests/test_phase15a_api.py tests/test_phase15a_cli.py tests/test_phase15b1_sampling_cli.py tests/test_phase15b1_release_cli_api.py -p no:cacheprovider
```

- [ ] **步骤 7：精确提交**

```powershell
git add -- src/pc_system/commands/phase15.py src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/api.py tests/test_phase15b2_cli_api.py
git commit -m "feat: expose Phase 15B-2 retrieval interfaces"
```

### 任务 9：并发、篡改与原位恢复门禁

**文件：**

- 新建：`tests/test_phase15b2_recovery.py`
- 按失败归属修改：`src/pc_system/model_feature_store.py`
- 按失败归属修改：`src/pc_system/model_feature_index.py`
- 按失败归属修改：`src/pc_system/model_index_release.py`
- 按失败归属修改：`src/pc_system/model_retrieval.py`

**接口：** 不增加公共行为；验证任务 4–7 的四态候选和恢复不变量。

- [ ] **步骤 1：编写逐中断点红灯测试**

```python
@pytest.mark.parametrize("interrupt_at", ["owner", "content", "manifest", "audit_complete"])
def test_same_operation_recovers_in_place_without_cleanup(project, interrupt_at):
    interrupt_publication(project, interrupt_at)
    recovered = retry_same_operation(project)
    assert recovered["status"] in {"ready", "completed"}
    assert not list(project.rglob("*quarantine*"))
```

分别覆盖特征、索引、索引 release、当前投影和检索报告；增加并发双请求、外部 owner、符号链接、reparse point、非普通文件、重复键、过大 JSONL 行和父目录 fsync 不确定。

- [ ] **步骤 2：运行恢复测试并确认至少一个预期 RED**

```powershell
python -m pytest -q tests/test_phase15b2_recovery.py -p no:cacheprovider
```

预期：中断恢复或完整性边界测试失败，而不是测试自身语法错误。

- [ ] **步骤 3：按缺陷归属做最多两轮最小修正**

每个修正必须保持：不删除、不移动、不接管不确定候选；`published_unconfirmed` 保持运行并由同幂等请求恢复。第二轮仍出现同类 Important 问题时停止补丁叠加，回到对应状态机重新设计。

- [ ] **步骤 4：运行全部 Phase 15B-2 聚焦套件**

```powershell
python -m pytest -q tests/test_phase15b2_review_evidence.py tests/test_phase15b2_retrieval_config.py tests/test_phase15b2_features.py tests/test_phase15b2_retrieval_input.py tests/test_phase15b2_feature_store.py tests/test_phase15b2_feature_index.py tests/test_phase15b2_index_release.py tests/test_phase15b2_retrieval.py tests/test_phase15b2_cli_api.py tests/test_phase15b2_recovery.py -p no:cacheprovider
```

- [ ] **步骤 5：独立复审并精确提交**

复审只报告 Critical/Important、验证证据和结论。修复确认问题后：

```powershell
git add -- tests/test_phase15b2_recovery.py src/pc_system/model_feature_store.py src/pc_system/model_feature_index.py src/pc_system/model_index_release.py src/pc_system/model_retrieval.py
git commit -m "fix: harden Phase 15B-2 recovery"
```

若生产文件无需修改，只提交新增恢复测试。

### 任务 10：端到端闭环、中文资料与最终就绪门禁

**文件：**

- 新建：`tests/test_phase15b2_e2e.py`
- 新建：`docs/phase15b2-hybrid-model-retrieval.md`
- 修改：`README.md`
- 修改：`docs/current-development-inventory.md`
- 修改：`docs/system-function-module-inventory.md`

**接口：** 验证任务 1–9 的完整公共流程，不增加新的领域模块。

- [ ] **步骤 1：编写完整闭环红灯测试**

```python
def test_published_object_to_explainable_top_k_and_index_rollback(project):
    publish_reviewed_object(project)
    import_release_and_sample_models(project)
    create_config_build_and_activate_index(project, "index-001")
    report = retrieve_candidates(project, "pump-001")
    assert report["candidates"][0]["model_id"] == "pump-a"
    assert set(report["candidates"][0]["components"]) >= {"category", "dimensions", "shape"}
    activate_second_index_then_rollback(project)
    assert load_model_retrieval(
        project,
        asset_id="scan-a",
        source_id="release-001",
        instance_id="pump-001",
        retrieval_run_id=report["retrieval_run_id"],
    ) == report
    assert_all_operation_chains_verify(project)
```

增加旧 Phase 14 发布软评分、Phase 13A Challenger 检索、模型头变化导致索引 stale 和无候选稳定错误。

- [ ] **步骤 2：运行 E2E 并确认 RED**

```powershell
python -m pytest -q tests/test_phase15b2_e2e.py -p no:cacheprovider
```

预期：文档或最终公共流程尚未就绪。

- [ ] **步骤 3：编写中文操作与集成资料**

文档必须给出：

- Phase 14 审查证据与旧发布兼容；
- 配置、生产索引、Challenger、自动采样、覆盖率；
- 索引激活、过期、历史和回滚；
- 生产/实验检索 CLI 与 API 示例；
- 评分解释、降级、风险和稳定错误；
- 审计查询与中断恢复操作；
- Phase 15C–15F 明确边界。

- [ ] **步骤 4：运行 Phase 15B-2 与 Phase 14/15 聚焦回归**

```powershell
python -m pytest -q tests/test_phase15b2_e2e.py tests/test_phase15b2_*.py tests/test_phase14_correction_*.py tests/test_phase15a_*.py tests/test_phase15b1_*.py -p no:cacheprovider
```

PowerShell 不可靠展开通配符时，先用 `rg --files tests` 生成明确的测试文件参数并在同一 PowerShell 进程调用 pytest。

- [ ] **步骤 5：执行唯一一次完整仓库门禁**

```powershell
python -m pytest -q -p no:cacheprovider
python -m compileall -q src tests
git diff --check
rg -n "T[B]D|T[O]DO|F[I]XME|implement[ ]later|fill[ ]in[ ]details" src/pc_system tests docs/phase15b2-hybrid-model-retrieval.md README.md docs/current-development-inventory.md docs/system-function-module-inventory.md
```

预期：全部测试通过；编译和差异检查退出码 0；占位符扫描无匹配。

- [ ] **步骤 6：最终独立复审**

复审范围为 Phase 15B-2 起点到当前 HEAD，验收规格第 21 节。只修复 Critical/Important；Minor 记入后续债务。

- [ ] **步骤 7：精确提交交付资料**

```powershell
git add -- tests/test_phase15b2_e2e.py docs/phase15b2-hybrid-model-retrieval.md README.md docs/current-development-inventory.md docs/system-function-module-inventory.md
git commit -m "docs: complete Phase 15B-2 hybrid retrieval"
```

- [ ] **步骤 8：暂不推送并报告模块就绪状态**

确认工作树干净、提交链完整、分支相对 `origin/codex/phase15b1-sampling-cli` 的提交范围正确。等待用户决定继续 Phase 15C，或将 Phase 15B 完整模块统一推送、创建 PR、合并和发布。

## 计划自审清单

- [x] 规格第 5 节对象审查证据由任务 1 覆盖。
- [x] 规格第 6 节配置由任务 2 覆盖。
- [x] 规格第 7–8 节特征由任务 3–4 覆盖。
- [x] 规格第 9 节索引和自动采样由任务 5 覆盖。
- [x] 规格第 10 节索引发布回滚由任务 6 覆盖。
- [x] 规格第 11–13 节检索由任务 7 覆盖。
- [x] 规格第 14–17 节审计、权限和公共入口由任务 2、4–8 覆盖。
- [x] 规格第 18–19 节恢复和资源限制由任务 4–9 覆盖。
- [x] 规格第 20–21 节验收由任务 9–10 覆盖。
