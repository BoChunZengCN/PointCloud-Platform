# Phase 15B-1 版本化模型采样实施计划

> **供执行人员使用：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施本计划。步骤使用复选框（`- [ ]`）跟踪状态。

**目标：** 为不可变 CAD 模型增加追加式发布/回滚历史，并生成可复现、不可覆盖的米制表面采样表达。

**架构：** 新建独立的模型资源锁、发布状态和采样模块。版本目录保持字节级不变；发布记录与采样表达使用原位候选、最终清单可见性标记和 Phase 15 审计恢复。API 只开放发布/回滚，采样先通过领域服务与 CLI 交付。

**技术栈：** Python 3.12、pytest、FastAPI、SHA-256 规范 JSON、Windows `msvcrt` / POSIX `fcntl` 内核字节锁、现有 Phase 15 审计账本。

**规格依据：** `docs/superpowers/specs/2026-08-27-phase15b1-versioned-model-sampling-design.md`

## 全局约束

- `models/<model_id>/versions/<version_id>` 在本阶段任何操作前后必须字节级不变。
- 发布记录、完整采样表达和资源锁文件不可删除或覆盖。
- 回滚创建新的 `release_id`，不得修改目标历史记录。
- 采样配置必须显式提供 `point_count` 与 `random_seed`；范围分别为 `1..500000` 与 `0..9223372036854775807`。
- 固定算法标识为 `sha256_area_weighted_v1`，输出坐标单位为米并保留 12 位小数。
- 采样 API、特征索引、Top-K 检索、法向量、FPFH 和配准不在计划范围内。
- 每个生产行为先写失败测试并观察预期 RED，再写最小实现。
- 只运行受影响测试；全仓 pytest 只在最终就绪门禁运行一次。

---

### 任务 1：跨平台永久模型资源锁

**文件：**
- 新建：`src/pc_system/model_resource_lock.py`
- 新建：`tests/test_phase15b1_resource_lock.py`

**接口：**
- 依赖：`pc_system.identifiers` 中的 `validate_identifier(value, label)`。
- 产出：`model_resource_lock(project_root: Path, resource_kind: str, *identifiers: str, timeout_seconds: float = 2.0) -> ContextManager[Path]`。
- 产出：`reports/model_matching_resource_locks` 下的永久锁文件。

- [ ] **步骤 1：编写路径与竞争行为的失败测试**

```python
def test_model_resource_lock_uses_stable_plain_file(tmp_path):
    with model_resource_lock(tmp_path, "release", "pump-a") as path:
        assert path == (
            tmp_path / "reports" / "model_matching_resource_locks"
            / "release-1373fa60d698c5e8bf6e679334ef39d51adde60f5d7d0aa0cad21b816e67a986.lock"
        )
        assert path.is_file()
    assert path.is_file()


def test_second_process_times_out_without_replacing_owner(tmp_path):
    with model_resource_lock(tmp_path, "release", "pump-a"):
        result = run_lock_probe_in_child(tmp_path, "release", "pump-a")
    assert result == {"code": "operation_busy"}
```

这些测试用于捕获永久锁路径被替换、无限阻塞，或把诊断元数据错误当作所有权证明等缺陷。

- [ ] **步骤 2：运行新测试并确认进入 RED 状态**

运行：

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_resource_lock.py -p no:cacheprovider
```

预期：由于 `pc_system.model_resource_lock` 尚不存在，测试在收集阶段失败。

- [ ] **步骤 3：实现非阻塞内核字节锁上下文管理器**

实现以下公共接口：

```python
@contextmanager
def model_resource_lock(
    project_root: Path,
    resource_kind: str,
    *identifiers: str,
    timeout_seconds: float = 2.0,
) -> Iterator[Path]:
    """Acquire a permanent per-resource OS byte lock or raise operation_busy."""
