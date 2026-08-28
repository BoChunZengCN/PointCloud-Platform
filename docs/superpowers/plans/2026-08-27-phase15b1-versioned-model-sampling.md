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
- 按规格中的可信项目存储命名空间协调平台进程，不声称抵御具有同等目录写权限的恶意本地进程；
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

### 任务 2A：发布状态分类器与关系图链头

**文件：**
- 新建：`src/pc_system/model_release_state.py`
- 新建：`tests/test_phase15b1_release_state.py`

**接口：**
- 产出：`ReleaseState`，仅包含 `NO_CANDIDATE`、`OWNED_CANDIDATE`、`RELEASE_VISIBLE_OLD_PROJECTION`、`RELEASE_PROJECTED`、`RELEASE_ANCESTOR`、`COMPLETED`。
- 产出：`ReleaseChain(ordered_release_ids: tuple[str, ...], head_release_id: str | None)`。
- 产出：`build_release_chain(releases: list[dict]) -> ReleaseChain`，只根据 `previous_release_id` 计算唯一根、唯一后继和唯一头。
- 产出：`classify_release_state(*, expected_owner: dict, actual_owner: dict | None, expected_release: dict, actual_release: dict | None, projected_release_id: str | None, operation_status: str, business_event_matches: bool, completed_result_matches: bool, chain: ReleaseChain) -> ReleaseState`。

- [ ] **步骤 1：编写关系图和互斥状态失败测试**

```python
def test_release_chain_ignores_started_time_when_computing_head():
    releases = [
        release("release-001", previous=None, created_at="2026-08-27T10:00:02+00:00"),
        release("release-002", previous="release-001", created_at="2026-08-27T10:00:01+00:00"),
    ]
    chain = build_release_chain(releases)
    assert chain.ordered_release_ids == ("release-001", "release-002")
    assert chain.head_release_id == "release-002"


def test_release_chain_rejects_branch():
    releases = [
        release("release-001", previous=None),
        release("release-002", previous="release-001"),
        release("release-003", previous="release-001"),
    ]
    with pytest.raises(ModelMatchingError) as exc_info:
        build_release_chain(releases)
    assert exc_info.value.code == "model_release_integrity_error"


def test_visible_owner_with_no_release_is_owned_candidate():
    assert classify_release_state(
        expected_owner=OWNER, actual_owner=OWNER,
        expected_release=RELEASE, actual_release=None,
        projected_release_id=None, operation_status="running",
        business_event_matches=False, completed_result_matches=False,
        chain=ReleaseChain((), None),
    ) is ReleaseState.OWNED_CANDIDATE
```

补充环、孤立节点、重复编号、两个根、owner 不匹配、发布记录局部匹配但非全等、旧投影、本投影、合法后继和完成结果不匹配测试。每个输入组合必须只落入一个状态；无法归类时返回 `model_release_integrity_error`。

- [ ] **步骤 2：运行状态测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_release_state.py -p no:cacheprovider
```

预期：由于 `pc_system.model_release_state` 尚不存在，测试在收集阶段失败。

- [ ] **步骤 3：实现纯关系图验证器**

`build_release_chain` 必须通过编号映射和 `previous_release_id` 反向索引验证唯一根、每个节点最多一个后继、无环且根遍历覆盖全部节点。`created_at` 不参与图验证，只允许调用方在返回展示结果时排序。

- [ ] **步骤 4：实现纯状态分类器**

分类器不读取或写入文件。owner 或 release 存在时必须与完整预期字典逐字段相等；`RELEASE_ANCESTOR` 只在关系图证明投影头从本发布可达时成立；`COMPLETED` 同时要求业务事件和完成结果匹配。任何矛盾证据失败关闭。

- [ ] **步骤 5：运行状态测试并确认 GREEN**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_release_state.py -p no:cacheprovider
```

- [ ] **步骤 6：提交状态分类基础**

```powershell
git add -- src/pc_system/model_release_state.py tests/test_phase15b1_release_state.py
git commit -m "feat: add model release state classifier"
```

---

### 任务 2B：不可变发布事务、恢复与查询

**文件：**
- 新建或重写：`src/pc_system/model_release.py`
- 新建或重写：`tests/test_phase15b1_model_release.py`

**接口：**
- 依赖：任务 2A 的 `build_release_chain` 与 `classify_release_state`，以及 `load_model_asset`、`load_model_version`、`fingerprint_file`、`model_resource_lock`、Phase 15 审计生命周期函数和 `Principal`。
- 产出：`release_model_version(project_root, *, model_id, version_id, release_id, action, expected_current_release_id, rollback_of_release_id, reason, principal, operation_id, request_id, idempotency_key) -> dict`。
- 产出：`load_current_model_release(project_root, model_id) -> dict | None`。
- 产出：`list_model_releases(project_root, model_id) -> list[dict]`。
- 产出：`list_version_release_status(project_root, model_id) -> list[dict]`。

- [ ] **步骤 1：编写不可变发布、恢复和篡改失败测试**

