import copy
import hashlib
import json
import struct

import pytest

from pc_system import mesh_reconstruction
from pc_system.mesh_reconstruction import create_mesh_run, load_mesh_run
from pc_system.mesh_reconstruction_config import (
    build_mesh_config,
    build_mesh_quality_policy,
)
from pc_system.mesh_worker_contract import validate_mesh_worker_result
from pc_system.model_matching_audit import load_operation, read_operation_events
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal
from pc_system.scan_to_mesh_errors import ScanToMeshError


EXPERT = Principal("mesh-expert", frozenset({"expert"}), "configured_token")
SOURCE_REF = {
    "asset_id": "scan",
    "release_id": "release-1",
    "instance_id": "pipe-1",
}


def _compact(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ).encode("utf-8")


def _fingerprint(value):
    return hashlib.sha256(_compact(value)).hexdigest()


def _source_snapshot():
    payload = {
        "schema_version": "1.0",
        "source_kind": "phase14_correction_release",
        "source_identity": {
            **SOURCE_REF,
            "class_id": "pipe",
        },
        "coordinate_unit": "m",
        "points": [
            [-1.0, -1.0, 0.0],
            [1.0, -1.0, 0.0],
            [1.0, 1.0, 0.0],
            [-1.0, 1.0, 0.0],
        ],
        "point_count": 4,
        "bounds_m": {"min": [-1.0, -1.0, 0.0], "max": [1.0, 1.0, 0.0]},
        "source_to_local_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "source_evidence": {"source_fingerprint": "a" * 64},
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


def _config():
    return build_mesh_config(
        "mesh-poisson-v1",
        {
            "schema_version": "1.0",
            "algorithm": "poisson",
            "worker_version": "open3d-mesh-0.19-v1",
            "normal_estimation": {
                "radius_m": 0.05,
                "max_nn": 30,
                "orientation": "consistent_tangent_plane",
                "orientation_k": 20,
            },
            "voxel_size_m": 0.0,
            "poisson": {"depth": 8, "density_quantile": 0.02},
            "ball_pivoting": None,
            "repair": {
                "remove_duplicate_vertices": True,
                "remove_degenerate_triangles": True,
                "remove_non_manifold_duplicates": True,
                "minimum_component_triangles": 0,
                "maximum_removed_component_fraction": 0.02,
                "fill_holes": False,
            },
            "preview": {"target_triangles": 100},
            "limits": {
                "maximum_input_points": 100,
                "maximum_vertices": 100,
                "maximum_triangles": 100,
                "maximum_artifact_bytes": 1_048_576,
                "timeout_seconds": 60,
            },
            "random_seed": 1,
        },
    )


def _policy():
    return build_mesh_quality_policy(
        "mesh-quality-v1",
        {
            "schema_version": "1.0",
            "policy_kind": "engineering_fixture",
            "production_context": None,
            "requires_watertight": False,
            "hard_limits": {
                "maximum_non_finite_vertices": 0,
                "maximum_non_finite_faces": 0,
                "maximum_degenerate_face_fraction": 0.02,
                "maximum_non_manifold_edge_fraction": 0.05,
                "maximum_component_count": 20,
                "minimum_largest_component_fraction": 0.80,
                "minimum_point_coverage": 0.75,
                "maximum_point_to_mesh_p95_m": 0.05,
                "maximum_repair_displacement_m": 0.01,
                "maximum_triangles": 100,
            },
            "review_limits": {
                "maximum_boundary_edge_fraction": 0.10,
                "maximum_component_count": 3,
                "minimum_largest_component_fraction": 0.95,
                "minimum_point_coverage": 0.90,
                "maximum_point_to_mesh_p95_m": 0.02,
                "maximum_repair_displacement_m": 0.005,
            },
            "decisions": {
                "hard_failure": "rejected",
                "review_threshold": "review_required",
                "all_passed": "passed",
                "auto_publish_passed": False,
            },
        },
    )


def _ply(vertices, faces):
    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(vertices)}",
        "property double x",
        "property double y",
        "property double z",
        f"element face {len(faces)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    lines.extend(" ".join(map(str, point)) for point in vertices)
    lines.extend("3 " + " ".join(map(str, face)) for face in faces)
    return ("\n".join(lines) + "\n").encode("ascii")