```

要求：

- 在计算规范 JSON 哈希和有界锁文件名之前，验证 `resource_kind` 及每个标识符；发布测试身份的精确字节为 `{"identifiers":["pump-a"],"resource_kind":"release"}`；
- 拒绝符号链接或重解析点形式的锁根目录与锁文件；
- 打开一个永久普通文件，不截断原文件；
- Windows 使用非阻塞 `msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)`，POSIX 使用 `fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)`；
- 使用 `time.monotonic()` 重试到显式截止时间；
- 超时返回 `ModelMatchingError("operation_busy", "Model resource is busy.")`；
- 只释放内核锁和文件描述符，绝不删除锁文件。

- [ ] **步骤 4：运行资源锁测试并确认 GREEN**

运行步骤 2 的命令。预期：全部测试通过。

- [ ] **步骤 5：提交资源锁基础**

```powershell
git add -- src/pc_system/model_resource_lock.py tests/test_phase15b1_resource_lock.py
git commit -m "feat: add model resource locks"
```

---

### 任务 2：不可变模型发布历史与回滚

**文件：**
- 新建：`src/pc_system/model_release.py`
- 新建：`tests/test_phase15b1_model_release.py`

**接口：**
- 依赖：`load_model_asset`、`load_model_version`、`fingerprint_file`、`model_resource_lock`、Phase 15 审计生命周期函数和 `Principal`。
- 产出：`release_model_version(project_root, *, model_id, version_id, release_id, action, expected_current_release_id, rollback_of_release_id, reason, principal, operation_id, request_id, idempotency_key) -> dict`。
- 产出：`load_current_model_release(project_root, model_id) -> dict | None`。
- 产出：`list_model_releases(project_root, model_id) -> list[dict]`。
- 产出：`list_version_release_status(project_root, model_id) -> list[dict]`。

- [ ] **步骤 1：编写激活与回滚行为的失败测试**

```python
def test_activate_then_rollback_appends_history_without_mutating_versions(tmp_path):
    import_versions(tmp_path, "v1", "v2")
    version_bytes = snapshot_version_bytes(tmp_path, "pump-a")

    first = release_model_version(
        tmp_path, model_id="pump-a", version_id="v2",
        release_id="release-001", action="activate",
        expected_current_release_id=None, rollback_of_release_id=None,
        reason="Initial production release", principal=EXPERT,
        operation_id="op-release-001", request_id="req-release-001",
        idempotency_key="idem-release-001",
    )
    rolled_back = release_model_version(
        tmp_path, model_id="pump-a", version_id="v1",
        release_id="release-002", action="rollback",
        expected_current_release_id="release-001",
        rollback_of_release_id="release-001",
        reason="Regression in v2", principal=EXPERT,
        operation_id="op-release-002", request_id="req-release-002",
        idempotency_key="idem-release-002",
    )

    assert first["version_id"] == "v2"
    assert rolled_back["previous_release_id"] == "release-001"
    assert rolled_back["version_id"] == "v1"
    assert [item["release_id"] for item in list_model_releases(tmp_path, "pump-a")] == [
        "release-001", "release-002"
    ]
    assert load_current_model_release(tmp_path, "pump-a") == rolled_back
    assert snapshot_version_bytes(tmp_path, "pump-a") == version_bytes
```

分别覆盖陈旧 `expected_current_release_id`、回滚到当前发布、跨模型发布引用、重复 `release_id`、无效原因、非专家主体、投影篡改、发布记录篡改、幂等重放，以及两个请求从同一预期头并发更新。

这些测试用于捕获原地修改历史、后写覆盖并发、未验证投影，以及回滚时静默改变版本字节等缺陷。

- [ ] **步骤 2：运行发布测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_model_release.py -p no:cacheprovider
```

预期：由于 `pc_system.model_release` 尚不存在，测试收集失败。

- [ ] **步骤 3：实现严格结构、安全读取和请求冻结**

定义精确常量与公共函数签名：

```python
RELEASE_ACTIONS = frozenset({"activate", "rollback"})

def release_model_version(
    project_root: Path,
    *,
    model_id: str,
    version_id: str,
    release_id: str,
    action: str,
    expected_current_release_id: str | None,
    rollback_of_release_id: str | None,
    reason: str,
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    """Publish one immutable activation or rollback record."""
```

使用规格规定的精确字段集合。在查询业务数据之前冻结并验证全部请求字段，构造规范审计载荷，要求 `expert` 角色，并使用稳定的 Phase 15 错误码。

