# Phase 15A CAD Model Library and Audit Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an immutable STL/OBJ/PLY model catalog with trusted identities, safe unit-aware mesh inspection, idempotent imports, and complete append-only audit history.

**Architecture:** Phase 15A establishes the durable contracts that later retrieval and registration plans consume. Domain services own validation and atomic persistence; API and CLI remain thin adapters. Every mutation starts an auditable operation, writes hash-chained events, and either publishes one complete immutable artifact set or records a stable failure without partial business state.

**Tech Stack:** Python 3.11+, standard library, optional `trimesh>=4,<5` production mesh adapter, FastAPI, pytest, JSON/JSONL artifacts, existing atomic JSON writer.

## Global Constraints

- Supported first-party mesh inputs are exactly STL, OBJ, and PLY.
- All persisted geometric measurements use meters; accepted declared units are `mm`, `cm`, and `m`.
- Import unit conversion is provenance, not registration scaling; Phase 15 registration remains rigid rotation plus translation only.
- Published model versions are immutable. A changed source or metadata contract requires a new `version_id`.
- Every successful, failed, retried, denied, and idempotently replayed mutation records an audit event.
- Production identities come from server-side token bindings. Browser-supplied actor and role headers are accepted only in development mode and are marked as development identities.
- The core package remains importable without `trimesh`; only production mesh inspection raises `mesh_engine_unavailable` when the optional dependency is absent.
- STEP, IGES, CAD conversion, surface sampling, feature indexing, retrieval, registration, model binding, frontend workbenches, and optimization are outside this plan.
- No Phase 1-14 public behavior or artifact path may regress.

---

## File and Responsibility Map

Create these focused modules:

- `src/pc_system/model_matching_errors.py`: stable Phase 15 domain errors.
- `src/pc_system/model_matching_identity.py`: trusted principals, role checks, and production token bindings.
- `src/pc_system/model_matching_audit.py`: idempotent operations, hash-chained events, verification, and state projection.
- `src/pc_system/model_library.py`: model-asset catalog creation and reads.
- `src/pc_system/model_mesh.py`: supported formats, unit normalization, and lazy `trimesh` inspection.
- `src/pc_system/model_import.py`: immutable model-version staging and atomic publication.
- `src/pc_system/commands/phase15.py`: thin Phase 15A CLI adapters.

Modify integration files only where required:

- `pyproject.toml`: `models` optional dependency and test dependency.
- `src/pc_system/api.py`: Phase 15A read/write routes and error mapping.
- `src/pc_system/cli_parser.py`: `create-model-asset` and `import-model` parsers.
- `src/pc_system/cli.py`: identifier validation and dispatch.
- `README.md`, `docs/system-function-module-inventory.md`: Phase 15A discoverability without claiming all of Phase 15 is complete.

Tests are split by contract:

- `tests/test_phase15a_identity.py`
- `tests/test_phase15a_audit.py`
- `tests/test_phase15a_model_library.py`
- `tests/test_phase15a_model_mesh.py`
- `tests/test_phase15a_model_import.py`
- `tests/test_phase15a_api.py`
- `tests/test_phase15a_cli.py`
- `tests/test_phase15a_e2e.py`
- `tests/test_phase15a_docs.py`
- `tests/fixtures/models/minimal.obj`
- `tests/fixtures/models/minimal.stl`
- `tests/fixtures/models/minimal.ply`

---

### Task 1: Stable Errors and Trusted Principal Contract

**Files:**
- Create: `src/pc_system/model_matching_errors.py`
- Create: `src/pc_system/model_matching_identity.py`
- Create: `tests/test_phase15a_identity.py`

**Interfaces:**
- Consumes: `validate_identifier(value, label)` from `pc_system.identifiers`.
- Produces: `ModelMatchingError`, `Principal`, `parse_principal_bindings`, `resolve_principal`, `require_any_role`.

- [ ] **Step 1: Write failing identity and authorization tests**

```python
import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import (
    Principal,
    parse_principal_bindings,
    require_any_role,
    resolve_principal,
)


def test_production_principal_comes_from_server_binding():
    bindings = parse_principal_bindings(
        '{"token-a":{"actor_id":"alice","roles":["expert","operator"]}}'
    )
    principal = resolve_principal(
        run_mode="production",
        token="token-a",
        actor_header="mallory",
        roles_header="approver",
        bindings=bindings,
    )
    assert principal == Principal(
        actor_id="alice",
        roles=frozenset({"expert", "operator"}),
        source="configured_token",
    )


def test_development_headers_are_marked_and_validated():
    principal = resolve_principal(
        run_mode="development",
        token=None,
        actor_header="dev-user",
        roles_header="expert,operator",
        bindings={},
    )
    assert principal.source == "development_headers"
    assert principal.roles == frozenset({"expert", "operator"})


def test_missing_production_binding_is_denied():
    with pytest.raises(ModelMatchingError) as exc_info:
        resolve_principal(
            run_mode="production",
            token="unknown",
            actor_header="alice",
            roles_header="expert",
            bindings={},
        )
    assert exc_info.value.code == "permission_denied"


def test_role_check_requires_one_allowed_role():
    principal = Principal("alice", frozenset({"operator"}), "configured_token")
    with pytest.raises(ModelMatchingError) as exc_info:
        require_any_role(principal, {"expert", "approver"})
    assert exc_info.value.code == "permission_denied"
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_identity.py -v --basetemp .pytest-tmp-p15a-task1-red`

Expected: FAIL during collection because the Phase 15 identity modules do not exist.

- [ ] **Step 3: Implement the stable error and principal types**

```python
# src/pc_system/model_matching_errors.py
class ModelMatchingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
```

```python
# src/pc_system/model_matching_identity.py
import json
from dataclasses import dataclass

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError

ALLOWED_ROLES = frozenset({"operator", "expert", "approver", "auditor"})


@dataclass(frozen=True)
class Principal:
    actor_id: str
    roles: frozenset[str]
    source: str


def _principal(actor_id: str, roles: list[str] | set[str], source: str) -> Principal:
    actor_id = validate_identifier(actor_id, "actor_id")
    normalized = frozenset(str(role).strip() for role in roles if str(role).strip())
    if not normalized or not normalized <= ALLOWED_ROLES:
        raise ModelMatchingError("permission_denied", "Principal roles are invalid or empty.")
    return Principal(actor_id=actor_id, roles=normalized, source=source)


def parse_principal_bindings(raw: str | None) -> dict[str, Principal]:
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("PC_SYSTEM_PRINCIPALS_JSON must be a JSON object.")
    return {
        token: _principal(item["actor_id"], item["roles"], "configured_token")
        for token, item in payload.items()
    }


def resolve_principal(*, run_mode: str, token: str | None,
                      actor_header: str | None, roles_header: str | None,
                      bindings: dict[str, Principal]) -> Principal:
    if token and token in bindings:
        return bindings[token]
    if run_mode == "development" and actor_header and roles_header:
        return _principal(actor_header, roles_header.split(","), "development_headers")
    raise ModelMatchingError("permission_denied", "A trusted Phase 15 principal is required.")


def require_any_role(principal: Principal, allowed: set[str]) -> None:
    if not principal.roles.intersection(allowed):
        raise ModelMatchingError("permission_denied", "Principal lacks a required role.")
```

