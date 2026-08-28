import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import pc_system.model_sampling as sampling_module
from pc_system.model_import import fingerprint_file, import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import (
    load_operation,
    read_verified_operation_snapshot,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal
from pc_system.model_sampling import (
    list_sampled_representations,
    load_sampled_representation,
    sample_model_version,
)


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def _reader(_path):
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def _prepare(project_root):
    create_model_asset(
        project_root, model_id="pump-a", display_name="Pump A",
        category_id="pump", manufacturer="", model_number="",
        keywords=[], tags=[], principal=EXPERT,
        operation_id="op-asset-sample", request_id="req-asset-sample",
        idempotency_key="idem-asset-sample",
    )
    import_model_version(
        project_root, model_id="pump-a", version_id="v1",
        source_path=FIXTURE, declared_unit="mm", license_name="internal",
        provenance={}, principal=EXPERT, operation_id="op-import-sample",
        request_id="req-import-sample", idempotency_key="idem-import-sample",
        mesh_reader=_reader,
    )


def _version_bytes(root):
    version = root / "models" / "pump-a" / "versions" / "v1"
    return {path.relative_to(version).as_posix(): path.read_bytes()
            for path in version.rglob("*") if path.is_file()}


def _sample(root, *, operation_id="op-sample-001", request_id="req-sample-001",
            idempotency_key="idem-sample-001"):
    return sample_model_version(
        root, model_id="pump-a", version_id="v1", point_count=10,
        random_seed=7, principal=EXPERT, operation_id=operation_id,
        request_id=request_id, idempotency_key=idempotency_key,
        mesh_reader=_reader,
    )


def _representation_dir(root, representation):
    return (root / "models" / "pump-a" / "representations" / "v1"
            / "cad_sampled" / representation["representation_id"])


def test_sample_model_version_publishes_outside_immutable_version(tmp_path):
    _prepare(tmp_path)
    before = _version_bytes(tmp_path)
    representation = _sample(tmp_path)
    assert representation["representation_type"] == "cad_sampled"
    assert representation["point_count"] == 10
    assert _version_bytes(tmp_path) == before
    assert load_sampled_representation(
        tmp_path, "pump-a", "v1", representation["representation_id"]
    ) == representation
    assert list_sampled_representations(tmp_path, "pump-a", "v1") == [representation]


def test_same_sampling_request_replays_without_duplicate(tmp_path):
    _prepare(tmp_path)
    assert _sample(tmp_path) == _sample(tmp_path)
    assert len(list_sampled_representations(tmp_path, "pump-a", "v1")) == 1


def test_same_config_new_operation_reuses_completed_representation(tmp_path):
    _prepare(tmp_path)
    first = _sample(tmp_path)
    second = _sample(
        tmp_path, operation_id="op-sample-002", request_id="req-sample-002",
        idempotency_key="idem-sample-002",
    )
    assert second == first
    assert load_operation(tmp_path, "op-sample-002")["status"] == "completed"
    snapshot = read_verified_operation_snapshot(tmp_path, "op-sample-002")
    assert [event["event_type"] for event in snapshot["events"]] == [
        "operation.started",
        "model_sampling.representation_reused",
        "operation.completed",
    ]
    assert len(list_sampled_representations(tmp_path, "pump-a", "v1")) == 1


def test_reuse_operation_cannot_be_rebound_as_original_producer(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    _sample(
        tmp_path, operation_id="op-sample-002", request_id="req-sample-002",
        idempotency_key="idem-sample-002",
    )
    reused = read_verified_operation_snapshot(tmp_path, "op-sample-002")
    directory = _representation_dir(tmp_path, representation)
    owner_path = directory / "operation_owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["operation_id"] = "op-sample-002"
    owner["request_id"] = reused["operation"]["request_id"]
    owner["request_fingerprint"] = reused["operation"]["request_fingerprint"]
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    manifest_path = directory / "representation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["operation_id"] = "op-sample-002"
    manifest["generated_by"] = reused["events"][0]["actor_id"]
    manifest["generated_at"] = reused["events"][0]["timestamp"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_sampled_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"]
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_tampered_sampled_points_fail_closed(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    path = (tmp_path / "models" / "pump-a" / "representations" / "v1"
            / "cad_sampled" / representation["representation_id"] / "sampled_points.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["points"][0][0] += 1
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_sampled_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"]
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_sampled_points_require_exact_structure(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    directory = _representation_dir(tmp_path, representation)
    points_path = directory / "sampled_points.json"
    points = json.loads(points_path.read_text(encoding="utf-8"))
    points["unexpected"] = True
    points_path.write_text(json.dumps(points), encoding="utf-8")
    manifest_path = directory / "representation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["geometry_fingerprint"] = fingerprint_file(points_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_sampled_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"]
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_coordinated_points_and_manifest_tamper_is_bound_to_audit(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    directory = _representation_dir(tmp_path, representation)
    points_path = directory / "sampled_points.json"
    points = json.loads(points_path.read_text(encoding="utf-8"))
    points["points"][0][0] += 1.0
    points_path.write_text(json.dumps(points), encoding="utf-8")
    manifest_path = directory / "representation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["geometry_fingerprint"] = fingerprint_file(points_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_sampled_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"]
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_representation_reader_rejects_float_point_count_spoof(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    path = _representation_dir(tmp_path, representation) / "representation.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["point_count"] = 10.0
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        sampling_module._load_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"],
            require_audit=False,
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_sampled_points_reader_rejects_integer_coordinate(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    directory = _representation_dir(tmp_path, representation)
    points_path = directory / "sampled_points.json"
    points = json.loads(points_path.read_text(encoding="utf-8"))
    points["points"][0][0] = 1
    points_path.write_text(json.dumps(points), encoding="utf-8")
    manifest_path = directory / "representation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["geometry_fingerprint"] = fingerprint_file(points_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        sampling_module._load_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"],
            require_audit=False,
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_representation_provenance_is_bound_to_audit_start(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    path = _representation_dir(tmp_path, representation) / "representation.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["generated_by"] = "mallory"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_sampled_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"]
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_representation_owner_is_part_of_public_integrity(tmp_path):
    _prepare(tmp_path)
    representation = _sample(tmp_path)
    path = _representation_dir(tmp_path, representation) / "operation_owner.json"
    owner = json.loads(path.read_text(encoding="utf-8"))
    owner["unexpected"] = True
    path.write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(ModelMatchingError) as exc_info:
        load_sampled_representation(
            tmp_path, "pump-a", "v1", representation["representation_id"]
        )
    assert exc_info.value.code == "model_representation_integrity_error"


def test_list_ignores_partial_owner_directory(tmp_path):
    _prepare(tmp_path)
    partial = (tmp_path / "models" / "pump-a" / "representations" / "v1"
               / "cad_sampled" / f"cad-sampled-{'0' * 64}")
    partial.mkdir(parents=True)
    (partial / "operation_owner.json").write_text("{}", encoding="utf-8")
    assert list_sampled_representations(tmp_path, "pump-a", "v1") == []


def test_incomplete_foreign_owner_is_not_taken_over(tmp_path):
    _prepare(tmp_path)
    config = sampling_module.build_sampling_config(10, 7)
    representation_id = (
        f"cad-sampled-{sampling_module.sampling_config_fingerprint(config)}"
    )
    partial = (tmp_path / "models" / "pump-a" / "representations" / "v1"
               / "cad_sampled" / representation_id)
    partial.mkdir(parents=True)
    (partial / "operation_owner.json").write_text(
        json.dumps({"operation_id": "foreign"}), encoding="utf-8"
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "operation_busy"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_existing_candidate_without_owner_is_not_claimed(tmp_path):
    _prepare(tmp_path)
    config = sampling_module.build_sampling_config(10, 7)
    representation_id = (
        f"cad-sampled-{sampling_module.sampling_config_fingerprint(config)}"
    )
    candidate = (tmp_path / "models" / "pump-a" / "representations" / "v1"
                 / "cad_sampled" / representation_id)
    candidate.mkdir(parents=True)
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "operation_busy"
    assert not (candidate / "operation_owner.json").exists()
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_owner_no_replace_conflict_is_reloaded_and_rejected(tmp_path, monkeypatch):
    _prepare(tmp_path)
    original_publish = sampling_module._publish_sampling_json

    def collide(path, value):
        if path.name == "operation_owner.json":
            path.write_text(json.dumps({"operation_id": "foreign"}), encoding="utf-8")
            return False
        return original_publish(path, value)

    monkeypatch.setattr(sampling_module, "_publish_sampling_json", collide)
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "operation_busy"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_owner_no_replace_requires_canonical_bytes(tmp_path, monkeypatch):
    _prepare(tmp_path)
    original_publish = sampling_module._publish_sampling_json

    def collide(path, value):
        if path.name == "operation_owner.json":
            path.write_text(json.dumps(value, sort_keys=False), encoding="utf-8")
            return False
        return original_publish(path, value)

    monkeypatch.setattr(sampling_module, "_publish_sampling_json", collide)
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "operation_busy"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_points_no_replace_rejects_numeric_byte_spoof(tmp_path, monkeypatch):
    _prepare(tmp_path)
    original_publish = sampling_module._publish_sampling_json

    def collide(path, value):
        if path.name == "sampled_points.json":
            conflicting = dict(value)
            conflicting["point_count"] = 10.0
            path.write_text(json.dumps(conflicting), encoding="utf-8")
            return False
        return original_publish(path, value)

    monkeypatch.setattr(sampling_module, "_publish_sampling_json", collide)
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "model_representation_integrity_error"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_representation_no_replace_conflict_is_reloaded_and_rejected(
    tmp_path, monkeypatch
):
    _prepare(tmp_path)
    original_publish = sampling_module._publish_sampling_json

    def collide(path, value):
        if path.name == "representation.json":
            conflicting = dict(value)
            conflicting["status"] = "broken"
            path.write_text(json.dumps(conflicting), encoding="utf-8")
            return False
        return original_publish(path, value)

    monkeypatch.setattr(sampling_module, "_publish_sampling_json", collide)
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "model_representation_integrity_error"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_completed_replay_does_not_enter_resource_lock(tmp_path, monkeypatch):
    _prepare(tmp_path)
    representation = _sample(tmp_path)

    @contextmanager
    def busy_lock(*_args, **_kwargs):
        raise ModelMatchingError("operation_busy", "locked")
        yield

    monkeypatch.setattr(sampling_module, "model_resource_lock", busy_lock)
    assert _sample(tmp_path) == representation
    assert load_operation(tmp_path, "op-sample-001")["status"] == "completed"


def test_resource_lock_contention_keeps_operation_running(tmp_path, monkeypatch):
    _prepare(tmp_path)

    @contextmanager
    def busy_lock(*_args, **_kwargs):
        raise ModelMatchingError("operation_busy", "locked")
        yield

    monkeypatch.setattr(sampling_module, "model_resource_lock", busy_lock)
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "operation_busy"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"


def test_directory_fsync_failure_requires_in_place_recovery(tmp_path, monkeypatch):
    _prepare(tmp_path)
    original_fsync = sampling_module._fsync_directory

    def fail_owner_directory(path):
        if path.name.startswith("cad-sampled-"):
            raise OSError("fsync unavailable")
        return original_fsync(path)

    monkeypatch.setattr(
        sampling_module,
        "_fsync_directory",
        fail_owner_directory,
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"
    monkeypatch.setattr(sampling_module, "_fsync_directory", original_fsync)
    assert _sample(tmp_path)["status"] == "ready"


def test_sampling_audit_event_order_is_stable(tmp_path):
    _prepare(tmp_path)
    _sample(tmp_path)
    snapshot = read_verified_operation_snapshot(tmp_path, "op-sample-001")
    assert [event["event_type"] for event in snapshot["events"]] == [
        "operation.started",
        "model_sampling.source_verified",
        "model_sampling.points_generated",
        "model_sampling.representation_published",
        "operation.completed",
    ]


def test_retry_completes_visible_representation_after_audit_failure(tmp_path, monkeypatch):
    _prepare(tmp_path)
    original_complete = sampling_module.complete_operation
    monkeypatch.setattr(
        sampling_module, "complete_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ModelMatchingError("audit_persistence_error", "unavailable")
        ),
    )
    with pytest.raises(ModelMatchingError) as exc_info:
        _sample(tmp_path)
    assert exc_info.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "running"
    monkeypatch.setattr(sampling_module, "complete_operation", original_complete)
    assert _sample(tmp_path)["status"] == "ready"
    assert load_operation(tmp_path, "op-sample-001")["status"] == "completed"