- [ ] **步骤 4：实现锁内不覆盖发布与重放恢复**

在 `model_resource_lock(project_root, "release", model_id)` 内执行：

- 依据发布记录验证当前投影；
- 精确比较 `expected_current_release_id`；
- 通过 `load_model_version` 验证目标版本；
- 只创建一次 `releases/<release_id>` 并持久化操作所有者信封；
- 将 `release.json` 作为不可变可见性标记发布；
- 原子写入 `current_release.json` 投影；
- 追加 `model_release.published` 或 `model_release.rolled_back`；
- 完成规范操作；
- 相同请求重放时验证发布记录，按需重建投影，确保业务事件只有一条，并完成原操作；
- 绝不递归删除、重命名或接管不匹配的候选记录。

- [ ] **步骤 5：运行发布与 Phase 15A 完整性测试并确认 GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_model_release.py `
  tests/test_phase15a_model_import.py `
  tests/test_phase15a_audit.py `
  -p no:cacheprovider
```

- [ ] **步骤 6：提交发布历史功能**

```powershell
git add -- src/pc_system/model_release.py tests/test_phase15b1_model_release.py
git commit -m "feat: add model release history"
```

---

### 任务 3：发布 CLI 与受保护 API

**文件：**
- 修改：`src/pc_system/commands/phase15.py`
- 修改：`src/pc_system/cli_parser.py`
- 修改：`src/pc_system/cli.py`
- 修改：`src/pc_system/api.py`
- 新建：`tests/test_phase15b1_release_cli_api.py`

**接口：**
- 依赖：任务 2 的全部发布函数。
- 产出：CLI 命令 `release-model-version` 和 `list-model-releases`。
- 产出：`POST /model-library/models/{model_id}/releases`。
- 扩展：`GET /model-library/models/{model_id}`，增加 `current_release` 和 `release_history`。

- [ ] **步骤 1：编写 CLI 与 API 契约失败测试**

```python
def test_release_cli_creates_audited_rollback(tmp_path, capsys):
    exit_code = main([
        "release-model-version", "--project-root", str(tmp_path),
        "--model-id", "pump-a", "--version-id", "v1",
        "--release-id", "release-002", "--action", "rollback",
        "--expected-current-release-id", "release-001",
        "--rollback-of-release-id", "release-001",
        "--reason", "Regression in v2", "--actor", "alice",
        "--operation-id", "op-release-002", "--request-id", "req-release-002",
        "--idempotency-key", "idem-release-002",
    ])
    assert exit_code == 0
    assert "release-002" in capsys.readouterr().out


def test_production_release_api_uses_configured_principal(tmp_path):
    response = production_client(tmp_path).post(
        "/model-library/models/pump-a/releases",
        headers={"X-API-Key": "expert-token", "X-Actor-ID": "spoofed"},
        json=release_payload(),
    )
    assert response.status_code == 201
    assert response.json()["actor_id"] == "trusted-expert"
```

补充精确请求结构、禁止鉴权前读取正文、角色拒绝审计、开发身份来源、稳定 HTTP 映射、公开历史读取和畸形可选标识符测试。

- [ ] **步骤 2：运行 CLI/API 测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_release_cli_api.py -p no:cacheprovider
```

预期：解析器拒绝新命令，API 返回 404。

- [ ] **步骤 3：增加精确解析器与轻量命令适配器**

`release-model-version` 必须接收全部审计标识符、`--action`、`--version-id`、`--release-id`、`--reason` 和 `--actor`。未提供可选的 `--expected-current-release-id` 与 `--rollback-of-release-id` 时必须精确传递 `None`。`list-model-releases` 只接收项目和模型标识符，并输出规范 JSON。

- [ ] **步骤 4：增加 API 载荷捕获与路由集成**

读取请求正文前先执行授权。精确捕获文本字段和可空发布标识符，不做隐式字符串转换。扩展 `_PHASE15_*` 错误分组以容纳新增稳定错误，同时保持模型读取公开且经过验证。

- [ ] **步骤 5：运行新增及既有 Phase 15 API/CLI 测试并确认 GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_release_cli_api.py `
  tests/test_phase15a_api.py tests/test_phase15a_cli.py `
  -p no:cacheprovider
```