```python
def test_retry_recovers_owner_visible_after_directory_sync_failure(tmp_path, monkeypatch):
    inject_owner_directory_sync_failure_once(monkeypatch)
    with pytest.raises(ModelMatchingError) as exc_info:
        activate_v1(tmp_path)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-release-001")["status"] == "running"
    assert activate_v1(tmp_path)["release_id"] == "release-001"


def test_successor_with_earlier_started_time_remains_graph_head(tmp_path):
    first = activate_with_started_time(tmp_path, "release-001", "10:00:02")
    second = activate_with_started_time(
        tmp_path, "release-002", "10:00:01",
        expected_current_release_id=first["release_id"],
    )
    assert load_current_model_release(tmp_path, "pump-a") == second
```

保留并扩展既有测试：发布 v1、升级 v2、追加回滚 v1；版本目录字节不变；陈旧头、并发推进、回滚到当前、跨模型回滚、重复发布编号、无效原因、非专家、幂等重放；owner/release/projection/audit 四个中断时点；较新后继投影恢复；完整 canonical 字段篡改；失败审计持久化错误不得静默。架构重设计后必须额外覆盖：可见 release 搭配结构合法但字段被修改的 owner 时原操作保持 `running`；只有 owner、release 与另一 verified canonical 操作完整闭环时才返回 `VERIFIED_FOREIGN`；无法证明归属时进入 `UNCERTAIN`。

- [ ] **步骤 2：运行发布事务测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_model_release.py -p no:cacheprovider
```

预期：owner 同步故障、启动时间逆序和完整状态分类测试失败，证明旧实现仍依赖局部分支与时间排序。

- [ ] **步骤 3：实现完整预期证据构造与原子 no-replace 发布**

从冻结请求、canonical `operation.started`、主体、当前版本清单及 SHA-256 重建完整 owner/release 字典。所有者信封和发布记录先写入并同步同目录临时文件，再通过硬链接 no-replace 发布最终路径并同步目录。最终 owner/release 一旦可见，任何异常都保持原操作 `running`。

- [ ] **步骤 4：实现单一磁盘状态采集与分类循环**

在模型资源锁内，每次动作前重新读取 owner、release、projection、审计和全部发布图，调用任务 2A 分类器。每个状态只允许一个动作：发布 owner、发布 release、推进旧投影、补齐业务事件、完成审计或返回已完成记录；动作后重新分类。不同请求观察到运行中的可见 owner/release 时返回 `publication_recovery_required`，不得创建候选或推进投影。

异常处理使用独立四状态归属分类：`ABSENT`、`OWNED`、`VERIFIED_FOREIGN`、`UNCERTAIN`。`FOREIGN` 不得根据 owner 与预期不相等直接推断；必须读取另一操作的 verified snapshot，并用请求指纹、主体、时间、owner 和可见 release 构成完整证据闭环。只有 `ABSENT` 与 `VERIFIED_FOREIGN` 允许终止当前操作；`OWNED` 与 `UNCERTAIN` 始终保留 `running`。

- [ ] **步骤 5：实现审计绑定查询与图链头投影校验**

公开读取逐条验证完整结构、版本清单指纹、canonical start、唯一业务事件、发布指纹及完成结果；用 `build_release_chain` 计算唯一头并与 `current_release.json` 比较。接口返回可以按 `created_at`、`release_id` 稳定展示排序，但 `is_current`、回滚合法性和后继判断只能使用关系图。

- [ ] **步骤 6：运行发布与 Phase 15A 聚焦回归并确认 GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_release_state.py `
  tests/test_phase15b1_model_release.py `
  tests/test_phase15a_model_import.py `
  tests/test_phase15a_audit.py `
  -p no:cacheprovider
```

- [ ] **步骤 7：提交发布历史功能**

```powershell
git add -- src/pc_system/model_release.py tests/test_phase15b1_model_release.py
git commit -m "feat: add recoverable model release history"
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

补充源清单篡改、采样点篡改、表达清单篡改、相同请求重放、同配置不同操作的复用规则、生产归属协同重绑定、canonical 字节冲突、部分所有者恢复、外部所有者拒绝、清单发布前失败、清单发布后失败和有效审计事件顺序测试。

- [ ] **步骤 2：运行发布测试并确认 RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_sampling_publication.py -p no:cacheprovider
```

预期：`sample_model_version` 尚不存在。

- [ ] **步骤 3：实现严格的采样点与表达读取器**

验证精确结构字段、路径身份、普通目录/文件、有限坐标、点数、配置指纹、源指纹、工件 URI 和 SHA-256。公开读取必须把 owner、表达清单和采样点的原始文件指纹绑定到原生产操作审计；没有有效最终 `representation.json` 的目录不得由列表接口返回。

- [ ] **步骤 4：实现受审计的原位候选发布**

使用确定性表达编号和任务 1 的采样资源锁。以 `ABSENT / OWNED_RECOVERABLE / VERIFIED_PUBLISHED / UNCERTAIN` 四态先分类后执行；冻结 `operation_owner.json`，写入采样点，最后发布 `representation.json` 作为可见性标记。no-replace 冲突必须比较 canonical 原始字节。匹配的原生产操作重试验证并继续；已验证外部表达只写 `model_sampling.representation_reused`，不得重写生产事件；不确定候选失败关闭但保持运行操作可重试。绝不递归删除或隔离候选目录。

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
