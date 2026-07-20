# Phase 14 Segmentation Correction Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a human-in-the-loop correction workflow that starts from a completed Phase 13A segmentation, supports simple exact-point corrections and recovery, and publishes immutable reviewed labels for Phase 13B evaluation and future controlled training.

**Architecture:** Add focused domain modules for session persistence, event replay, review prioritization, and immutable publication. Expose those modules through protected FastAPI routes and CLI commands, then provide a standalone native-Canvas correction workbench that operates on paginated points carrying exact `source_point_index` values.

**Tech Stack:** Python 3.11 standard library, FastAPI, pytest, vanilla HTML/CSS/JavaScript, Node.js for isolated frontend behavior tests.

## Global Constraints

- Python remains `>=3.11`; do not add runtime dependencies.
- Raw LAS/LAZ and Phase 13A run artifacts remain immutable.
- The browser edits only the bounded point set evaluated by the selected Phase 13A run.
- Every browser selection resolves to exact `source_point_index` values.
- Session event replay must be deterministic for the same baseline and event log.
- One sample has one active editor; every write uses `expected_revision`.
- Published releases are immutable and may only be superseded by a new release.
- Golden-regression labels are evaluation-only and cannot drive training or parameter search.
- Phase 14 writes a future-training contract but does not train or promote models.
- All API write routes use the existing API-key protection.
- Every task follows red-green-refactor and leaves the existing Phase 1–13B tests passing.

## File Map

- Create `src/pc_system/segmentation_corrections.py`: session paths, lifecycle, source-point loading, baseline construction, locking, and materialized draft persistence.
- Create `src/pc_system/segmentation_correction_events.py`: correction-event validation, deterministic replay, undo/redo, restore semantics, object summaries, and draft fingerprints.
- Create `src/pc_system/segmentation_review_queue.py`: deterministic issue suggestions, priorities, queue filtering, and correction diffs.
- Create `src/pc_system/segmentation_correction_releases.py`: reviewed-revision freeze, immutable release, derived benchmark, feedback export, training policy, and publication task state.
- Create `src/pc_system/commands/phase14.py`: thin CLI adapters for create, apply, submit, publish, and publication retry.
- Modify `src/pc_system/cli_parser.py`: Phase 14 command definitions.
- Modify `src/pc_system/cli.py`: identifier validation and Phase 14 dispatch.
- Modify `src/pc_system/api.py`: read/write routes and stable domain-error mapping.
- Create `frontend/correction.html`: standalone correction workbench structure.
- Create `frontend/correction.css`: responsive three-pane correction layout.
- Create `frontend/segmentation-correction.js`: pure CommonJS/browser-compatible state and geometry helpers.
- Create `frontend/correction.js`: API orchestration and native Canvas interaction.
- Modify `frontend/index.html`: discoverable link to correction workbench.
- Create `tests/phase14_helpers.py`: reusable completed-run and correction-session fixtures.
- Create focused test files named in each task below.
- Create `docs/phase14-segmentation-correction-loop.md`: operator, API, artifact, recovery, and future-training documentation.
- Modify `README.md` and `docs/system-function-module-inventory.md`: Phase 14 status and entry points.

---

### Task 1: Correction Session and Automatic Baseline

**Files:**
- Create: `src/pc_system/segmentation_corrections.py`
- Create: `tests/phase14_helpers.py`
- Create: `tests/test_phase14_correction_sessions.py`

**Interfaces:**
- Consumes: Phase 13A `segmentation_run.json`, `object_segments.json`, membership artifacts, and `sample_points_from_source(path: Path, max_points: int) -> list[dict]`.
- Produces: `CorrectionError(code: str, message: str)`, `create_correction_session(project_root: Path, *, asset_id: str, run_id: str, session_id: str, sample_id: str, actor: str, benchmark_id: str | None = None, lock_ttl_seconds: int = 900, baseline_release_id: str | None = None) -> dict`, `load_correction_session(project_root: Path, asset_id: str, session_id: str) -> dict`, `load_correction_baseline(project_root: Path, asset_id: str, session_id: str) -> dict`, `load_correction_points(project_root: Path, asset_id: str, session_id: str, *, offset: int = 0, limit: int = 10000) -> dict`, `load_correction_objects(project_root: Path, asset_id: str, session_id: str) -> dict`, and `list_correction_sessions(project_root: Path, asset_id: str) -> list[dict]`.

- [ ] **Step 1: Write fixture helpers for a deterministic completed Phase 13A run**

```python
def write_completed_run(project: Path, *, asset_id: str = "scan", run_id: str = "run-001") -> None:
    points = [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.0, "z": 0.0},
        {"x": 5.0, "y": 0.0, "z": 0.0},
    ]
    source = project / "samples" / "scan.points.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(points), encoding="utf-8")
    run_segmentation(
        project,
        asset_id=asset_id,
        asset_version="v1",
        source_uri=str(source),
        points=points,
        config={"engine": "builtin_geometric", "distance_threshold": 0.2, "min_points": 1, "max_points": 100},
        run_id=run_id,
    )
```

- [ ] **Step 2: Write failing session and baseline tests**