- [ ] **步骤 6：提交发布接口**

```powershell
git add -- src/pc_system/commands/phase15.py src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/api.py tests/test_phase15b1_release_cli_api.py
git commit -m "feat: expose model release controls"
```

---

### 任务 4：确定性网格采样内核

**文件：**
- 修改：`src/pc_system/model_mesh.py`
- 新建：`src/pc_system/model_sampling.py`
- 新建：`tests/test_phase15b1_sampling_kernel.py`

**接口：**
- 在 `model_mesh.py` 中产出：`read_mesh_geometry_m(path: Path, declared_unit: str, *, reader: MeshReader) -> tuple[list[list[float]], list[list[int]]]`。
- 在 `model_sampling.py` 中产出：`build_sampling_config(point_count: int, random_seed: int) -> dict`。
- 产出：`sampling_config_fingerprint(config: dict) -> str`。
- 产出：`sample_mesh_surface(vertices_m, faces, config) -> dict`。

- [ ] **步骤 1：编写确定性几何失败测试**

```python
def test_same_mesh_and_config_produce_literal_points():
    config = build_sampling_config(point_count=3, random_seed=7)
    result = sample_mesh_surface(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        [[0, 1, 2]],
        config,
    )
    assert result == {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "point_count": 3,
        "points": [
            [0.081298607113, 0.640763524138, 0.0],
            [0.549106701442, 0.023405678479, 0.0],
            [0.125583899168, 0.188869152696, 0.0],
        ],
    }
```

上述字面量点来自配置指纹 `eaa98cd4674118a8cdca4215d9a4296ce1ec003ef15fa55a0a922a7550f97961` 对应的既定 SHA-256 分通道公式；测试不得自行计算预期值。补充扇形三角化、面积比为 1:3 的双三角形选择、单位转换、部分和全部退化、`-0.0`、最大边界、包含布尔值的错误精确类型、非有限顶点及源顺序稳定性测试。

这些测试用于捕获错误使用 `random`、以顶点采样代替表面采样、错误平方根重心映射、遗漏单位转换或三角形顺序不稳定等缺陷。

- [ ] **步骤 2：运行采样内核测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_sampling_kernel.py -p no:cacheprovider
```

预期：由于采样接口尚不存在，测试收集失败。

- [ ] **步骤 3：公开已验证的米制几何读取且不重复校验逻辑**

重构 `inspect_mesh`，使其调用 `read_mesh_geometry_m`，并保留所有 Phase 15A 错误和摘要字段。新读取函数验证格式、单位、顶点和面，并且每个顶点只执行一次比例换算。

- [ ] **步骤 4：实现规范配置与 SHA-256 分通道生成器**

使用以下精确表达身份：

```python
config_fingerprint = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
representation_id = f"cad-sampled-{config_fingerprint}"
```

对每个样本和通道计算 `b"phase15b1" + bytes.fromhex(config_fingerprint) + i.to_bytes(8, "big") + bytes([lane])` 的哈希。把摘要前 8 个字节解释为整数并除以 `2**64`，得到 `[0,1)`。

- [ ] **步骤 5：实现扇形三角化与面积加权重心采样**

保持面顺序，忽略面积精确为零的三角形；总面积为零时失败；按累计面积选择三角形，并使用 `sqrt(u)` 计算均匀重心坐标。每个米制坐标保留 12 位小数，并把任意符号零规范为 `0.0`。

- [ ] **步骤 6：运行采样内核与 Phase 15A 网格测试并确认 GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_sampling_kernel.py tests/test_phase15a_model_mesh.py `
  -p no:cacheprovider
