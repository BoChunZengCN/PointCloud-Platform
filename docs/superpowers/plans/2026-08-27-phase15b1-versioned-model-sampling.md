# Phase 15B-1 Versioned Model Sampling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为不可变 CAD 模型增加追加式发布/回滚历史，并生成可复现、不可覆盖的米制表面采样表达。

**Architecture:** 新建独立的模型资源锁、发布状态和采样模块。版本目录保持字节级不变；发布记录与采样表达使用原位候选、最终清单可见性标记和 Phase 15 审计恢复。API 只开放发布/回滚，采样先通过领域服务与 CLI 交付。

**Tech Stack:** Python 3.12、pytest、FastAPI、SHA-256 canonical JSON、Windows `msvcrt` / POSIX `fcntl` 内核字节锁、现有 Phase 15 审计账本。

**Spec:** `docs/superpowers/specs/2026-08-27-phase15b1-versioned-model-sampling-design.md`

## Global Constraints

- `models/<model_id>/versions/<version_id>` 在本阶段任何操作前后必须字节级不变。
- 发布记录、完整采样表达和资源锁文件不可删除或覆盖。
- 回滚创建新的 `release_id`，不得修改目标历史记录。
- 采样配置必须显式提供 `point_count` 与 `random_seed`；范围分别为 `1..500000` 与 `0..9223372036854775807`。
- 固定算法标识为 `sha256_area_weighted_v1`，输出坐标单位为米并保留 12 位小数。
- 采样 API、特征索引、Top-K 检索、法向量、FPFH 和配准不在计划范围内。
- 每个生产行为先写失败测试并观察预期 RED，再写最小实现。
- 只运行受影响测试；全仓 pytest 只在最终就绪门禁运行一次。

---

### Task 1: Permanent Cross-Platform Model Resource Locks

**Files:**
- Create: `src/pc_system/model_resource_lock.py`
- Create: `tests/test_phase15b1_resource_lock.py`

**Interfaces:**
- Consumes: `validate_identifier(value, label)` from `pc_system.identifiers`.
- Produces: `model_resource_lock(project_root: Path, resource_kind: str, *identifiers: str, timeout_seconds: float = 2.0) -> ContextManager[Path]`.
- Produces: permanent lock files under `reports/model_matching_resource_locks`.

- [ ] **Step 1: Write failing path and contention tests**

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

The mutation caught is replacing the permanent lock path, blocking indefinitely, or treating diagnostic metadata as ownership.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_resource_lock.py -p no:cacheprovider
```

Expected: collection fails because `pc_system.model_resource_lock` does not exist.

- [ ] **Step 3: Implement a non-blocking kernel byte-lock context manager**

Implement this public shape:

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

Requirements:

- validate `resource_kind` and every identifier before hashing canonical JSON into the bounded lock filename; for the release test identity the exact bytes are `{"identifiers":["pump-a"],"resource_kind":"release"}`;
- reject link/reparse-point lock roots and lock files;
- open one permanent plain file without truncating it;
- acquire non-blocking `msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)` on Windows or `fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)` on POSIX;
- retry with `time.monotonic()` until the explicit deadline;
- raise `ModelMatchingError("operation_busy", "Model resource is busy.")` on timeout;
- release only the kernel lock and descriptor; never unlink the lock file.

- [ ] **Step 4: Run lock tests GREEN**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit the resource-lock foundation**

```powershell
git add -- src/pc_system/model_resource_lock.py tests/test_phase15b1_resource_lock.py
git commit -m "feat: add model resource locks"
```

---

### Task 2: Immutable Model Release History and Rollback

**Files:**
- Create: `src/pc_system/model_release.py`
- Create: `tests/test_phase15b1_model_release.py`

**Interfaces:**
- Consumes: `load_model_asset`, `load_model_version`, `fingerprint_file`, `model_resource_lock`, Phase 15 audit lifecycle functions, and `Principal`.
- Produces: `release_model_version(project_root, *, model_id, version_id, release_id, action, expected_current_release_id, rollback_of_release_id, reason, principal, operation_id, request_id, idempotency_key) -> dict`.
- Produces: `load_current_model_release(project_root, model_id) -> dict | None`.
- Produces: `list_model_releases(project_root, model_id) -> list[dict]`.
- Produces: `list_version_release_status(project_root, model_id) -> list[dict]`.

- [ ] **Step 1: Write failing activation and rollback behavior tests**

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

Add separate tests for stale `expected_current_release_id`, rollback to the current release, cross-model release references, duplicate `release_id`, invalid reason, non-expert principal, projection tampering, release tampering, idempotent replay, and two concurrent updates from the same expected head.

The mutations caught are in-place history edits, last-writer-wins races, unverified projections, and rollback that silently changes version bytes.

- [ ] **Step 2: Run release tests and verify RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_model_release.py -p no:cacheprovider
```