```python
def test_create_session_materializes_exact_automatic_baseline(tmp_path):
    write_completed_run(tmp_path)
    session = create_correction_session(
        tmp_path, asset_id="scan", run_id="run-001",
        session_id="session-001", sample_id="sample-001", actor="alice",
    )
    points = load_correction_points(tmp_path, "scan", "session-001")
    assert session["status"] == "draft"
    assert session["revision"] == 0
    assert session["active_editor"] == "alice"
    assert [point["source_point_index"] for point in points] == [0, 1, 2]
    assert all({"baseline", "draft"} <= point.keys() for point in points)

def test_create_session_rejects_incomplete_or_duplicate_source(tmp_path):
    with pytest.raises(CorrectionError) as exc_info:
        create_correction_session(
            tmp_path, asset_id="scan", run_id="missing",
            session_id="session-001", sample_id="sample-001", actor="alice",
        )
    assert exc_info.value.code == "segmentation_run_not_found"
```

- [ ] **Step 3: Run tests and verify the missing module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_sessions.py -v --basetemp .pytest-tmp-p14-task1-red`

Expected: FAIL during collection with `ModuleNotFoundError: pc_system.segmentation_corrections`.

- [ ] **Step 4: Implement session paths, validation, source loading, and baseline creation**

```python
class CorrectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

def create_correction_session(
    project_root: Path,
    *,
    asset_id: str,
    run_id: str,
    session_id: str,
    sample_id: str,
    actor: str,
    benchmark_id: str | None = None,
    lock_ttl_seconds: int = 900,
    baseline_release_id: str | None = None,
) -> dict:
    asset_id = validate_identifier(asset_id, "asset_id")
    run_id = validate_identifier(run_id, "run_id")
    session_id = validate_identifier(session_id, "session_id")
    run_dir = project_root / "reports" / "segmentation_runs" / asset_id / run_id
    run = _read_json(run_dir / "segmentation_run.json", "segmentation_run_not_found")
    if run.get("status") != "completed":
        raise CorrectionError("segmentation_run_not_completed", "Only completed runs can be corrected.")
    points = sample_points_from_source(
        _resolve_source(project_root, run["source_uri"]),
        max_points=int(run["config"].get("max_points", run["source_point_count"])),
    )
    baseline = _build_automatic_assignments(run_dir, run, points)
    # Write correction_session.json, baseline_labels.json, empty events.jsonl,
    # draft_labels.json, and draft_objects.json only after all validation succeeds.
```

The automatic baseline must assign every sampled point exactly once. Membership indices outside the loaded point range, duplicate memberships, missing membership artifacts, source fingerprint mismatch, existing session directory, empty actor, or non-positive lock TTL must raise stable `CorrectionError.code` values and must not leave a partial session directory.

- [ ] **Step 5: Add optional existing-label overlay and pagination tests**

```python
def test_existing_labels_overlay_only_matching_source_indices(tmp_path):
    write_completed_run(tmp_path)
    write_development_benchmark(tmp_path, labeled_indices=[0])
    create_correction_session(
        tmp_path, asset_id="scan", run_id="run-001", session_id="session-001",
        sample_id="sample-001", actor="alice", benchmark_id="bench-dev",
    )
    points = load_correction_points(tmp_path, "scan", "session-001", offset=0, limit=2)
    assert points[0]["draft"]["class_id"] == "pipe"
    assert points[1]["draft"] == points[1]["baseline"]