```

- [ ] **步骤 7：提交确定性采样内核**

```powershell
git add -- src/pc_system/model_mesh.py src/pc_system/model_sampling.py tests/test_phase15b1_sampling_kernel.py
git commit -m "feat: add deterministic model sampling"
```

---

### 任务 5：不可变采样表达发布

**文件：**
- 修改：`src/pc_system/model_sampling.py`
- 新建：`tests/test_phase15b1_sampling_publication.py`

**接口：**
- 依赖：任务 1 的资源锁、任务 4 的采样内核、`load_model_version`、`fingerprint_file`、Phase 15 审计、`Principal` 和 `MeshReader`。
- 产出：`sample_model_version(project_root, *, model_id, version_id, point_count, random_seed, principal, operation_id, request_id, idempotency_key, mesh_reader) -> dict`。
- 产出：`load_sampled_representation(project_root, model_id, version_id, representation_id) -> dict`。
- 产出：`list_sampled_representations(project_root, model_id, version_id) -> list[dict]`。

- [ ] **步骤 1：编写发布、不可变性与恢复失败测试**

```python
def test_sample_model_version_publishes_outside_immutable_version(tmp_path):
    import_model_fixture(tmp_path, model_id="pump-a", version_id="v1")
    version_before = snapshot_version_bytes(tmp_path, "pump-a")

    representation = sample_model_version(
        tmp_path, model_id="pump-a", version_id="v1",
        point_count=10, random_seed=7, principal=EXPERT,
        operation_id="op-sample-001", request_id="req-sample-001",
        idempotency_key="idem-sample-001", mesh_reader=fake_mesh_reader,
    )

    assert representation["representation_type"] == "cad_sampled"
    assert representation["point_count"] == 10
    assert snapshot_version_bytes(tmp_path, "pump-a") == version_before
    assert load_sampled_representation(
        tmp_path, "pump-a", "v1", representation["representation_id"]
    ) == representation
```

补充源清单篡改、采样点篡改、表达清单篡改、相同请求重放、同配置不同操作的复用规则、部分所有者恢复、外部所有者拒绝、清单发布前失败、清单发布后失败和有效审计事件顺序测试。

- [ ] **步骤 2：运行发布测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_sampling_publication.py -p no:cacheprovider
```

预期：`sample_model_version` 尚不存在。

- [ ] **步骤 3：实现严格的采样点与表达读取器**

验证精确结构字段、路径身份、普通目录/文件、有限坐标、点数、配置指纹、源指纹、工件 URI 和 SHA-256。没有有效最终 `representation.json` 的目录不得由列表接口返回。

- [ ] **步骤 4：实现受审计的原位候选发布**

使用确定性表达编号和任务 1 的采样资源锁。冻结 `operation_owner.json`，写入采样点，最后发布 `representation.json` 作为可见性标记。匹配的重试验证并继续已有字节；所有者或内容不匹配时失败关闭。绝不递归删除或隔离候选目录。

- [ ] **步骤 5：运行发布与导入完整性测试并确认 GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_sampling_publication.py `
  tests/test_phase15b1_sampling_kernel.py `
  tests/test_phase15a_model_import.py tests/test_phase15a_audit.py `
  -p no:cacheprovider
```

- [ ] **步骤 6：提交不可变采样表达**

```powershell
git add -- src/pc_system/model_sampling.py tests/test_phase15b1_sampling_publication.py
git commit -m "feat: publish sampled model representations"
```

---

### 任务 6：采样 CLI、中文文档与端到端门禁

**文件：**
- 修改：`src/pc_system/commands/phase15.py`
- 修改：`src/pc_system/cli_parser.py`
- 修改：`src/pc_system/cli.py`
- 新建：`tests/test_phase15b1_sampling_cli.py`
- 新建：`tests/test_phase15b1_e2e.py`
- 新建：`docs/phase15b1-versioned-model-sampling.md`
- 修改：`README.md`
- 修改：`docs/current-development-inventory.md`
- 修改：`docs/system-function-module-inventory.md`

**接口：**
- 依赖：`sample_model_version` 和采样表达查询函数。
- 产出：CLI `sample-model-version` 和 `list-model-representations`。
- 文档：操作员激活、回滚、采样、历史检查和恢复规则。

- [ ] **步骤 1：编写采样 CLI 与端到端失败测试**