Expected: collection fails because `pc_system.model_release` does not exist.

- [ ] **Step 3: Implement strict schemas, safe readers, and request freezing**

Define exact constants and public signature:

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

Use exact field sets from the spec. Freeze and validate all request fields before business lookup, construct a canonical audit payload, require role `expert`, and use stable Phase 15 error codes.

- [ ] **Step 4: Implement locked no-replace publication and replay recovery**

Inside `model_resource_lock(project_root, "release", model_id)`:

- validate the current projection against its release record;
- compare `expected_current_release_id` exactly;
- verify the target version through `load_model_version`;
- create `releases/<release_id>` once and persist an operation-owner envelope;
- publish `release.json` as the immutable visibility marker;
- atomically write `current_release.json` as a projection;
- append `model_release.published` or `model_release.rolled_back`;
- complete the canonical operation;
- on identical replay, validate the release, rebuild the projection if needed, ensure one business event, and complete the original operation;
- never recursively delete, rename, or take over a mismatched candidate.

- [ ] **Step 5: Run release and Phase 15A integrity tests GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_model_release.py `
  tests/test_phase15a_model_import.py `
  tests/test_phase15a_audit.py `
  -p no:cacheprovider
```

- [ ] **Step 6: Commit release history**

```powershell
git add -- src/pc_system/model_release.py tests/test_phase15b1_model_release.py
git commit -m "feat: add model release history"
```

---

### Task 3: Release CLI and Protected API

**Files:**
- Modify: `src/pc_system/commands/phase15.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Modify: `src/pc_system/api.py`
- Create: `tests/test_phase15b1_release_cli_api.py`

**Interfaces:**
- Consumes: all Task 2 release functions.
- Produces: CLI commands `release-model-version` and `list-model-releases`.
- Produces: `POST /model-library/models/{model_id}/releases`.
- Extends: `GET /model-library/models/{model_id}` with `current_release` and `release_history`.

- [ ] **Step 1: Write failing CLI and API contract tests**

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

Add tests for exact request shape, body-before-auth avoidance, role denial audit, development identity source, stable HTTP mapping, public history reads, and malformed optional identifiers.

- [ ] **Step 2: Run CLI/API tests and verify RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_release_cli_api.py -p no:cacheprovider
```

Expected: parser rejects the new command and API returns 404.

- [ ] **Step 3: Add exact parsers and thin command adapters**

`release-model-version` requires all audit identifiers, `--action`, `--version-id`, `--release-id`, `--reason`, and `--actor`. Optional `--expected-current-release-id` and `--rollback-of-release-id` pass `None` exactly when omitted. `list-model-releases` accepts only project and model identifiers and prints canonical JSON.

- [ ] **Step 4: Add API capture and route integration**

Authorize before reading the request body. Capture exact text fields and nullable release identifiers without implicit string conversion. Extend `_PHASE15_*` error groups for the new stable errors. Keep model reads public and verified.

- [ ] **Step 5: Run new and existing Phase 15 API/CLI tests GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_release_cli_api.py `
  tests/test_phase15a_api.py tests/test_phase15a_cli.py `
  -p no:cacheprovider