```

Implement overlay through `load_benchmark_sample` and `match_point_labels`; retain automatic assignments for unlabeled points. Return `{"offset", "limit", "total", "points"}` from the paginated public loader, cap `limit` at 50,000, and reject negative offsets or non-positive limits.

- [ ] **Step 6: Run focused tests and existing Phase 13 tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_sessions.py tests/test_phase13_segmentation_service.py tests/test_phase13b_benchmarks.py -v --basetemp .pytest-tmp-p14-task1-green`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/pc_system/segmentation_corrections.py tests/phase14_helpers.py tests/test_phase14_correction_sessions.py
git commit -m "feat: create Phase 14 correction sessions"
```

---

### Task 2: Deterministic Correction Events, Undo, and Restore

**Files:**
- Create: `src/pc_system/segmentation_correction_events.py`
- Modify: `src/pc_system/segmentation_corrections.py`
- Create: `tests/test_phase14_correction_events.py`

**Interfaces:**
- Consumes: `load_correction_session`, the immutable `baseline_labels.json`, and JSONL events.
- Produces: `apply_correction_event(project_root: Path, *, asset_id: str, session_id: str, actor: str, expected_revision: int, client_request_id: str, operation: dict) -> dict`, `materialize_correction(baseline: dict, events: list[dict]) -> dict`, and `read_correction_events(project_root: Path, asset_id: str, session_id: str) -> list[dict]`.

- [ ] **Step 1: Write failing tests for merge, split, relabel, noise, and confirmation**

```python
@pytest.mark.parametrize("operation", [
    {"type": "confirm", "instance_ids": ["obj-001"]},
    {"type": "merge", "instance_ids": ["obj-001", "obj-002"], "target_instance_id": "obj-001"},
    {"type": "split", "instance_id": "obj-001", "source_point_indices": [1]},
    {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"},
    {"type": "mark_noise", "source_point_indices": [2]},
    {"type": "restore_from_noise", "source_point_indices": [2], "target_instance_id": "obj-001"},
]):
    def test_operation_replays_to_same_fingerprint(tmp_path, operation):
        session = correction_session(tmp_path)
        updated = apply_correction_event(
            tmp_path, asset_id="scan", session_id=session["session_id"],
            actor="alice", expected_revision=session["revision"],
            client_request_id=f"request-{operation['type']}", operation=operation,
        )
        replayed = materialize_correction(
            load_correction_baseline(tmp_path, "scan", session["session_id"]),
            read_correction_events(tmp_path, "scan", session["session_id"]),
        )
        assert replayed["fingerprint"] == updated["draft_fingerprint"]
```

- [ ] **Step 2: Write failing tests for revision, lock, idempotency, and rejected writes**

```python
def test_stale_revision_does_not_append_event(tmp_path):
    session = correction_session(tmp_path)
    apply_test_event(tmp_path, session, expected_revision=0, client_request_id="req-1")
    with pytest.raises(CorrectionError) as exc_info:
        apply_test_event(tmp_path, session, expected_revision=0, client_request_id="req-2")
    assert exc_info.value.code == "stale_revision"
    assert len(read_correction_events(tmp_path, "scan", session["session_id"])) == 1

def test_repeated_client_request_is_idempotent(tmp_path):
    session = correction_session(tmp_path)
    first = apply_test_event(tmp_path, session, expected_revision=0, client_request_id="req-1")
    second = apply_test_event(tmp_path, session, expected_revision=0, client_request_id="req-1")
    assert second == first
```

- [ ] **Step 3: Run tests and verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_events.py -v --basetemp .pytest-tmp-p14-task2-red`

Expected: FAIL because the event module and functions do not exist.

- [ ] **Step 4: Implement strict operation schemas and deterministic event replay**

```python
SUPPORTED_OPERATIONS = {
    "confirm", "merge", "split", "relabel", "mark_noise",
    "restore_from_noise", "undo", "redo", "restore",
}

def materialize_correction(baseline: dict, events: list[dict]) -> dict:
    assignments = {int(item["source_point_index"]): dict(item) for item in baseline["assignments"]}
    active_events: list[dict] = []
    redo_events: list[dict] = []
    for event in events:
        operation = event["operation"]
        if operation["type"] == "undo":
            if active_events:
                redo_events.append(active_events.pop())
        elif operation["type"] == "redo":
            if redo_events:
                active_events.append(redo_events.pop())
        else:
            active_events.append(event)
            redo_events.clear()
        assignments = _replay_active_events(baseline, active_events)
    return _build_materialized_document(assignments, active_events, redo_events)
```

Validate exact integer indices without boolean or float coercion. Merge requires at least two distinct active non-noise objects. Split requires a non-empty proper subset and generates a deterministic ID from the session/event sequence. Relabel requires a validated class ID. Noise restore requires an active target object. Confirm changes review state only. Restore accepts `scope="all"`, exact indices, or instance IDs and copies those assignments from the immutable baseline.

- [ ] **Step 5: Implement atomic write ordering**

Under an exclusive session lock file:

1. Load and revalidate the session status, editor lock, expiration, and `expected_revision`.
2. Return the stored response for an already accepted `client_request_id`.
3. Validate and simulate the new event without touching disk.
4. Append exactly one newline-delimited event record and flush it.
5. Atomically rewrite draft labels, objects, and session metadata.
6. Increment revision exactly once and renew `lock_expires_at`.

If materialization or persistence fails before the append, no artifact changes. If a process stops after the append, the next load must rematerialize draft artifacts from baseline plus the event log.

- [ ] **Step 6: Add undo, redo, full restore, and historical-release restore tests**

```python
def test_undo_redo_and_restore_are_append_only(tmp_path):
    session = correction_session(tmp_path)
    changed = apply_relabel(tmp_path, session, "pipe")
    undone = apply_operation(tmp_path, changed, {"type": "undo"})
    redone = apply_operation(tmp_path, undone, {"type": "redo"})
    restored = apply_operation(tmp_path, redone, {"type": "restore", "scope": "all"})
    assert undone["draft_fingerprint"] == session["draft_fingerprint"]
    assert redone["draft_fingerprint"] == changed["draft_fingerprint"]
    assert restored["draft_fingerprint"] == session["draft_fingerprint"]
    assert len(read_correction_events(tmp_path, "scan", session["session_id"])) == 4
```

- [ ] **Step 7: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_sessions.py tests/test_phase14_correction_events.py -v --basetemp .pytest-tmp-p14-task2-green`

Expected: all selected tests PASS.

- [ ] **Step 8: Commit**

```powershell
git add src/pc_system/segmentation_corrections.py src/pc_system/segmentation_correction_events.py tests/test_phase14_correction_events.py
git commit -m "feat: replay Phase 14 correction events"
```

---

### Task 3: Review Queue, Suggestions, and Correction Diff

**Files:**
- Create: `src/pc_system/segmentation_review_queue.py`
- Modify: `src/pc_system/segmentation_corrections.py`
- Create: `tests/test_phase14_review_queue.py`

**Interfaces:**
- Consumes: baseline/draft assignments and objects, optional Phase 13A quality flags, optional Phase 13B evaluation artifacts.
- Produces: `build_review_queue(*, session: dict, baseline: dict, draft: dict, quality: dict | None = None, evaluation: dict | None = None) -> dict` and `build_correction_diff(baseline: dict, draft: dict) -> dict`.

- [ ] **Step 1: Write failing deterministic queue and diff tests**

```python
def test_queue_prioritizes_evaluation_errors_before_proxy_flags():
    queue = build_review_queue(
        session={"session_id": "session-001"},
        baseline=baseline(),
        draft=draft(),
        quality={"flags": [{"object_id": "obj-2", "code": "small_fragment"}]},
        evaluation={"instance_errors": [{"instance_id": "obj-1", "kind": "under_segmented"}]},
    )
    assert queue["items"][0]["instance_id"] == "obj-1"
    assert queue["items"][0]["suggested_action"] == "split"

def test_diff_counts_changed_points_objects_and_classes():
    diff = build_correction_diff(baseline(), relabeled_and_split_draft())
    assert diff["changed_point_count"] == 2
    assert diff["created_instance_count"] == 1
    assert diff["class_change_count"] == 1
```

- [ ] **Step 2: Run tests and verify missing module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_review_queue.py -v --basetemp .pytest-tmp-p14-task3-red`

Expected: FAIL during collection.

- [ ] **Step 3: Implement stable scoring and suggestion provenance**

```python
SEVERITY_WEIGHT = {"critical": 400, "high": 300, "medium": 200, "low": 100}
SOURCE_WEIGHT = {"golden_evaluation": 30, "operational_quality": 20, "heuristic": 10}

def _priority(item: dict) -> int:
    return SEVERITY_WEIGHT[item["severity"]] + SOURCE_WEIGHT[item["source"]]
```

Queue items contain `item_id`, `instance_id`, `source`, `reason_code`, `severity`, `priority`, `suggested_action`, `confirmed`, and evidence. Sort by descending priority and then stable `item_id`. Suggestions never mutate labels. A `confirm` event marks matching items confirmed.

Diff output contains counts for changed, noise-added, noise-restored, created, removed, merged, split, relabeled, and confirmed entities plus bounded affected IDs. It must not include unrestricted filesystem paths.

- [ ] **Step 4: Materialize queue and diff after every accepted event**

Update session creation and event application to atomically write:

```python
write_json(queue, session_dir / "review_queue.json")
write_json(diff, session_dir / "correction_diff.json")
session["artifacts"].update({
    "review_queue": "review_queue.json",
    "correction_diff": "correction_diff.json",
})
```

- [ ] **Step 5: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_review_queue.py tests/test_phase14_correction_events.py -v --basetemp .pytest-tmp-p14-task3-green`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/segmentation_review_queue.py src/pc_system/segmentation_corrections.py tests/test_phase14_review_queue.py
git commit -m "feat: prioritize Phase 14 correction review"
```

---

### Task 4: Review Lifecycle and Immutable Publication

**Files:**
- Create: `src/pc_system/segmentation_correction_releases.py`
- Modify: `src/pc_system/segmentation_corrections.py`
- Create: `tests/test_phase14_correction_releases.py`

**Interfaces:**
- Consumes: a correction session at an exact revision, Phase 13A provenance, optional source benchmark metadata.
- Produces: `transition_correction_session(project_root: Path, *, asset_id: str, session_id: str, action: str, actor: str, expected_revision: int) -> dict`, `publish_correction_release(project_root: Path, *, asset_id: str, session_id: str, release_id: str, reviewer: str, expected_revision: int, benchmark_split: str, license_name: str, evaluation_config: dict | None = None, baseline_evaluation_id: str | None = None, regression_thresholds: dict | None = None, search_config: dict | None = None) -> dict`, `retry_publication_tasks(project_root: Path, *, asset_id: str, release_id: str, actor: str) -> dict`, `list_correction_releases(project_root: Path, asset_id: str) -> list[dict]`, and `load_correction_release(project_root: Path, asset_id: str, release_id: str) -> dict`.

- [ ] **Step 1: Write failing lifecycle and immutable-release tests**

```python
def test_publish_freezes_reviewed_revision_and_refuses_overwrite(tmp_path):
    session = corrected_session(tmp_path)
    reviewed = transition_correction_session(
        tmp_path, asset_id="scan", session_id=session["session_id"],
        action="submit", actor="alice", expected_revision=session["revision"],
    )
    release = publish_correction_release(
        tmp_path, asset_id="scan", session_id=session["session_id"],
        release_id="release-001", reviewer="bob",
        expected_revision=reviewed["revision"], benchmark_split="development",
        license_name="internal",
    )
    assert release["status"] == "published"
    assert release["source_revision"] == reviewed["revision"]
    with pytest.raises(CorrectionError) as exc_info:
        publish_correction_release(
            tmp_path, asset_id="scan", session_id=session["session_id"],
            release_id="release-001", reviewer="bob",
            expected_revision=reviewed["revision"],
            benchmark_split="development", license_name="internal",
        )
    assert exc_info.value.code == "release_exists"
```

- [ ] **Step 2: Write failing policy tests**

```python
@pytest.mark.parametrize(("split", "expected"), [
    ("development", "eligible"),
    ("validation", "evaluation_only"),
    ("golden_regression", "evaluation_only"),
])
def test_training_policy_is_explicit(tmp_path, split, expected):
    release = publish_reviewed(tmp_path, benchmark_split=split)
    policy = read_release_artifact(tmp_path, release, "training_policy")
    assert policy["eligibility"] == expected

def test_golden_regression_rejects_parameter_search(tmp_path):
    with pytest.raises(CorrectionError) as exc_info:
        publish_reviewed(tmp_path, benchmark_split="golden_regression", search_config={"max_trials": 2})
    assert exc_info.value.code == "golden_search_forbidden"
```

- [ ] **Step 3: Run tests and verify missing publication module failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_releases.py -v --basetemp .pytest-tmp-p14-task4-red`

Expected: FAIL during collection.

- [ ] **Step 4: Implement lifecycle transitions and release preflight**

Allowed transitions:

```python
TRANSITIONS = {
    ("draft", "submit"): "in_review",
    ("in_review", "return"): "draft",
    ("in_review", "publish"): "published",
    ("draft", "abandon"): "abandoned",
    ("in_review", "abandon"): "abandoned",
}
```

The publish preflight validates the exact revision, state, reviewer policy, non-empty draft, full assignment coverage, valid boxes, source fingerprint, benchmark split, license, unique release/benchmark IDs, and the golden-search rule before creating any release path.

- [ ] **Step 5: Implement immutable artifacts and derived benchmark**

Write:

```text
reports/segmentation_correction_releases/<asset>/<release>/
  correction_release.json
  labels.json
  objects.json
  correction_diff.json
  provenance.json
  publication_tasks.json
  training_policy.json
datasets/segmentation_feedback/<release>/
  feedback_manifest.json
  before_labels.json
  after_labels.json
  operations.jsonl
benchmarks/<derived-benchmark-id>/
  benchmark.json
  samples/<sample-id>/labels.json
```

The derived benchmark ID is `<release_id>-benchmark`, validated before use. Convert `source_point_index` to Phase 13B `point_index`, preserve coordinates, and recompute axis-aligned boxes with identity quaternion. Create directories through staging siblings and atomically rename only after all required artifacts are valid.

- [ ] **Step 6: Implement downstream task isolation and retry**

`publication_tasks.json` records evaluation, regression, and search as `not_requested`, `planned`, `running`, `completed`, or `failed`. Required release artifacts are committed before optional downstream work starts. Downstream failure updates only task state and never rolls back or overwrites the release.

Use `evaluate_segmentation_run` against the derived benchmark only when requested. Use `compare_evaluations` only when a baseline evaluation and thresholds are supplied. Search is allowed only for `development` or `validation`; its runner must use the derived labels but must never use `golden_regression`.

- [ ] **Step 7: Add restoration-from-release test**

```python
def test_restore_release_creates_new_draft_without_mutating_release(tmp_path):
    release = publish_reviewed(tmp_path)
    release_path = (
        tmp_path / "reports" / "segmentation_correction_releases"
        / "scan" / release["release_id"] / "correction_release.json"
    )
    original_release_bytes = release_path.read_bytes()
    restored = create_correction_session(
        tmp_path, asset_id="scan", run_id="run-001", session_id="session-restore",
        sample_id="sample-001", actor="alice", baseline_release_id=release["release_id"],
    )
    assert restored["baseline"]["kind"] == "correction_release"
    assert restored["supersedes_release_id"] == release["release_id"]
    assert release_path.read_bytes() == original_release_bytes
```

Extend `create_correction_session` with `baseline_release_id: str | None = None`; reject using benchmark and release baselines together.

- [ ] **Step 8: Run focused and Phase 13B integration tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_releases.py tests/test_phase13b_benchmarks.py tests/test_phase13b_evaluation.py tests/test_phase13b_search.py -v --basetemp .pytest-tmp-p14-task4-green`

Expected: all selected tests PASS.

- [ ] **Step 9: Commit**

```powershell
git add src/pc_system/segmentation_corrections.py src/pc_system/segmentation_correction_releases.py tests/test_phase14_correction_releases.py
git commit -m "feat: publish immutable correction releases"
```

---

### Task 5: Protected Phase 14 API

**Files:**
- Modify: `src/pc_system/api.py`
- Create: `tests/test_phase14_correction_api.py`

**Interfaces:**
- Consumes: public functions from Tasks 1–4 and existing `require_write_key`.
- Produces: the Phase 14 HTTP routes from the approved design, with stable status mapping.

- [ ] **Step 1: Write failing API happy-path test**

```python
def test_api_correction_flow(tmp_path):
    write_completed_run(tmp_path)
    client = TestClient(create_app(tmp_path, api_key="secret"))
    created = client.post(
        "/segmentation-corrections/scan",
        headers={"X-API-Key": "secret"},
        json={"run_id": "run-001", "session_id": "session-001", "sample_id": "sample-001", "actor": "alice"},
    )
    assert created.status_code == 201
    event = client.post(
        "/segmentation-corrections/scan/session-001/events",
        headers={"X-API-Key": "secret"},
        json={"actor": "alice", "expected_revision": 0, "client_request_id": "req-1",
              "operation": {"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"}},
    )
    assert event.status_code == 200
    assert client.get("/segmentation-corrections/scan/session-001/points?offset=0&limit=10").json()["total"] == 3
```

- [ ] **Step 2: Write failing security and conflict mapping tests**

```python
def test_all_correction_writes_require_key(tmp_path):
    client = TestClient(create_app(tmp_path, api_key="secret"))
    assert client.post("/segmentation-corrections/scan", json={}).status_code == 401

def test_domain_conflicts_have_stable_http_status(tmp_path):
    # stale_revision -> 409, session_locked -> 423, missing -> 404,
    # validation and invalid identifiers -> 400, immutable -> 409.
```

- [ ] **Step 3: Run tests and verify route failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_api.py -v --basetemp .pytest-tmp-p14-task5-red`

Expected: FAIL with route 404 responses.

- [ ] **Step 4: Add route helpers and read routes**

Add a single `_correction_http_error(exc: CorrectionError) -> HTTPException` mapper. Add:

```text
GET  /segmentation-corrections/<asset_id>
GET  /segmentation-corrections/<asset_id>/<session_id>
GET  /segmentation-corrections/<asset_id>/<session_id>/points
GET  /segmentation-corrections/<asset_id>/<session_id>/objects
GET  /segmentation-corrections/<asset_id>/<session_id>/queue
GET  /segmentation-corrections/<asset_id>/<session_id>/events
GET  /segmentation-correction-releases/<asset_id>
GET  /segmentation-correction-releases/<asset_id>/<release_id>
```

Read routes validate every path identifier and return JSON values, never server filesystem paths.

- [ ] **Step 5: Add protected write routes**

Add:

```text
POST /segmentation-corrections/<asset_id>
POST /segmentation-corrections/<asset_id>/<session_id>/events
POST /segmentation-corrections/<asset_id>/<session_id>/submit
POST /segmentation-corrections/<asset_id>/<session_id>/return
POST /segmentation-corrections/<asset_id>/<session_id>/publish
POST /segmentation-correction-releases/<asset_id>/<release_id>/retry
```

Every route accepts `X-API-Key`, invokes `require_write_key` before accessing mutation payload fields, and uses HTTP 201 only for new sessions/releases.

- [ ] **Step 6: Run API tests plus existing API security tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_api.py tests/test_api.py tests/test_api_phase4.py tests/test_phase13b_cli_api.py -v --basetemp .pytest-tmp-p14-task5-green`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/pc_system/api.py tests/test_phase14_correction_api.py
git commit -m "feat: expose protected correction APIs"
```

---

### Task 6: Phase 14 CLI

**Files:**
- Create: `src/pc_system/commands/phase14.py`
- Modify: `src/pc_system/cli_parser.py`
- Modify: `src/pc_system/cli.py`
- Create: `tests/test_phase14_correction_cli.py`

**Interfaces:**
- Consumes: Tasks 1–4 domain functions.
- Produces: `create-segmentation-correction`, `apply-segmentation-correction`, `submit-segmentation-correction`, `publish-segmentation-correction`, and `retry-segmentation-publication`.

- [ ] **Step 1: Write failing parser and flow tests**

```python
def test_cli_create_apply_submit_publish(tmp_path):
    write_completed_run(tmp_path)
    assert main(["create-segmentation-correction", "--project-root", str(tmp_path),
                 "--asset-id", "scan", "--run-id", "run-001",
                 "--session-id", "session-001", "--sample-id", "sample-001",
                 "--actor", "alice"]) == 0
    operation = tmp_path / "operation.json"
    operation.write_text(json.dumps({"type": "relabel", "instance_ids": ["obj-001"], "class_id": "pipe"}), encoding="utf-8")
    assert main(["apply-segmentation-correction", "--project-root", str(tmp_path),
                 "--asset-id", "scan", "--session-id", "session-001",
                 "--actor", "alice", "--expected-revision", "0",
                 "--client-request-id", "req-1", "--operation", str(operation)]) == 0
```

- [ ] **Step 2: Run tests and verify parser rejection**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_cli.py -v --basetemp .pytest-tmp-p14-task6-red`

Expected: FAIL with invalid choice for the new commands.

- [ ] **Step 3: Implement parser definitions with exact required arguments**

Use JSON configuration files for event operation and publication options; do not add dense operation-specific flag combinations. Add all Phase 14 identifiers to the existing pre-dispatch validation tuple.

```python
apply_correction.add_argument("--operation", required=True, type=Path)
apply_correction.add_argument("--expected-revision", required=True, type=int)
publish_correction.add_argument("--publication", required=True, type=Path)
```

- [ ] **Step 4: Implement thin command adapters and dispatch**

Each command reads an object using the established `_load_json_object` pattern, invokes one domain function, prints the primary manifest path, and returns `0`. Domain validation remains in domain modules; existing centralized CLI error handling returns `2` for invalid inputs and `1` for missing artifacts.

- [ ] **Step 5: Run CLI tests and existing CLI regression**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_cli.py tests/test_cli_error_handling.py tests/test_phase13_cli_api.py tests/test_phase13b_cli_api.py -v --basetemp .pytest-tmp-p14-task6-green`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/pc_system/commands/phase14.py src/pc_system/cli_parser.py src/pc_system/cli.py tests/test_phase14_correction_cli.py
git commit -m "feat: add Phase 14 correction CLI"
```

---

### Task 7: Standalone Simple Correction Workbench

**Files:**
- Create: `frontend/correction.html`
- Create: `frontend/correction.css`
- Create: `frontend/segmentation-correction.js`
- Create: `frontend/correction.js`
- Modify: `frontend/index.html`
- Create: `tests/test_phase14_correction_frontend.py`

**Interfaces:**
- Consumes: Task 5 API routes and exact point records.
- Produces: `buildCorrectionViewModel(session, queue, objects)`, `projectPoint(point, camera, viewport)`, `pickIndices(points, polygon)`, and `buildOperation(action, selection, context)` as pure functions usable in browser and Node.js.

- [ ] **Step 1: Write failing static structure and copy tests**

```python
def test_correction_workbench_has_simple_primary_controls():
    html = (ROOT / "frontend" / "correction.html").read_text(encoding="utf-8")
    for marker in (
        'id="correction-canvas"', 'id="review-queue"', 'id="object-panel"',
        'data-action="confirm"', 'data-action="merge"', 'data-action="split"',
        'data-action="relabel"', 'data-action="noise"', 'data-action="undo"',
        'data-action="redo"', 'data-action="restore"',
    ):
        assert marker in html
    assert "系统建议" in html
    assert "人工已确认" in html
```

- [ ] **Step 2: Write failing Node behavior tests**

```python
def test_projection_selection_and_context_operations_in_node():
    result = run_node("""
      const m = require(MODULE);
      const projected = m.projectPoint({source_point_index: 7, x: 1, y: 2, z: 3},
                                       {view: 'top', zoom: 1, panX: 0, panY: 0},
                                       {width: 100, height: 100});
      const operation = m.buildOperation('split', [7], {instanceId: 'obj-1'});
      console.log(JSON.stringify({projected, operation}));
    """)
    assert result["projected"]["source_point_index"] == 7
    assert result["operation"]["source_point_indices"] == [7]
```

- [ ] **Step 3: Run tests and verify missing files**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_frontend.py -v --basetemp .pytest-tmp-p14-task7-red`

Expected: FAIL because the workbench files do not exist.

- [ ] **Step 4: Implement accessible workbench layout**

Create:

- Left: prioritized review queue with reason, severity, suggestion, and confirmation state.
- Center: Canvas viewer, top/front/side buttons, rotate/pan/zoom, fit view, box/lasso/brush selection, baseline/draft toggle, loading and empty states.
- Right: selected objects, class selector, contextual primary action, advanced tools disclosure, and diff summary.
- Bottom: undo, redo, restore baseline, submit/review/publish lifecycle state and conflict banner.

Primary merge and split must be achievable in at most two explicit actions after selection. Hide advanced tools behind `<details>`.

- [ ] **Step 5: Implement pure view-model and geometry module**

```javascript
(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.segmentationCorrection = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  function buildOperation(action, sourcePointIndices, context) {
    const indices = [...new Set(sourcePointIndices)].sort((a, b) => a - b);
    if (action === "split") {
      return { type: "split", instance_id: context.instanceId, source_point_indices: indices };
    }
    // Return explicit payloads for merge, relabel, noise, restore, confirm, undo, redo.
  }
  return { buildCorrectionViewModel, projectPoint, pickIndices, buildOperation };
});
```

Projection and selection must preserve exact source indices; rendering may decimate visually, but an operation may include only points explicitly returned by the API and selected by deterministic hit testing.

- [ ] **Step 6: Implement API orchestration and conflict recovery**

`correction.js` reads `asset_id`, `session_id`, and optional API base from query parameters, loads session/points/objects/queue in parallel, renews the displayed lock on accepted writes, sends `expected_revision` and a generated `client_request_id`, and handles:

- 409 by reloading and showing the stale-write banner;
- 423 by switching to read-only mode;
- 401 by requesting an API key in the local settings panel;
- validation errors without clearing the selection;
- publication task failures without claiming the release failed.

- [ ] **Step 7: Run frontend and API integration tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_frontend.py tests/test_phase14_correction_api.py tests/test_frontend_dashboard.py -v --basetemp .pytest-tmp-p14-task7-green`