def _glb(vertices, faces):
    positions = b"".join(struct.pack("<fff", *point) for point in vertices)
    indices = b"".join(struct.pack("<III", *face) for face in faces)
    binary = positions + indices
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteLength": len(positions), "byteOffset": 0},
            {"buffer": 0, "byteLength": len(indices), "byteOffset": len(positions)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(vertices), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5125, "count": len(faces) * 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    json_chunk = _compact(document)
    while len(json_chunk) % 4:
        json_chunk += b" "
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def _descriptor(path, *, geometry=False):
    payload = path.read_bytes()
    value = {
        "path": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if geometry:
        value.update(vertex_count=4, triangle_count=4)
    return value


def _metrics(**changes):
    value = {
        "schema_version": "1.0",
        "raw_vertex_count": 4,
        "raw_triangle_count": 4,
        "repaired_vertex_count": 4,
        "repaired_triangle_count": 4,
        "non_finite_vertex_count": 0,
        "non_finite_face_count": 0,
        "duplicate_vertex_count": 0,
        "degenerate_triangle_count": 0,
        "non_manifold_edge_count": 0,
        "boundary_edge_count": 0,
        "component_count": 1,
        "largest_component_fraction": 1.0,
        "normal_consistency_fraction": 1.0,
        "point_to_mesh_p50_m": 0.001,
        "point_to_mesh_p95_m": 0.002,
        "point_to_mesh_max_m": 0.003,
        "mesh_to_point_coverage": 1.0,
        "repair_max_displacement_m": 0.0,
    }
    value.update(changes)
    return value


class FakeWorker:
    def __init__(self, *, metrics=None, error=None):
        self.metrics = _metrics() if metrics is None else metrics
        self.error = error
        self.calls = 0

    def __call__(self, request, workspace):
        self.calls += 1
        if self.error is not None:
            raise self.error
        vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        faces = [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]
        for name in ("raw_mesh.ply", "repaired_mesh.ply"):
            (workspace / name).write_bytes(_ply(vertices, faces))
        (workspace / "preview_mesh.glb").write_bytes(_glb(vertices, faces))
        metrics_path = workspace / "mesh_metrics.json"
        metrics_path.write_bytes(_canonical(self.metrics))
        result = {
            "schema_version": "1.0",
            "status": "completed",
            "request_fingerprint": request["request_fingerprint"],
            "algorithm": request["algorithm"],
            "worker_version": request["worker_version"],
            "artifacts": {
                "raw_mesh": _descriptor(workspace / "raw_mesh.ply", geometry=True),
                "repaired_mesh": _descriptor(workspace / "repaired_mesh.ply", geometry=True),
                "preview_mesh": _descriptor(workspace / "preview_mesh.glb", geometry=True),
                "mesh_metrics": _descriptor(metrics_path),
            },
            "metrics_fingerprint": _fingerprint(self.metrics),
        }
        (workspace / "worker_result.json").write_bytes(_canonical(result))
        return validate_mesh_worker_result(workspace, request)


@pytest.fixture
def frozen_dependencies(monkeypatch):
    source = _source_snapshot()
    config = _config()
    policy = _policy()
    monkeypatch.setattr(mesh_reconstruction, "freeze_mesh_source", lambda *args, **kwargs: copy.deepcopy(source))
    monkeypatch.setattr(mesh_reconstruction, "load_mesh_config", lambda *args, **kwargs: copy.deepcopy(config))
    monkeypatch.setattr(mesh_reconstruction, "load_mesh_quality_policy", lambda *args, **kwargs: copy.deepcopy(policy))
    return source, config, policy


def _create(root, worker, *, run_id="mesh-run-1", operation_id="mesh-op-1"):
    return create_mesh_run(
        root,
        run_id=run_id,
        source_kind="phase14_correction_release",
        source_ref=SOURCE_REF,
        config_id="mesh-poisson-v1",
        policy_id="mesh-quality-v1",
        worker=worker,
        principal=EXPERT,
        operation_id=operation_id,
        request_id=f"request-{operation_id}",
        idempotency_key=f"idem-{operation_id}",
    )


@pytest.mark.parametrize(
    ("changes", "status"),
    [({}, "passed"), ({"mesh_to_point_coverage": 0.85}, "review_required"), ({"mesh_to_point_coverage": 0.70}, "rejected")],
)
def test_mesh_run_publishes_three_quality_states(
    tmp_path, frozen_dependencies, changes, status
):
    worker = FakeWorker(metrics=_metrics(**changes))
    run_id = f"mesh-run-{status}"
    operation_id = f"mesh-op-{status}"

    run = _create(tmp_path, worker, run_id=run_id, operation_id=operation_id)

    assert run["status"] == status
    assert run["state_history"] == ["requested", "running", "quality_checked", status]
    assert worker.calls == 1
    assert load_mesh_run(tmp_path, run_id) == run
    assert load_operation(tmp_path, operation_id)["status"] == "completed"
    event_types = [event["event_type"] for event in read_operation_events(tmp_path, operation_id)]
    assert event_types == [
        "operation.started",
        "mesh.run_started",
        "mesh.worker_completed",
        "mesh.quality_checked",
        "mesh.run_completed",
        "operation.completed",
    ]


def test_completed_run_replays_without_calling_worker_again(tmp_path, frozen_dependencies):
    worker = FakeWorker()

    first = _create(tmp_path, worker)
    second = _create(tmp_path, worker)

    assert second == first
    assert worker.calls == 1


def test_quality_report_binds_the_exact_worker_metrics_fingerprint(
    tmp_path, frozen_dependencies
):
    integer_distances = _metrics(
        point_to_mesh_p50_m=0,
        point_to_mesh_p95_m=0,
        point_to_mesh_max_m=0,
        repair_max_displacement_m=0,
    )
    _create(tmp_path, FakeWorker(metrics=integer_distances))
    directory = tmp_path / "scan_to_cad" / "mesh_runs" / "mesh-run-1"
    worker_result = json.loads(
        (directory / "worker_result.json").read_text(encoding="utf-8")
    )
    quality = json.loads(
        (directory / "quality_report.json").read_text(encoding="utf-8")
    )

    assert quality["metrics_fingerprint"] == worker_result["metrics_fingerprint"]


@pytest.mark.parametrize("mutation", ["changed_mesh", "missing_snapshot"])
def test_committed_run_reports_internal_tampering_as_run_integrity_error(
    tmp_path, frozen_dependencies, mutation
):
    _create(tmp_path, FakeWorker())
    directory = tmp_path / "scan_to_cad" / "mesh_runs" / "mesh-run-1"
    if mutation == "changed_mesh":
        (directory / "raw_mesh.ply").write_bytes(b"tampered")
    else:
        (directory / "policy_snapshot.json").unlink()

    with pytest.raises(ScanToMeshError) as caught:
        load_mesh_run(tmp_path, "mesh-run-1")

    assert caught.value.code == "mesh_run_integrity_error"


def test_explicit_worker_failure_is_failed_and_never_falls_back(tmp_path, frozen_dependencies):
    worker = FakeWorker(error=ScanToMeshError("mesh_worker_failed", "算法失败。"))

    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, worker)

    assert caught.value.code == "mesh_worker_failed"
    assert worker.calls == 1
    assert load_operation(tmp_path, "mesh-op-1")["status"] == "failed"
    assert not (tmp_path / "scan_to_cad" / "mesh_runs" / "mesh-run-1" / "commit.json").exists()


def test_retry_after_worker_result_recovers_without_rerunning_worker(
    tmp_path, frozen_dependencies, monkeypatch
):
    worker = FakeWorker()
    real_ensure = mesh_reconstruction.ensure_operation_event
    failed = False

    def fail_once(root, operation_id, event_type, details):
        nonlocal failed
        if event_type == "mesh.worker_completed" and not failed:
            failed = True
            raise ModelMatchingError("audit_persistence_error", "模拟审计中断")
        return real_ensure(root, operation_id, event_type, details)

    monkeypatch.setattr(mesh_reconstruction, "ensure_operation_event", fail_once)
    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, worker)
    assert caught.value.code == "publication_recovery_required"
    monkeypatch.setattr(mesh_reconstruction, "ensure_operation_event", real_ensure)

    run = _create(tmp_path, worker)

    assert run["status"] == "passed"
    assert worker.calls == 1