```python
def test_import_release_sample_and_rollback_is_fully_auditable(tmp_path):
    create_asset_and_import_two_versions(tmp_path)
    activate_v2(tmp_path)
    sample_v2(tmp_path, point_count=16, random_seed=11)
    rollback_to_v1(tmp_path)

    assert load_current_model_release(tmp_path, "pump-a")["version_id"] == "v1"
    representations = list_sampled_representations(tmp_path, "pump-a", "v2")
    assert len(representations) == 1
    assert representations[0]["point_count"] == 16
    for operation_id in RELEASE_AND_SAMPLE_OPERATION_IDS:
        assert verify_operation_chain(read_operation_events(tmp_path, operation_id))
```

补充 CLI 测试：必须显式提供点数/种子、无效配置稳定返回退出码 2、输出表达路径，以及第二次调用保持确定性。

- [ ] **步骤 2：运行 CLI/E2E 测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_sampling_cli.py tests/test_phase15b1_e2e.py `
  -p no:cacheprovider
```

预期：解析器拒绝采样命令。

- [ ] **步骤 3：实现轻量采样 CLI 适配器**

增加必填参数 `--model-id`、`--version-id`、`--point-count`、`--random-seed`、`--actor`、`--operation-id`、`--request-id` 和 `--idempotency-key`。只有在验证完成后才输出最终 `representation.json` 路径。

- [ ] **步骤 4：编写中文操作与集成文档**

记录精确 CLI/API 示例、不可变版本和发布布局、当前投影语义、回滚行为、采样配置、稳定错误、审计查询、生产与实验规则，以及明确的 Phase 15B-2/15C 边界。

- [ ] **步骤 5：运行全部 Phase 15B-1 与聚焦 Phase 15 回归测试**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_resource_lock.py `
  tests/test_phase15b1_model_release.py `
  tests/test_phase15b1_release_cli_api.py `
  tests/test_phase15b1_sampling_kernel.py `
  tests/test_phase15b1_sampling_publication.py `
  tests/test_phase15b1_sampling_cli.py `
  tests/test_phase15b1_e2e.py `
  tests/test_phase15a_api.py tests/test_phase15a_cli.py `
  tests/test_phase15a_model_import.py tests/test_phase15a_audit.py `
  -p no:cacheprovider
```

- [ ] **步骤 6：执行一次最终仓库就绪门禁**

```powershell
uv run --extra test python -m pytest -q -p no:cacheprovider
uv run --extra test python -m compileall -q src tests
git diff --check
rg -n "T[B]D|T[O]DO|F[I]XME|implement[ ]later|fill[ ]in[ ]details" `
  src/pc_system tests docs/phase15b1-versioned-model-sampling.md README.md
```

预期：全部测试通过，编译检查退出码为 0，差异检查无输出，占位符扫描无匹配。

- [ ] **步骤 7：提交 Phase 15B-1 交付资料**

```powershell
git add -- `
  src/pc_system/commands/phase15.py src/pc_system/cli_parser.py src/pc_system/cli.py `
  tests/test_phase15b1_sampling_cli.py tests/test_phase15b1_e2e.py `
  docs/phase15b1-versioned-model-sampling.md README.md `
  docs/current-development-inventory.md docs/system-function-module-inventory.md
git commit -m "docs: complete Phase 15B-1 versioned sampling"
```

## 最终复审清单

- [ ] 模型版本目录在发布、回滚和采样前后保持字节级一致。
- [ ] 当前发布读取遇到缺失、畸形、跨模型或篡改证据时失败关闭。
- [ ] 并发发布请求不能同时推进同一个预期头。
- [ ] 回滚追加新的发布记录并保留全部历史。
- [ ] 采样只使用经过验证的不可变源工件。
- [ ] 相同源与配置在重复运行时产生字节级相同的采样点。
- [ ] 每个完整表达都不可变且经过指纹验证。
- [ ] 部分候选对读取方不可见，并且只能在所有权匹配时恢复。
- [ ] 在消费 API 正文之前强制执行生产身份和角色校验。
- [ ] CLI、API、领域服务、审计和文档契约一致。
- [ ] Phase 15B-2 检索特征和 Phase 15C 配准保持在范围之外。
- [ ] 合并前记录聚焦验证和全量验证证据。