Expected: all selected tests PASS.

- [ ] **Step 8: Commit**

```powershell
git add frontend/correction.html frontend/correction.css frontend/segmentation-correction.js frontend/correction.js frontend/index.html tests/test_phase14_correction_frontend.py
git commit -m "feat: add segmentation correction workbench"
```

---

### Task 8: End-to-End Recovery, Publication, and Security

**Files:**
- Create: `tests/test_phase14_correction_e2e.py`
- Modify: Phase 14 modules only where the new tests expose defects.

**Interfaces:**
- Consumes: full CLI, API, persistence, publication, and frontend contracts.
- Produces: verified cross-layer Phase 14 workflow.

- [ ] **Step 1: Write end-to-end correction and release test**

```python
def test_end_to_end_correct_review_publish_and_evaluate(tmp_path):
    write_completed_run(tmp_path)
    session = create_session_via_api(tmp_path)
    session = apply_merge_via_api(tmp_path, session)
    session = apply_split_via_api(tmp_path, session)
    session = apply_relabel_via_api(tmp_path, session)
    reviewed = submit_via_api(tmp_path, session)
    release = publish_via_api(tmp_path, reviewed, split="development")
    assert release["status"] == "published"
    assert derived_labels_cover_source_once(tmp_path, release)
    assert feedback_before_after_are_versioned(tmp_path, release)
    assert training_policy(tmp_path, release)["eligibility"] == "eligible"
```