def test_quality_publication_io_failure_recovers_without_rerunning_worker(
    tmp_path, frozen_dependencies, monkeypatch
):
    worker = FakeWorker()
    real_publish = mesh_reconstruction._publish_exact_json
    failed = False

    def fail_quality_once(path, value, **kwargs):
        nonlocal failed
        if path.name == "quality_report.json" and not failed:
            failed = True
            raise OSError("模拟质量报告发布中断")
        return real_publish(path, value, **kwargs)

    monkeypatch.setattr(mesh_reconstruction, "_publish_exact_json", fail_quality_once)
    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, worker)
    assert caught.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "mesh-op-1")["status"] == "running"
    monkeypatch.setattr(mesh_reconstruction, "_publish_exact_json", real_publish)

    run = _create(tmp_path, worker)

    assert run["status"] == "passed"
    assert worker.calls == 1


def test_completed_audit_response_loss_replays_committed_run(
    tmp_path, frozen_dependencies, monkeypatch
):
    worker = FakeWorker()
    real_complete = mesh_reconstruction.complete_operation
    failed = False

    def complete_then_raise(root, operation_id, result):
        nonlocal failed
        completed = real_complete(root, operation_id, result)
        if not failed:
            failed = True
            raise ModelMatchingError("audit_persistence_error", "模拟响应丢失")
        return completed

    monkeypatch.setattr(mesh_reconstruction, "complete_operation", complete_then_raise)
    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, worker)
    assert caught.value.code == "publication_recovery_required"
    monkeypatch.setattr(mesh_reconstruction, "complete_operation", real_complete)

    run = _create(tmp_path, worker)

    assert run["status"] == "passed"
    assert worker.calls == 1