```

- [ ] **Step 6: Commit release interfaces**

```powershell
git add -- src/pc_system/commands/phase15.py src/pc_system/cli_parser.py src/pc_system/cli.py src/pc_system/api.py tests/test_phase15b1_release_cli_api.py
git commit -m "feat: expose model release controls"
```

---

### Task 4: Deterministic Mesh Sampling Kernel

**Files:**
- Modify: `src/pc_system/model_mesh.py`
- Create: `src/pc_system/model_sampling.py`
- Create: `tests/test_phase15b1_sampling_kernel.py`

**Interfaces:**
- Produces in `model_mesh.py`: `read_mesh_geometry_m(path: Path, declared_unit: str, *, reader: MeshReader) -> tuple[list[list[float]], list[list[int]]]`.
- Produces in `model_sampling.py`: `build_sampling_config(point_count: int, random_seed: int) -> dict`.
- Produces: `sampling_config_fingerprint(config: dict) -> str`.
- Produces: `sample_mesh_surface(vertices_m, faces, config) -> dict`.

- [ ] **Step 1: Write failing deterministic geometry tests**

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

The literal points above come from the published SHA-256 lane formula for config fingerprint `eaa98cd4674118a8cdca4215d9a4296ce1ec003ef15fa55a0a922a7550f97961`; the test must not calculate its own expected values. Add tests for fan triangulation, area selection across a 1:3 triangle pair, unit conversion, partial and total degeneration, `-0.0`, maximum boundaries, wrong exact types including booleans, non-finite vertices, and source order stability.

The mutations caught are use of `random`, vertex sampling instead of surface sampling, incorrect square-root barycentric mapping, unit omission, or unstable triangle ordering.

- [ ] **Step 2: Run kernel tests and verify RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_sampling_kernel.py -p no:cacheprovider
```

Expected: collection fails because sampling APIs do not exist.

- [ ] **Step 3: Expose validated meter geometry without duplicating validation**

Refactor `inspect_mesh` to call `read_mesh_geometry_m`; preserve every Phase 15A error and summary field. The new reader validates format, unit, vertices, faces and scales each vertex exactly once.

- [ ] **Step 4: Implement canonical config and SHA-256 lane generator**

Use this exact representation identity:

```python
config_fingerprint = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
representation_id = f"cad-sampled-{config_fingerprint}"
```

For each sample and lane, hash `b"phase15b1" + bytes.fromhex(config_fingerprint) + i.to_bytes(8, "big") + bytes([lane])`. Convert the first 8 digest bytes to `[0,1)` by dividing by `2**64`.

- [ ] **Step 5: Implement fan triangulation and area-weighted barycentric sampling**

Preserve face order, ignore exact zero-area triangles, fail if total area is zero, select by cumulative area, and calculate uniform barycentric coordinates with `sqrt(u)`. Round each meter coordinate to 12 decimals and map either signed zero to `0.0`.

- [ ] **Step 6: Run kernel and Phase 15A mesh tests GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_sampling_kernel.py tests/test_phase15a_model_mesh.py `
  -p no:cacheprovider
```

- [ ] **Step 7: Commit the deterministic sampling kernel**

```powershell
git add -- src/pc_system/model_mesh.py src/pc_system/model_sampling.py tests/test_phase15b1_sampling_kernel.py
git commit -m "feat: add deterministic model sampling"
```

---

### Task 5: Immutable Sampled Representation Publication

**Files:**
- Modify: `src/pc_system/model_sampling.py`
- Create: `tests/test_phase15b1_sampling_publication.py`

**Interfaces:**
- Consumes: Task 1 resource lock, Task 4 kernel, `load_model_version`, `fingerprint_file`, Phase 15 audit, `Principal`, and `MeshReader`.
- Produces: `sample_model_version(project_root, *, model_id, version_id, point_count, random_seed, principal, operation_id, request_id, idempotency_key, mesh_reader) -> dict`.
- Produces: `load_sampled_representation(project_root, model_id, version_id, representation_id) -> dict`.
- Produces: `list_sampled_representations(project_root, model_id, version_id) -> list[dict]`.

- [ ] **Step 1: Write failing publication, immutability, and recovery tests**

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

Add tests for source manifest tampering, sampled point tampering, representation tampering, same-request replay, same-config different-operation reuse rules, partial owner recovery, foreign owner rejection, failure before manifest publication, failure after manifest publication, and valid audit event sequence.

- [ ] **Step 2: Run publication tests and verify RED**

```powershell
uv run --extra test python -m pytest -q tests/test_phase15b1_sampling_publication.py -p no:cacheprovider
```

Expected: `sample_model_version` is missing.

- [ ] **Step 3: Implement strict sampled-point and representation readers**

Verify exact schema fields, path identities, plain directories/files, finite coordinates, point count, config fingerprint, source fingerprints, artifact URI and SHA-256. A directory without a valid final `representation.json` is not returned by list APIs.

- [ ] **Step 4: Implement audited in-place candidate publication**

Use the deterministic representation ID and Task 1 sampling resource lock. Freeze `operation_owner.json`; write sampled points; publish `representation.json` last as the visibility marker. Matching retry validates and resumes existing bytes. Mismatched owner or content fails closed. Never recursively delete or quarantine the candidate.