- [ ] **Step 2: Write crash recovery and no-partial-publish tests**

Inject a writer failure after event append and assert session reload rematerializes the exact revision. Inject a required release-artifact failure before staging rename and assert no final release, benchmark, or feedback directory exists. Inject downstream evaluation failure and assert the published release remains immutable with a failed retryable task.

- [ ] **Step 3: Write security matrix test**

Parametrize every Phase 14 write route and assert missing/wrong keys return 401 before state changes. Parametrize malicious identifiers such as `../escape`, `bad$id`, `.`, and `..` for all route families and assert 400 with no paths created outside the intended roots.

- [ ] **Step 4: Run end-to-end tests and fix only demonstrated defects**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_e2e.py -v --basetemp .pytest-tmp-p14-task8-red`

Expected before fixes: one or more targeted failures that identify cross-layer gaps.

Apply the smallest fixes in the owning Phase 14 module, then rerun:

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_e2e.py tests/test_phase14_correction_api.py tests/test_phase14_correction_releases.py -v --basetemp .pytest-tmp-p14-task8-green`

Expected: all selected tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/pc_system tests/test_phase14_correction_e2e.py
git commit -m "test: verify Phase 14 correction lifecycle"
```

---

### Task 9: Operator Documentation and Full Regression

**Files:**
- Create: `docs/phase14-segmentation-correction-loop.md`
- Modify: `README.md`
- Modify: `docs/system-function-module-inventory.md`
- Create: `tests/test_phase14_correction_docs.py`

**Interfaces:**
- Consumes: final CLI, API, artifact, and UI behavior.
- Produces: discoverable and auditable Phase 14 operating documentation.

- [ ] **Step 1: Write failing documentation contract test**

```python
def test_phase14_docs_cover_operation_recovery_and_training_boundaries():
    document = (ROOT / "docs" / "phase14-segmentation-correction-loop.md").read_text(encoding="utf-8")
    for term in (
        "correction.html", "source_point_index", "expected_revision",
        "confirm", "merge", "split", "relabel", "mark_noise",
        "undo", "redo", "restore", "published", "immutable",
        "segmentation_feedback", "golden_regression", "evaluation-only",
        "Champion/Challenger",
    ):
        assert term in document