def test_other_operation_cannot_take_over_existing_run(tmp_path, frozen_dependencies, monkeypatch):
    worker = FakeWorker()
    real_ensure = mesh_reconstruction.ensure_operation_event

    def interrupt(root, operation_id, event_type, details):
        if event_type == "mesh.worker_completed":
            raise ModelMatchingError("audit_persistence_error", "模拟中断")
        return real_ensure(root, operation_id, event_type, details)

    monkeypatch.setattr(mesh_reconstruction, "ensure_operation_event", interrupt)
    with pytest.raises(ScanToMeshError):
        _create(tmp_path, worker)

    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, worker, operation_id="mesh-op-other")

    assert caught.value.code == "operation_busy"
    assert worker.calls == 1


def test_unowned_nonempty_run_directory_requires_recovery(tmp_path, frozen_dependencies):
    directory = tmp_path / "scan_to_cad" / "mesh_runs" / "mesh-run-1"
    directory.mkdir(parents=True)
    (directory / "unknown.tmp").write_text("partial", encoding="utf-8")

    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, FakeWorker())

    assert caught.value.code == "publication_recovery_required"


@pytest.mark.parametrize("filename", ["source_snapshot.json", "config_snapshot.json", "policy_snapshot.json"])
def test_retry_rejects_tampered_frozen_snapshot(
    tmp_path, frozen_dependencies, monkeypatch, filename
):
    worker = FakeWorker()
    real_ensure = mesh_reconstruction.ensure_operation_event

    def interrupt(root, operation_id, event_type, details):
        if event_type == "mesh.worker_completed":
            raise ModelMatchingError("audit_persistence_error", "模拟中断")
        return real_ensure(root, operation_id, event_type, details)

    monkeypatch.setattr(mesh_reconstruction, "ensure_operation_event", interrupt)
    with pytest.raises(ScanToMeshError):
        _create(tmp_path, worker)
    path = tmp_path / "scan_to_cad" / "mesh_runs" / "mesh-run-1" / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema_version"] = "tampered"
    path.write_bytes(_canonical(value))
    monkeypatch.setattr(mesh_reconstruction, "ensure_operation_event", real_ensure)

    with pytest.raises(ScanToMeshError) as caught:
        _create(tmp_path, worker)

    assert caught.value.code == "mesh_run_integrity_error"
    assert worker.calls == 1