- [ ] **Step 5: Run publication and import-integrity tests GREEN**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_sampling_publication.py `
  tests/test_phase15b1_sampling_kernel.py `
  tests/test_phase15a_model_import.py tests/test_phase15a_audit.py `
  -p no:cacheprovider
```

- [ ] **Step 6: Commit immutable representations**

```powershell
git add -- src/pc_system/model_sampling.py tests/test_phase15b1_sampling_publication.py
git commit -m "feat: publish sampled model representations"
```

---

### Task 6: Sampling CLI, Documentation, and End-to-End Gate

**Files:**
- Modify: `src/pc_system/commands/phase15.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Create: `tests/test_phase15b1_sampling_cli.py`
- Create: `tests/test_phase15b1_e2e.py`
- Create: `docs/phase15b1-versioned-model-sampling.md`
- Modify: `README.md`
- Modify: `docs/current-development-inventory.md`
- Modify: `docs/system-function-module-inventory.md`

**Interfaces:**
- Consumes: `sample_model_version` and sampled representation queries.
- Produces: CLI `sample-model-version` and `list-model-representations`.
- Documents: operator activation, rollback, sampling, history inspection and recovery rules.

- [ ] **Step 1: Write failing sampling CLI and end-to-end tests**

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

Add CLI tests for required explicit point count/seed, stable exit code 2 on invalid config, printed representation path, and deterministic second invocation.

- [ ] **Step 2: Run CLI/E2E tests and verify RED**

```powershell
uv run --extra test python -m pytest -q `
  tests/test_phase15b1_sampling_cli.py tests/test_phase15b1_e2e.py `
  -p no:cacheprovider
```

Expected: parser rejects sampling commands.

- [ ] **Step 3: Implement thin sampling CLI adapters**

Add required arguments `--model-id`, `--version-id`, `--point-count`, `--random-seed`, `--actor`, `--operation-id`, `--request-id`, and `--idempotency-key`. Print the final `representation.json` path only after verified completion.

- [ ] **Step 4: Write Chinese operator and integrator documentation**

Document exact CLI/API examples, immutable version and release layout, current projection semantics, rollback behavior, sampling configuration, stable errors, audit lookup, production-versus-experiment rules, and explicit Phase 15B-2/15C boundaries.

- [ ] **Step 5: Run all Phase 15B-1 and focused Phase 15 regressions**

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

- [ ] **Step 6: Run the final repository readiness gate once**

```powershell
uv run --extra test python -m pytest -q -p no:cacheprovider
uv run --extra test python -m compileall -q src tests
git diff --check
rg -n "T[B]D|T[O]DO|F[I]XME|implement[ ]later|fill[ ]in[ ]details" `
  src/pc_system tests docs/phase15b1-versioned-model-sampling.md README.md
```

Expected: all tests pass, compile exits 0, diff check is empty, and placeholder scan has no matches.

- [ ] **Step 7: Commit Phase 15B-1 delivery artifacts**

```powershell
git add -- `
  src/pc_system/commands/phase15.py src/pc_system/cli_parser.py src/pc_system/cli.py `
  tests/test_phase15b1_sampling_cli.py tests/test_phase15b1_e2e.py `
  docs/phase15b1-versioned-model-sampling.md README.md `
  docs/current-development-inventory.md docs/system-function-module-inventory.md
git commit -m "docs: complete Phase 15B-1 versioned sampling"
```

## Final Review Checklist

- [ ] Model version directories are byte-identical before and after release, rollback, and sampling.
- [ ] Current release reads fail closed on missing, malformed, cross-model, or tampered evidence.
- [ ] Concurrent release requests cannot both advance the same expected head.
- [ ] Rollback appends a new release record and preserves all history.
- [ ] Sampling uses only verified immutable source artifacts.
- [ ] Same source/config produces byte-identical sampled points across repeated runs.
- [ ] Every complete representation is immutable and fingerprint-verified.
- [ ] Partial candidates are invisible to readers and recover only under matching ownership.
- [ ] Production identities and roles are enforced before API body consumption.
- [ ] CLI, API, domain, audit and documentation contracts agree.
- [ ] Phase 15B-2 retrieval features and Phase 15C registration remain out of scope.
- [ ] Focused and full verification evidence is captured before merge.