- [ ] **Step 4: Run identity tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_identity.py -v --basetemp .pytest-tmp-p15a-task1-green`

Expected: all five tests PASS.

- [ ] **Step 5: Commit the identity contract**

```powershell
git add src/pc_system/model_matching_errors.py src/pc_system/model_matching_identity.py tests/test_phase15a_identity.py
git commit -m "feat: add Phase 15 trusted principals"
```

---

### Task 2: Idempotent Hash-Chained Audit Operations

**Files:**
- Create: `src/pc_system/model_matching_audit.py`
- Create: `tests/test_phase15a_audit.py`
- Modify: `docs/superpowers/specs/2026-07-22-phase15-model-library-retrieval-registration-design.md`
- Modify: `docs/superpowers/plans/2026-07-22-phase15a-cad-model-library-audit.md`

**Interfaces:**
- Consumes: `Principal`, `ModelMatchingError`, `validate_identifier`, `write_json`.
- Produces: `start_operation`, `append_operation_event`, `complete_operation`, `fail_operation`, `record_denied_operation`, `load_operation`, `read_operation_events`, `verify_operation_chain`.

- [ ] **Step 1: Write failing audit lifecycle tests**

```python
import json

import pytest

from pc_system.model_matching_audit import (
    append_operation_event,
    complete_operation,
    load_operation,
    record_denied_operation,
    read_operation_events,
    start_operation,
    verify_operation_chain,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


PRINCIPAL = Principal("alice", frozenset({"expert"}), "configured_token")


def test_operation_events_are_ordered_and_hash_chained(tmp_path):
    operation, replayed = start_operation(
        tmp_path,
        operation_id="op-001",
        operation_type="model_asset.create",
        principal=PRINCIPAL,
        request_id="request-001",
        idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is False
    append_operation_event(
        tmp_path, "op-001", "model_asset.validated", {"model_id": "pump-a"}
    )
    complete_operation(tmp_path, "op-001", {"model_id": "pump-a"})
    events = read_operation_events(tmp_path, "op-001")
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[1]["previous_event_hash"] == events[0]["event_hash"]
    assert verify_operation_chain(events) is True
    assert load_operation(tmp_path, "op-001")["status"] == "completed"


def test_same_idempotency_request_replays_and_is_audited(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    count_before = len(read_operation_events(tmp_path, "op-001"))
    operation, replayed = start_operation(
        tmp_path, operation_id="op-002", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-002", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    assert replayed is True
    assert operation["operation_id"] == "op-001"
    events = read_operation_events(tmp_path, "op-001")
    assert len(events) == count_before + 1
    assert events[-1]["event_type"] == "operation.replayed"
    assert events[-1]["details"]["requested_operation_id"] == "op-002"


def test_same_operation_with_changed_payload_is_rejected(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        start_operation(
            tmp_path, operation_id="op-002", operation_type="model_asset.create",
            principal=PRINCIPAL, request_id="request-002", idempotency_key="idem-001",
            request_payload={"model_id": "pump-b"},
        )
    assert exc_info.value.code == "idempotency_conflict"


def test_tampered_event_breaks_verification(tmp_path):
    start_operation(
        tmp_path, operation_id="op-001", operation_type="model_asset.create",
        principal=PRINCIPAL, request_id="request-001", idempotency_key="idem-001",
        request_payload={"model_id": "pump-a"},
    )
    events = read_operation_events(tmp_path, "op-001")
    events[0]["details"]["request_id"] = "changed"
    assert verify_operation_chain(events) is False


def test_denied_request_records_system_audit_without_raw_token(tmp_path):
    operation_id = record_denied_operation(
        tmp_path, request_id="request-denied-001", route="POST /model-library/models",
        token="secret-token", reason="permission_denied",
    )
    operation = load_operation(tmp_path, operation_id)
    events = read_operation_events(tmp_path, operation_id)
    serialized = json.dumps({"operation": operation, "events": events})
    assert operation["status"] == "failed"
    assert events[-1]["details"]["code"] == "permission_denied"
    assert "secret-token" not in serialized
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_audit.py -v --basetemp .pytest-tmp-p15a-task2-red`

Expected: FAIL during collection because `model_matching_audit` does not exist.

- [ ] **Step 3: Implement canonical hashing and paths**

```python
def _operation_dir(project_root: Path, operation_id: str) -> Path:
    operation_id = validate_identifier(operation_id, "operation_id")
    return project_root / "reports" / "model_matching_operations" / operation_id


def _idempotency_path(project_root: Path, idempotency_key: str) -> Path:
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return project_root / "reports" / "model_matching_idempotency" / f"{key_hash}.json"


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_hash(event: dict) -> str:
    return _canonical_hash({key: value for key, value in event.items() if key != "event_hash"})
```

Use `datetime.now(timezone.utc).isoformat()` for timestamps. Write one JSON object per line with sorted keys. Flush and `os.fsync` before releasing the per-operation lock.

- [ ] **Step 4: Implement idempotent operation start and event append**

```python
def start_operation(project_root: Path, *, operation_id: str,
                    operation_type: str, principal: Principal,
                    request_id: str, idempotency_key: str,
                    request_payload: dict) -> tuple[dict, bool]:
    operation_id = validate_identifier(operation_id, "operation_id")
    request_id = validate_identifier(request_id, "request_id")
    idempotency_key = validate_identifier(idempotency_key, "idempotency_key")
    request_fingerprint = _canonical_hash(request_payload)
    idempotency_path = _idempotency_path(project_root, idempotency_key)
    if idempotency_path.exists():
        index = json.loads(idempotency_path.read_text(encoding="utf-8"))
        existing = load_operation(project_root, index["operation_id"])
        if (existing["operation_type"], existing["request_fingerprint"]) != (
            operation_type, request_fingerprint
        ):
            append_operation_event(
                project_root, existing["operation_id"], "operation.idempotency_conflict",
                {"requested_operation_id": operation_id, "request_id": request_id,
                 "request_fingerprint": request_fingerprint},
            )
            raise ModelMatchingError("idempotency_conflict", "Idempotency key is already bound to a different request.")
        append_operation_event(
            project_root, existing["operation_id"], "operation.replayed",
            {"requested_operation_id": operation_id, "request_id": request_id,
             "actor_id": principal.actor_id},
        )
        return existing, True
    root = _operation_dir(project_root, operation_id)
    root.mkdir(parents=True, exist_ok=False)
    operation = {
        "schema_version": "1.0", "operation_id": operation_id,
        "operation_type": operation_type, "status": "running",
        "actor_id": principal.actor_id, "roles": sorted(principal.roles),
        "principal_source": principal.source, "request_id": request_id,
        "idempotency_key_hash": hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
        "request_fingerprint": request_fingerprint,
        "started_at": utc_now(), "completed_at": None,
        "result": None, "error": None,
    }
    write_json(operation, root / "operation.json")
    _claim_idempotency_index(
        idempotency_path,
        {"operation_id": operation_id, "request_fingerprint": request_fingerprint},
    )
    append_operation_event(project_root, operation_id, "operation.started", {
        "request_id": request_id, "request_fingerprint": request_fingerprint,
    })
    return operation, False
```

`_claim_idempotency_index` writes canonical JSON to a unique temporary file in the same `reports/model_matching_idempotency` directory, flushes and `fsync`s it, then atomically publishes without replacement through a platform-verified hard-link adapter. Never use `os.replace` or an overwrite fallback. If another request wins the hard-link race, remove only the unstarted operation directory created by the losing request, then execute the existing-index replay/conflict branch. Always attempt temporary-name cleanup; a crash or cleanup error before publication leaves only an invisible partial temp, while one after publication leaves a complete final index plus a harmless temp hard link. POSIX must `fsync` the parent directory where supported. Windows guarantees process-crash safety only on supported local NTFS-like storage.

The no-replace adapter returns one of three explicit outcomes. `not_published` means no final name became visible and is the only state in which the candidate operation may be discarded. `published_confirmed` means the complete final name is visible and directory durability was confirmed. `published_unconfirmed` means the complete final name became visible but the post-link directory `fsync` failed; callers must preserve the referenced operation, return and separately audit stable `audit_persistence_error`, and allow later deterministic replay/recovery. An exception after link creation must never be interpreted as `not_published`.

Use the stable external lock path `reports/model_matching_locks/<operation_id>.lock`; it is outside the operation directory so cleanup and atomic rename cannot change lock identity. Lock files are persistent coordination artifacts and are never unlinked. Acquire a nonblocking OS kernel byte lock through a focused standard-library adapter (`msvcrt` on Windows, `fcntl` on POSIX). Kernel ownership alone proves liveness. Owner token, PID, purpose, and acquisition time are diagnostic metadata written only after acquisition. Always release the kernel lock and close its descriptor in `finally`, including when metadata write or `fsync` fails.

Hold the indexed operation's kernel lock continuously across idempotency-index publication and the durable `operation.started` append. A concurrent append or live-initializer replay returns `operation_busy`; no event is partially written. A replayer may reconcile a no-event initializer only after acquiring that same indexed-operation kernel lock; elapsed time alone must never terminalize a live initializer. Validate the index fingerprint against the indexed operation and validate hash plus lifecycle semantics before append. Reject `operation.started` after any terminal event.

The Task 2 regression set must cover a live delayed initializer, abandoned-owner restart, lock metadata write/fsync failure, stale and incomplete lock metadata, lifecycle rejection, separately audited integrity failures, non-`FileExistsError` index I/O cleanup/audit, index fingerprint mismatch, concurrent denied-marker recovery, corrupt-marker isolation/reporting with retry-safe audit receipts, malformed JSONL and event schemas, denied-marker identifier/fingerprint validation, and projection repair under the operation lock.

Add a startup/write preflight suitable for reuse by later adapters. It verifies both nonblocking kernel-lock exclusion and hard-link no-replace behavior, caches only a successful result per project root and process, and returns stable `audit_persistence_error` without silent degradation when unavailable. Test winner races, crash before publication, crash after publication before temp cleanup, cleanup interruption, partial temp leftovers, complete destination visibility, unsupported hard links, capability-probe failure/retry, and no overwrite.

- [ ] **Step 5: Implement completion, failure, reads, and verification**

`complete_operation` appends `operation.completed`, then atomically updates `operation.json` to `completed`. `fail_operation` appends `operation.failed` with stable code and message, then writes `failed`. Both reject terminal operations with `operation_immutable`.

`verify_operation_chain(events)` recomputes every hash, verifies monotonically increasing sequence numbers starting at 1, and verifies each `previous_event_hash` (`None` for the first event).

`record_denied_operation` generates `denied-<uuid>` server-side, uses principal `system-api` with source `system`, stores only a SHA-256 token fingerprint, appends `security.permission_denied`, and finishes the operation as failed with code `permission_denied`. It never records the raw credential or untrusted actor/role headers.

- [ ] **Step 6: Run audit tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_audit.py -v --basetemp .pytest-tmp-p15a-task2-green`

Expected: all four tests PASS.

- [ ] **Step 7: Commit the audit foundation**

```powershell
git add src/pc_system/model_matching_audit.py tests/test_phase15a_audit.py docs/superpowers/specs/2026-07-22-phase15-model-library-retrieval-registration-design.md docs/superpowers/plans/2026-07-22-phase15a-cad-model-library-audit.md
git commit -m "fix: converge audit publication integrity"
```

---

### Task 3: Immutable Model Asset Catalog

**Files:**
- Create: `src/pc_system/model_library.py`
- Create: `tests/test_phase15a_model_library.py`

**Interfaces:**
- Consumes: Task 1 identity/error contract and Task 2 audit operations.
- Produces: `create_model_asset`, `load_model_asset`, `list_model_assets`, `model_asset_path`, `model_version_dir`.

- [ ] **Step 1: Write failing model-asset tests**

```python
import pytest

from pc_system.model_library import (
    create_model_asset,
    list_model_assets,
    load_model_asset,
)
from pc_system.model_matching_audit import load_operation, read_operation_events
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")


def create_pump(project):
    return create_model_asset(
        project,
        model_id="pump-a",
        display_name="Pump A",
        category_id="pump",
        manufacturer="Acme",
        model_number="A-100",
        keywords=["centrifugal", "process pump"],
        tags=["pump", "motor-coupled"],
        principal=EXPERT,
        operation_id="op-model-001",
        request_id="request-model-001",
        idempotency_key="idem-model-001",
    )


def test_create_and_list_model_asset(tmp_path):
    created = create_pump(tmp_path)
    assert created["model_id"] == "pump-a"
    assert created["tags"] == ["motor-coupled", "pump"]
    assert load_model_asset(tmp_path, "pump-a") == created
    assert [item["model_id"] for item in list_model_assets(tmp_path)] == ["pump-a"]


def test_duplicate_model_identity_is_immutable(tmp_path):
    create_pump(tmp_path)
    with pytest.raises(ModelMatchingError) as exc_info:
        create_model_asset(
            tmp_path, model_id="pump-a", display_name="Different",
            category_id="pump", manufacturer="Acme", model_number="A-100",
            keywords=[], tags=[], principal=EXPERT,
            operation_id="op-model-002", request_id="request-model-002",
            idempotency_key="idem-model-002",
        )
    assert exc_info.value.code == "model_exists"


def test_model_creation_requires_expert_role(tmp_path):
    operator = Principal("bob", frozenset({"operator"}), "configured_token")
    with pytest.raises(ModelMatchingError) as exc_info:
        create_model_asset(
            tmp_path, model_id="pump-a", display_name="Pump A",
            category_id="pump", manufacturer="Acme", model_number="A-100",
            keywords=[], tags=[], principal=operator,
            operation_id="op-model-001", request_id="request-model-001",
            idempotency_key="idem-model-001",
        )
    assert exc_info.value.code == "permission_denied"
    assert load_operation(tmp_path, "op-model-001")["status"] == "failed"
    assert read_operation_events(tmp_path, "op-model-001")[-1]["details"]["code"] == "permission_denied"
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_model_library.py -v --basetemp .pytest-tmp-p15a-task3-red`

Expected: FAIL during collection because `model_library` does not exist.

- [ ] **Step 3: Implement paths and normalized metadata**

```python
def model_asset_path(project_root: Path, model_id: str) -> Path:
    model_id = validate_identifier(model_id, "model_id")
    return project_root / "models" / model_id / "model_asset.json"


def model_version_dir(project_root: Path, model_id: str, version_id: str) -> Path:
    validate_identifier(model_id, "model_id")
    validate_identifier(version_id, "version_id")
    return project_root / "models" / model_id / "versions" / version_id


def _terms(values: list[str], label: str) -> list[str]:
    normalized = sorted({str(value).strip().lower() for value in values if str(value).strip()})
    if any(len(value) > 128 for value in normalized):
        raise ValueError(f"{label} entries must not exceed 128 characters.")
    return normalized
```

- [ ] **Step 4: Implement audited creation and deterministic reads**

`create_model_asset` must:

1. validate only the path-forming `operation_id`, then start `model_asset.create` before authorization and business validation;
2. call `require_any_role(principal, {"expert"})` inside the audited operation;
3. validate all identifiers and non-empty display name;
4. reject an existing asset with `model_exists`;
5. write `models/<model_id>/model_asset.json` atomically;
6. append `model_asset.created` with the manifest fingerprint;
7. complete the operation with `model_id` and artifact path;
8. on exception, record the stable failure before re-raising.

The manifest uses schema `1.0`, sorted tags/keywords, lifecycle status `active`, and UTC timestamps. `list_model_assets` sorts by `model_id` and ignores temporary directories.

- [ ] **Step 5: Run model catalog and audit tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_model_library.py tests/test_phase15a_audit.py -v --basetemp .pytest-tmp-p15a-task3-green`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the model catalog**

```powershell
git add src/pc_system/model_library.py tests/test_phase15a_model_library.py
git commit -m "feat: add immutable model asset catalog"
```

---

### Task 4: STL, OBJ, and PLY Mesh Inspection

**Files:**
- Modify: `pyproject.toml`
- Create: `src/pc_system/model_mesh.py`
- Create: `tests/test_phase15a_model_mesh.py`
- Create: `tests/fixtures/models/minimal.obj`
- Create: `tests/fixtures/models/minimal.stl`
- Create: `tests/fixtures/models/minimal.ply`

**Interfaces:**
- Consumes: `ModelMatchingError`.
- Produces: `MeshReader`, `SUPPORTED_MESH_FORMATS`, `UNIT_SCALE_TO_METERS`, `inspect_mesh`, `trimesh_mesh_reader`.

- [ ] **Step 1: Add deterministic text fixtures**

`tests/fixtures/models/minimal.obj`:

```text
v 0 0 0
v 1000 0 0
v 0 1000 0
f 1 2 3
```

`tests/fixtures/models/minimal.stl`:

```text
solid triangle
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 1000 0 0
      vertex 0 1000 0
    endloop
  endfacet
endsolid triangle
```

`tests/fixtures/models/minimal.ply`:

```text
ply
format ascii 1.0
element vertex 3
property float x
property float y
property float z
element face 1
property list uchar int vertex_indices
end_header
0 0 0
1000 0 0
0 1000 0
3 0 1 2
```

- [ ] **Step 2: Write failing format, unit, and geometry tests**

```python
from pathlib import Path

import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_mesh import inspect_mesh, trimesh_mesh_reader


FIXTURES = Path(__file__).parent / "fixtures" / "models"


@pytest.mark.parametrize("name", ["minimal.obj", "minimal.stl", "minimal.ply"])
def test_supported_meshes_are_inspected_in_meters(name):
    result = inspect_mesh(FIXTURES / name, "mm", reader=trimesh_mesh_reader)
    assert result["coordinate_unit"] == "m"
    assert result["vertex_count"] == 3
    assert result["face_count"] == 1
    assert result["bounds_m"]["max"] == [1.0, 1.0, 0.0]


def test_unknown_unit_is_rejected():
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(FIXTURES / "minimal.obj", "inch", reader=trimesh_mesh_reader)
    assert exc_info.value.code == "invalid_model_unit"


def test_unknown_format_is_rejected(tmp_path):
    path = tmp_path / "model.step"
    path.write_text("not a supported mesh", encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=trimesh_mesh_reader)
    assert exc_info.value.code == "invalid_model_format"


def test_non_finite_or_empty_geometry_is_rejected(tmp_path):
    def empty_reader(_path):
        return {"vertices": [], "faces": []}
    path = tmp_path / "empty.obj"
    path.write_text("# empty", encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        inspect_mesh(path, "m", reader=empty_reader)
    assert exc_info.value.code == "invalid_model_geometry"
```

- [ ] **Step 3: Run tests and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_model_mesh.py -v --basetemp .pytest-tmp-p15a-task4-red`

Expected: FAIL during collection because `model_mesh` does not exist.

- [ ] **Step 4: Add the optional production dependency**

```toml
[project.optional-dependencies]
models = ["trimesh>=4,<5"]
test = [
  "pytest>=8,<9",
  "fastapi>=0.115,<1",
  "httpx>=0.27,<1",
  "trimesh>=4,<5",
]
```

Keep all existing extras and test dependencies unchanged apart from adding `models` and the test-only `trimesh` line.

- [ ] **Step 5: Implement the lazy adapter and meter normalization**

```python
import math
from pathlib import Path
from typing import Callable

from pc_system.model_matching_errors import ModelMatchingError

MeshReader = Callable[[Path], dict]
SUPPORTED_MESH_FORMATS = frozenset({".stl", ".obj", ".ply"})
UNIT_SCALE_TO_METERS = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def trimesh_mesh_reader(path: Path) -> dict:
    try:
        import trimesh
    except ImportError as exc:
        raise ModelMatchingError(
            "mesh_engine_unavailable", "Install pc-system[models] to inspect production meshes."
        ) from exc
    loaded = trimesh.load_mesh(path, process=False)
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    return {
        "vertices": loaded.vertices.tolist(),
        "faces": loaded.faces.tolist(),
        "is_watertight": bool(loaded.is_watertight),
    }


def inspect_mesh(path: Path, declared_unit: str, *, reader: MeshReader) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_MESH_FORMATS:
        raise ModelMatchingError("invalid_model_format", f"Unsupported model format: {suffix}")
    unit = str(declared_unit).strip().lower()
    if unit not in UNIT_SCALE_TO_METERS:
        raise ModelMatchingError("invalid_model_unit", f"Unsupported model unit: {declared_unit}")
    mesh = reader(path)
    vertices = mesh.get("vertices", [])
    faces = mesh.get("faces", [])
    if not vertices or not faces or any(
        len(vertex) != 3 or not all(math.isfinite(float(value)) for value in vertex)
        for vertex in vertices
    ):
        raise ModelMatchingError("invalid_model_geometry", "Mesh must contain finite vertices and faces.")
    scale = UNIT_SCALE_TO_METERS[unit]
    minimum = [min(float(vertex[axis]) for vertex in vertices) * scale for axis in range(3)]
    maximum = [max(float(vertex[axis]) for vertex in vertices) * scale for axis in range(3)]
    return {
        "schema_version": "1.0", "source_format": suffix[1:],
        "declared_unit": unit, "coordinate_unit": "m", "unit_scale_to_m": scale,
        "vertex_count": len(vertices), "face_count": len(faces),
        "bounds_m": {"min": minimum, "max": maximum},
        "is_watertight": mesh.get("is_watertight"),
    }
```

- [ ] **Step 6: Install the updated test extra and run mesh tests**

Run: `.\.venv\Scripts\python.exe -m pip install -e ".[test]"`

Expected: exit code 0 and `trimesh` installed in the worktree environment.

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_model_mesh.py -v --basetemp .pytest-tmp-p15a-task4-green`

Expected: all parametrized format and validation tests PASS.

- [ ] **Step 7: Commit mesh inspection**

```powershell
git add pyproject.toml src/pc_system/model_mesh.py tests/test_phase15a_model_mesh.py tests/fixtures/models
git commit -m "feat: inspect supported CAD meshes"
```

---

### Task 5: Atomic Immutable Model-Version Import

**Files:**
- Create: `src/pc_system/model_import.py`
- Create: `tests/test_phase15a_model_import.py`

**Interfaces:**
- Consumes: `model_version_dir`, `load_model_asset`, `inspect_mesh`, `MeshReader`, Task 1 identity/error, Task 2 audit.
- Produces: `import_model_version`, `load_model_version`, `list_model_versions`, `fingerprint_file`.

- [ ] **Step 1: Write failing import, replay, and rollback tests**

```python
from pathlib import Path

import pytest

from pc_system.model_import import import_model_version, list_model_versions, load_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def create_asset(project):
    create_model_asset(
        project, model_id="pump-a", display_name="Pump A", category_id="pump",
        manufacturer="Acme", model_number="A-100", keywords=["centrifugal"],
        tags=["pump"], principal=EXPERT, operation_id="op-asset-001",
        request_id="request-asset-001", idempotency_key="idem-asset-001",
    )


def fake_reader(_path):
    return {"vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]], "faces": [[0, 1, 2]]}


def test_import_publishes_immutable_version_and_source_copy(tmp_path):
    create_asset(tmp_path)
    version = import_model_version(
        tmp_path, model_id="pump-a", version_id="v1", source_path=FIXTURE,
        declared_unit="mm", license_name="internal", provenance={"supplier":"Acme"},
        principal=EXPERT, operation_id="op-import-001", request_id="request-import-001",
        idempotency_key="idem-import-001", mesh_reader=fake_reader,
    )
    root = tmp_path / "models" / "pump-a" / "versions" / "v1"
    assert version["status"] == "imported"
    assert version["source_format"] == "obj"
    assert version["coordinate_unit"] == "m"
    assert (root / version["artifacts"]["source"]).read_bytes() == FIXTURE.read_bytes()
    assert [item["version_id"] for item in list_model_versions(tmp_path, "pump-a")] == ["v1"]


def test_same_idempotent_import_returns_existing_version(tmp_path):
    create_asset(tmp_path)
    arguments = dict(
        model_id="pump-a", version_id="v1", source_path=FIXTURE,
        declared_unit="mm", license_name="internal", provenance={"supplier":"Acme"},
        principal=EXPERT, operation_id="op-import-001", request_id="request-import-001",
        idempotency_key="idem-import-001", mesh_reader=fake_reader,
    )
    first = import_model_version(tmp_path, **arguments)
    second = import_model_version(tmp_path, **arguments)
    assert second == first


def test_idempotent_replay_rejects_changed_source_bytes(tmp_path):
    create_asset(tmp_path)
    source = tmp_path / "input.obj"
    source.write_bytes(FIXTURE.read_bytes())
    arguments = dict(
        model_id="pump-a", version_id="v1", source_path=source,
        declared_unit="mm", license_name="internal", provenance={}, principal=EXPERT,
        operation_id="op-import-001", request_id="request-import-001",
        idempotency_key="idem-import-001", mesh_reader=fake_reader,
    )
    first = import_model_version(tmp_path, **arguments)
    source.write_text("v 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n", encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **arguments)
    assert exc_info.value.code == "idempotency_conflict"
    persisted = load_model_version(tmp_path, "pump-a", "v1")
    assert persisted["source_fingerprint"] == first["source_fingerprint"]


def test_existing_version_cannot_be_overwritten(tmp_path):
    create_asset(tmp_path)
    import_model_version(
        tmp_path, model_id="pump-a", version_id="v1", source_path=FIXTURE,
        declared_unit="mm", license_name="internal", provenance={}, principal=EXPERT,
        operation_id="op-import-001", request_id="request-import-001",
        idempotency_key="idem-import-001", mesh_reader=fake_reader,
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(
            tmp_path, model_id="pump-a", version_id="v1", source_path=FIXTURE,
            declared_unit="m", license_name="internal", provenance={}, principal=EXPERT,
            operation_id="op-import-002", request_id="request-import-002",
            idempotency_key="idem-import-002", mesh_reader=fake_reader,
        )
    assert exc_info.value.code == "model_version_exists"


def test_new_version_can_explicitly_supersede_existing_version(tmp_path):
    create_asset(tmp_path)
    common = dict(
        model_id="pump-a", source_path=FIXTURE, declared_unit="mm",
        license_name="internal", provenance={}, principal=EXPERT,
        mesh_reader=fake_reader,
    )
    import_model_version(
        tmp_path, version_id="v1", operation_id="op-import-001",
        request_id="request-import-001", idempotency_key="idem-import-001",
        **common,
    )
    version = import_model_version(
        tmp_path, version_id="v2", supersedes_version_id="v1",
        operation_id="op-import-002", request_id="request-import-002",
        idempotency_key="idem-import-002", **common,
    )
    assert version["supersedes_version_id"] == "v1"


def test_failed_inspection_leaves_no_final_version(tmp_path):
    create_asset(tmp_path)
    def failing_reader(_path):
        raise ModelMatchingError("invalid_model_geometry", "broken mesh")
    with pytest.raises(ModelMatchingError):
        import_model_version(
            tmp_path, model_id="pump-a", version_id="v1", source_path=FIXTURE,
            declared_unit="mm", license_name="internal", provenance={}, principal=EXPERT,
            operation_id="op-import-001", request_id="request-import-001",
            idempotency_key="idem-import-001", mesh_reader=failing_reader,
        )
    assert not (tmp_path / "models" / "pump-a" / "versions" / "v1").exists()
    assert (tmp_path / "reports" / "model_matching_operations" / "op-import-001" / "operation.json").is_file()


def test_replay_recovers_version_published_before_audit_completion(tmp_path, monkeypatch):
    import pc_system.model_import as model_import_module
    create_asset(tmp_path)
    original_append = model_import_module.append_operation_event
    failed_once = {"value": False}
    def interrupted_append(project_root, operation_id, event_type, details):
        if event_type == "model_version.published" and not failed_once["value"]:
            failed_once["value"] = True
            raise OSError("simulated audit interruption")
        return original_append(project_root, operation_id, event_type, details)
    monkeypatch.setattr(model_import_module, "append_operation_event", interrupted_append)
    arguments = dict(
        model_id="pump-a", version_id="v1", source_path=FIXTURE,
        declared_unit="mm", license_name="internal", provenance={}, principal=EXPERT,
        operation_id="op-import-001", request_id="request-import-001",
        idempotency_key="idem-import-001", mesh_reader=fake_reader,
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        import_model_version(tmp_path, **arguments)
    assert exc_info.value.code == "publication_recovery_required"
    assert (tmp_path / "models" / "pump-a" / "versions" / "v1" / "model_manifest.json").is_file()
    monkeypatch.setattr(model_import_module, "append_operation_event", original_append)
    recovered = import_model_version(tmp_path, **arguments)
    assert recovered["version_id"] == "v1"
    assert load_operation(tmp_path, "op-import-001")["status"] == "completed"
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_model_import.py -v --basetemp .pytest-tmp-p15a-task5-red`

Expected: FAIL during collection because `model_import` does not exist.

- [ ] **Step 3: Implement file fingerprinting and immutable reads**

```python
def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_version(project_root: Path, model_id: str, version_id: str) -> dict:
    path = model_version_dir(project_root, model_id, version_id) / "model_manifest.json"
    if not path.is_file():
        raise ModelMatchingError("model_version_not_found", f"Model version not found: {model_id}/{version_id}")
    return json.loads(path.read_text(encoding="utf-8"))
```

`list_model_versions` scans only `versions/*/model_manifest.json`, loads manifests, and sorts by `version_id`.

- [ ] **Step 4: Implement staged import and atomic publication**

`import_model_version` must:

1. start `model_version.import` before authorization, identifier validation, or source reading, using the untrusted request values only as JSON audit details and never as paths;
2. require `expert` inside the audited operation;
3. validate model/version IDs and an optional `supersedes_version_id`, then require the referenced model/version to exist;
4. compute the source SHA-256 inside the running operation and append `model_source.fingerprinted`;
5. on replay, recompute the current source fingerprint and reject changed source bytes with audited `idempotency_conflict`;
6. return `load_model_version` when the same completed operation replays with the same source fingerprint;
7. when a replay finds a final manifest owned by the same still-running operation, append `model_version.recovered`, complete the operation, and return that immutable version;
8. verify the model asset exists;
9. reserve the target version with an exclusive `.version-<version_id>.lock` under the model root;
10. reject an existing target owned by another operation with `model_version_exists`;
11. create `.p15-model-<operation_id>` beside the final version directory;
12. call `inspect_mesh` before publishing;
13. copy the source to `source/model.<suffix>`;
14. write `source_geometry.json` and `model_manifest.json` in staging; the manifest includes `operation_id` and `supersedes_version_id`;
15. append `model_version.prepared` before the atomic rename;
16. atomically rename staging to the final version directory;
17. append `model_version.published` with all artifact fingerprints;
18. complete the operation;
19. on failure before final rename, remove only this operation's staging directory, record failure, and release the reservation;
20. on audit interruption after final rename, preserve the immutable final version, leave the canonical operation recoverable, and raise `publication_recovery_required` so the same idempotent request can reconcile it.

Use this exact public signature so CLI, API, and tests share one contract:

```text
import_model_version(project_root: Path, *, model_id: str, version_id: str, source_path: Path, declared_unit: str, license_name: str, provenance: dict, principal: Principal, operation_id: str, request_id: str, idempotency_key: str, supersedes_version_id: str | None = None, mesh_reader: MeshReader = trimesh_mesh_reader) -> dict
```

The manifest records `schema_version`, IDs, source format/path/fingerprint, declared unit, coordinate unit `m`, unit scale, license, provenance, imported timestamp, status `imported`, the supplied optional `supersedes_version_id`, `index_status="not_indexed"`, and relative artifacts.

- [ ] **Step 5: Run import tests and focused regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_model_import.py tests/test_phase15a_model_library.py tests/test_phase15a_audit.py -v --basetemp .pytest-tmp-p15a-task5-green`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit immutable import**

```powershell
git add src/pc_system/model_import.py tests/test_phase15a_model_import.py
git commit -m "feat: import immutable model versions"
```

---

### Task 6: Protected Model-Library API

**Files:**
- Modify: `src/pc_system/api.py`
- Create: `tests/test_phase15a_api.py`

**Interfaces:**
- Consumes: Tasks 1-5 domain functions.
- Produces: Phase 15A API reads and protected expert writes.

- [ ] **Step 1: Write failing API flow and security tests**

```python
import json
from pathlib import Path

from fastapi.testclient import TestClient

from pc_system.api import create_app


EXPERT_HEADERS = {
    "X-Actor-ID": "alice",
    "X-Actor-Roles": "expert",
}


def test_development_api_create_and_import_flow(tmp_path):
    staged = tmp_path / "imports" / "models" / "minimal.obj"
    staged.parent.mkdir(parents=True)
    staged.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    client = TestClient(create_app(tmp_path, run_mode="development"))
    created = client.post(
        "/model-library/models", headers=EXPERT_HEADERS,
        json={
            "model_id":"pump-a", "display_name":"Pump A", "category_id":"pump",
            "manufacturer":"Acme", "model_number":"A-100", "keywords":["centrifugal"],
            "tags":["pump"], "operation_id":"op-model-001",
            "request_id":"request-model-001", "idempotency_key":"idem-model-001",
        },
    )
    imported = client.post(
        "/model-library/models/pump-a/versions", headers=EXPERT_HEADERS,
        json={
            "version_id":"v1", "staged_source":"imports/models/minimal.obj",
            "declared_unit":"m", "license":"internal", "provenance":{"supplier":"Acme"},
            "operation_id":"op-import-001", "request_id":"request-import-001",
            "idempotency_key":"idem-import-001",
        },
    )
    assert created.status_code == 201
    assert imported.status_code == 201
    assert client.get("/model-library").json()["model_count"] == 1
    assert client.get("/model-library/models/pump-a").json()["version_count"] == 1


def test_operator_cannot_create_model(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))
    response = client.post(
        "/model-library/models",
        headers={"X-Actor-ID":"bob", "X-Actor-Roles":"operator"},
        json={},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


def test_production_ignores_spoofed_actor_headers(tmp_path):
    bindings = {"token-a": {"actor_id":"alice", "roles":["expert"]}}
    client = TestClient(
        create_app(
            tmp_path, api_key="legacy-key", run_mode="production",
            principal_bindings=bindings,
        )
    )
    response = client.post(
        "/model-library/models",
        headers={"X-API-Key":"unknown", "X-Actor-ID":"alice", "X-Actor-Roles":"expert"},
        json={},
    )
    assert response.status_code == 403
    denied = list((tmp_path / "reports" / "model_matching_operations").glob("denied-*"))
    assert len(denied) == 1
    assert "unknown" not in (denied[0] / "events.jsonl").read_text(encoding="utf-8")


def test_import_path_cannot_escape_staging_root(tmp_path):
    client = TestClient(create_app(tmp_path, run_mode="development"))
    response = client.post(
        "/model-library/models/pump-a/versions", headers=EXPERT_HEADERS,
        json={"staged_source":"../secret.obj"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_staged_source"
```

- [ ] **Step 2: Run API tests and verify missing-route failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_api.py -v --basetemp .pytest-tmp-p15a-task6-red`

Expected: FAIL because the model-library routes return 404 or `create_app` rejects the new parameter.

- [ ] **Step 3: Add principal resolution and stable HTTP mapping**

Extend `create_app` without breaking existing callers:

```python
def create_app(project_root: Path, api_key: str | None = None,
               run_mode: str | None = None,
               principal_bindings: dict | None = None) -> FastAPI:
```

Normalize `principal_bindings` into `Principal` values. Add a helper that accepts `X-API-Key`, `X-Actor-ID`, and `X-Actor-Roles`, calls `resolve_principal`, and requires the route's role before reading mutation payload fields.

When trusted-principal resolution fails, call `record_denied_operation` with a server-generated operation ID, route template, request ID, raw token for one-way hashing, and stable reason. Return 403 only after the denied audit is durable. Do not read the mutation payload or record actor/role headers from the rejected request.

Map errors:

- `permission_denied` -> 403
- `model_not_found`, `model_version_not_found`, `operation_not_found` -> 404
- `model_exists`, `model_version_exists`, `idempotency_conflict`, `operation_busy` -> 409
- validation, invalid source, format, unit, geometry -> 400
- mesh adapter runtime failure -> 503

- [ ] **Step 4: Add safe source-path resolution and API routes**

Safe staged source resolver:

```python
def _staged_model_source(project_root: Path, relative: str) -> Path:
    staging_root = (project_root / "imports" / "models").resolve()
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(staging_root)
    except ValueError as exc:
        raise ModelMatchingError(
            "invalid_staged_source", "Model source must be inside imports/models."
        ) from exc
    if not candidate.is_file():
        raise ModelMatchingError("invalid_staged_source", "Staged model source does not exist.")
    return candidate
```

Add:

```text
GET  /model-library
POST /model-library/models
GET  /model-library/models/<model_id>
POST /model-library/models/<model_id>/versions
GET  /audit/operations/<operation_id>
```

The model detail response includes `model`, `version_count`, and sorted `versions`. The audit response includes the operation, events, and `chain_valid`.

- [ ] **Step 5: Run Phase 15A API and existing API security tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_api.py tests/test_api.py tests/test_api_phase4.py tests/test_phase14_correction_api.py -v --basetemp .pytest-tmp-p15a-task6-green`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the API**

```powershell
git add src/pc_system/api.py tests/test_phase15a_api.py
git commit -m "feat: expose protected model library APIs"
```

---

### Task 7: Model Catalog and Import CLI

**Files:**
- Create: `src/pc_system/commands/phase15.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Create: `tests/test_phase15a_cli.py`

**Interfaces:**
- Consumes: Tasks 1-5 domain functions.
- Produces: `create-model-asset` and `import-model` commands.

- [ ] **Step 1: Write failing CLI flow and error tests**

```python
import json
from pathlib import Path

from pc_system.cli import main


FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def test_cli_create_asset_and_import_version(tmp_path):
    assert main([
        "create-model-asset", "--project-root", str(tmp_path),
        "--model-id", "pump-a", "--display-name", "Pump A",
        "--category-id", "pump", "--manufacturer", "Acme",
        "--model-number", "A-100", "--keyword", "centrifugal",
        "--tag", "pump", "--actor", "alice",
        "--operation-id", "op-model-001", "--request-id", "request-model-001",
        "--idempotency-key", "idem-model-001",
    ]) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"supplier":"Acme"}), encoding="utf-8")
    assert main([
        "import-model", "--project-root", str(tmp_path),
        "--model-id", "pump-a", "--version-id", "v1",
        "--source", str(FIXTURE), "--unit", "mm", "--license", "internal",
        "--provenance", str(provenance), "--actor", "alice",
        "--operation-id", "op-import-001", "--request-id", "request-import-001",
        "--idempotency-key", "idem-import-001",
    ]) == 0
    assert (tmp_path / "models" / "pump-a" / "versions" / "v1" / "model_manifest.json").is_file()


def test_cli_rejects_invalid_model_identifier_before_writing(tmp_path):
    assert main([
        "create-model-asset", "--project-root", str(tmp_path),
        "--model-id", "../escape", "--display-name", "Bad",
        "--category-id", "pump", "--actor", "alice",
        "--operation-id", "op-model-001", "--request-id", "request-model-001",
        "--idempotency-key", "idem-model-001",
    ]) == 2
    assert not (tmp_path.parent / "escape").exists()
```

- [ ] **Step 2: Run tests and verify parser rejection**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_cli.py -v --basetemp .pytest-tmp-p15a-task7-red`

Expected: FAIL because argparse reports invalid choices for both commands.

- [ ] **Step 3: Add exact parser definitions**

`create-model-asset` requires project root, model ID, display name, category ID, actor, operation ID, request ID, and idempotency key. Manufacturer and model number default to empty strings. `--keyword` and `--tag` are repeatable.

`import-model` requires project root, model/version IDs, source path, unit (`mm|cm|m`), license, actor, operation ID, request ID, and idempotency key. `--provenance` is an optional JSON object path.

Add `model_id`, `version_id`, `operation_id`, `request_id`, and `idempotency_key` to centralized identifier validation.

- [ ] **Step 4: Implement thin command functions**

```python
def run_create_model_asset(project_root: Path, *, model_id: str,
                           display_name: str, category_id: str,
                           manufacturer: str, model_number: str,
                           keywords: list[str], tags: list[str], actor: str,
                           operation_id: str, request_id: str,
                           idempotency_key: str) -> int:
    principal = Principal(actor, frozenset({"expert"}), "cli")
    asset = create_model_asset(
        project_root, model_id=model_id, display_name=display_name,
        category_id=category_id, manufacturer=manufacturer,
        model_number=model_number, keywords=keywords, tags=tags,
        principal=principal, operation_id=operation_id,
        request_id=request_id, idempotency_key=idempotency_key,
    )
    print(model_asset_path(project_root, asset["model_id"]))
    return 0
```

`run_import_model` reads one JSON object for provenance, invokes `import_model_version` with `trimesh_mesh_reader`, prints the final manifest path, and returns 0.

- [ ] **Step 5: Add CLI dispatch and stable domain-error handling**

Import `ModelMatchingError` in `cli.py` and add this branch before the existing `except RuntimeError` branch so every Phase 15A command returns exit code 2 for rejected requests:

```python
    except ModelMatchingError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
```

Dispatch both commands to the thin functions with named arguments, then run focused regressions:

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_cli.py tests/test_cli_error_handling.py tests/test_phase14_correction_cli.py -v --basetemp .pytest-tmp-p15a-task7-green`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit the CLI**

```powershell
git add src/pc_system/commands/phase15.py src/pc_system/cli_parser.py src/pc_system/cli.py tests/test_phase15a_cli.py
git commit -m "feat: add Phase 15A model library CLI"
```

---

### Task 8: End-to-End Audit, Documentation, and Full Regression

**Files:**
- Create: `tests/test_phase15a_e2e.py`
- Create: `docs/phase15-model-library.md`
- Modify: `README.md`
- Modify: `docs/system-function-module-inventory.md`
- Create: `tests/test_phase15a_docs.py`

**Interfaces:**
- Consumes: complete Phase 15A domain, API, CLI, persistence, identity, and audit contracts.
- Produces: documented and regression-tested Phase 15A model-library foundation.

- [ ] **Step 1: Write end-to-end audit and rollback tests**

```python
import json
from pathlib import Path

from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import read_operation_events, verify_operation_chain
from pc_system.model_matching_identity import Principal


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def test_model_library_import_is_fully_auditable(tmp_path):
    create_model_asset(
        tmp_path, model_id="pump-a", display_name="Pump A", category_id="pump",
        manufacturer="Acme", model_number="A-100", keywords=["centrifugal"],
        tags=["pump"], principal=EXPERT, operation_id="op-asset-001",
        request_id="request-asset-001", idempotency_key="idem-asset-001",
    )
    import_model_version(
        tmp_path, model_id="pump-a", version_id="v1", source_path=FIXTURE,
        declared_unit="mm", license_name="internal", provenance={"supplier":"Acme"},
        principal=EXPERT, operation_id="op-import-001", request_id="request-import-001",
        idempotency_key="idem-import-001",
    )
    events = read_operation_events(tmp_path, "op-import-001")
    assert verify_operation_chain(events)
    assert events[-1]["event_type"] == "operation.completed"
    manifest = json.loads(
        (tmp_path / "models" / "pump-a" / "versions" / "v1" / "model_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["operation_id"] == "op-import-001"
    assert manifest["source_fingerprint"]
    assert manifest["artifacts"]["source"] == "source/model.obj"
```

Add a second test that injects `write_json` failure before stage rename and asserts:

- no final version directory exists;
- the source asset remains unchanged;
- the operation is `failed` with stable code;
- event-chain verification remains true;
- no path outside `models/<model_id>` and `reports/model_matching_operations/<operation_id>` was created.

- [ ] **Step 2: Run end-to-end tests and fix only demonstrated cross-layer defects**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_e2e.py -v --basetemp .pytest-tmp-p15a-task8-red`

Expected before fixes: targeted failures identify any audit ordering or cleanup gaps.

Apply the smallest correction in the owning Phase 15A module, then run:

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_e2e.py tests/test_phase15a_model_import.py tests/test_phase15a_audit.py -v --basetemp .pytest-tmp-p15a-task8-green`

Expected: all selected tests PASS.

- [ ] **Step 3: Write failing documentation contract tests**

```python
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_phase15a_docs_cover_formats_units_immutability_identity_and_audit():
    document = (ROOT / "docs" / "phase15-model-library.md").read_text(encoding="utf-8")
    for term in (
        "STL", "OBJ", "PLY", "mm", "cm", "model_manifest.json",
        "immutable", "operation_id", "idempotency", "hash chain",
        "configured_token", "imports/models", "model_version_exists",
        "Phase 15B",
    ):
        assert term in document


def test_readme_does_not_claim_full_phase15_is_complete():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Phase 15A" in readme
    assert "CAD model library foundation" in readme
    assert "Phase 15 |" not in readme or "已完成 / Done" not in next_phase15_row(readme)
```

Implement `next_phase15_row` in the test as a five-line helper that returns the Markdown table row beginning with `| Phase 15 |`, or an empty string.

- [ ] **Step 4: Run docs tests and verify missing-document failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_docs.py -v --basetemp .pytest-tmp-p15a-docs-red`

Expected: FAIL because `docs/phase15-model-library.md` does not exist.

- [ ] **Step 5: Write operator and integrator documentation**

Document:

- Phase 15A scope and explicit Phase 15B-F boundaries;
- supported formats and optional `pc-system[models]` installation;
- units and meter normalization;
- create/import CLI examples with non-secret sample IDs;
- safe API staging under `imports/models`;
- production principal-binding JSON structure with redacted tokens;
- immutable asset/version paths;
- operation, event, idempotency, failure, retry, and audit verification behavior;
- stable errors and recovery steps;
- how later feature indexing consumes `model_manifest.json` and `source_geometry.json`.

Update the README phase table to mark only `Phase 15A / CAD model library foundation` complete. Update the module inventory with M1 complete and M2-M6 planned.

- [ ] **Step 6: Run documentation and all Phase 15A tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase15a_docs.py tests/test_phase15a_identity.py tests/test_phase15a_audit.py tests/test_phase15a_model_library.py tests/test_phase15a_model_mesh.py tests/test_phase15a_model_import.py tests/test_phase15a_api.py tests/test_phase15a_cli.py tests/test_phase15a_e2e.py -q --basetemp .pytest-tmp-p15a-focused`

Expected: all Phase 15A tests PASS.

- [ ] **Step 7: Run complete regression and syntax checks**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-p15a-full`

Expected: all tests PASS; the known Starlette `httpx` deprecation warning may remain.

Run: `.\.venv\Scripts\python.exe -m compileall -q src tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 8: Verify scope and absence of plan placeholders**

Run: `rg -n "T[B]D|T[O]DO|F[I]XME|implement[ ]later|fill[ ]in[ ]details" src/pc_system tests docs/phase15-model-library.md README.md`

Expected: no Phase 15A placeholder matches.

Run: `git status --short`

Expected: only the Phase 15A files named in this plan are modified or untracked.

- [ ] **Step 9: Commit documentation and final regression artifacts**

```powershell
git add docs/phase15-model-library.md docs/system-function-module-inventory.md README.md tests/test_phase15a_docs.py tests/test_phase15a_e2e.py
git commit -m "docs: complete Phase 15A model library"
```

---

## Phase 15A Final Review Checklist

- [ ] Model assets use stable validated IDs and deterministic normalized metadata.
- [ ] STL, OBJ, and PLY are accepted; other formats and unknown units are rejected.
- [ ] Meter-normalized bounds preserve the declared import scale in provenance.
- [ ] Source files and model manifests have SHA-256 fingerprints.
- [ ] Published model versions cannot be overwritten.
- [ ] Failed imports leave no final business artifacts.
- [ ] Production Phase 15 writes use server-configured principals.
- [ ] Development header identities are explicitly marked as development identities.
- [ ] Only experts can create assets or import versions.
- [ ] Every mutation has a canonical `operation_id` and append-only event chain.
- [ ] Same idempotency key plus same payload replays; changed payload conflicts.
- [ ] Audit chain verification detects modified, missing, or reordered events.
- [ ] API model paths cannot escape `imports/models`.
- [ ] CLI, API, and domain services share the same validation rules.
- [ ] Documentation claims only Phase 15A, not complete retrieval or registration.
- [ ] Complete pytest, compile, and Git diff checks pass.

## Follow-Up Plan Sequence

After Phase 15A is implemented and reviewed, create separate implementation plans in this order:

1. Phase 15B: deterministic mesh surface sampling, versioned feature profiles, and hybrid Top-K retrieval.
2. Phase 15C: Open3D FPFH/RANSAC/FGR coarse registration, multi-scale ICP, and residual gates.
3. Phase 15D: match decisions, immutable model bindings, business workbench, and professional lab.
4. Phase 15E: `scanned_reference` point-cloud templates using the shared representation contract.
5. Phase 15F: feedback datasets, bounded optimization, Champion/Challenger approval, and rollback.