```

- [ ] **Step 2: Run test and verify missing document failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_docs.py -v --basetemp .pytest-tmp-p14-task9-red`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write operator and integration documentation**

Document:

- prerequisites and creation from Phase 13A;
- optional Phase 13B label overlay;
- simple confirm/merge/split/relabel/noise workflow;
- selection tools and exact index guarantee;
- lock/revision conflict recovery;
- undo/redo, baseline restoration, and release restoration;
- review and publication policy;
- artifact tree and immutable lineage;
- API and CLI examples with safe placeholder credentials;
- derived evaluation/regression/search behavior;
- training eligibility and the absolute golden-regression prohibition;
- future self-training with Champion/Challenger promotion outside Phase 14.

Update README with a Phase 14 quick-start and workbench link. Mark Phase 14 modules as complete in the inventory only after implementation tests pass.

- [ ] **Step 4: Run documentation test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_phase14_correction_docs.py -v --basetemp .pytest-tmp-p14-task9-green`

Expected: PASS.

- [ ] **Step 5: Run formatting and placeholder checks**

Run: `git diff --check`

Expected: no output.

Run: `rg -n "T[B]D|T[O]DO|implement[ ]later|fill[ ]in[ ]details" docs/phase14-segmentation-correction-loop.md src/pc_system frontend tests/test_phase14*`

Expected: no Phase 14 placeholder matches.

- [ ] **Step 6: Run the complete test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-p14-full`

Expected: all tests PASS with no errors; the known Starlette deprecation warning may remain.

- [ ] **Step 7: Run targeted syntax and frontend checks**

Run: `.\.venv\Scripts\python.exe -m compileall -q src`

Expected: exit code 0.

Run: `node --check frontend/segmentation-correction.js`

Expected: exit code 0.

Run: `node --check frontend/correction.js`

Expected: exit code 0.

- [ ] **Step 8: Verify repository scope**

Run: `git status --short`

Expected: only intended Phase 14 source, tests, frontend, and documentation changes are present.

Run: `git diff --stat HEAD~9..HEAD`

Expected: the change set is limited to the files named in this plan.

- [ ] **Step 9: Commit**

```powershell
git add docs/phase14-segmentation-correction-loop.md docs/system-function-module-inventory.md README.md tests/test_phase14_correction_docs.py
git commit -m "docs: complete Phase 14 correction workflow"
```

---

## Final Review Checklist

- [ ] Every source point in a session has exactly one baseline and draft assignment.
- [ ] Every correction operation uses exact validated integer source indices.
- [ ] Event replay from immutable baseline produces the persisted draft fingerprint.
- [ ] Undo, redo, and restore append events rather than deleting history.
- [ ] Stale revisions and foreign locks cannot mutate artifacts.
- [ ] Common merge and split actions remain one- or two-step operations.
- [ ] Publication freezes one reviewed revision and never overwrites a release.
- [ ] Derived benchmark labels pass existing Phase 13B validation and evaluation.
- [ ] Downstream failures are retryable and do not invalidate a published release.
- [ ] Feedback exports include before/after labels, operations, provenance, and eligibility.
- [ ] Golden-regression data is absent from training and parameter-search inputs.
- [ ] All Phase 14 write routes are API-key protected.
- [ ] No unrestricted local filesystem paths are returned through the API.
- [ ] Full tests, compile checks, Node syntax checks, and `git diff --check` pass.
