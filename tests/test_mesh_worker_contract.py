import copy
import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from pc_system.mesh_reconstruction_config import build_mesh_config
from pc_system.mesh_worker_contract import (
    load_mesh_worker_input,
    load_mesh_worker_request,
    prepare_mesh_worker_request,
    validate_mesh_worker_result,
)
from pc_system.scan_to_mesh_errors import ScanToMeshError


def _canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _source_snapshot():
    payload = {
        "schema_version": "1.0",
        "source_kind": "phase14_correction_release",
        "source_identity": {
            "asset_id": "scan",
            "release_id": "release-1",
            "instance_id": "pipe-1",
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
    return {**payload, "fingerprint": _digest(payload)}


def _config(algorithm="poisson"):
    value = {
        "schema_version": "1.0",
        "algorithm": algorithm,
        "worker_version": "open3d-mesh-0.19-v1",
        "normal_estimation": {
            "radius_m": 0.05,
            "max_nn": 30,
            "orientation": "consistent_tangent_plane",
            "orientation_k": 20,
        },
        "voxel_size_m": 0.0,
        "poisson": {"depth": 8, "density_quantile": 0.02}
        if algorithm == "poisson"
        else None,
        "ball_pivoting": None
        if algorithm == "poisson"
        else {"radii_m": [0.1, 0.2]},
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
    }
    return build_mesh_config(f"mesh-{algorithm}", value)


def _ply(vertices, faces):
    header = [
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
    body = [" ".join(str(value) for value in point) for point in vertices]
    body.extend("3 " + " ".join(str(index) for index in face) for face in faces)
    return ("\n".join(header + body) + "\n").encode("ascii")


def _glb(vertices, faces):
    positions = b"".join(
        struct.pack("<fff", *map(float, point)) for point in vertices
    )
    indices = b"".join(
        struct.pack("<III", *map(int, face)) for face in faces
    )
    binary = positions + indices
    while len(binary) % 4:
        binary += b"\0"
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteLength": len(positions), "byteOffset": 0},
            {
                "buffer": 0,
                "byteLength": len(indices),
                "byteOffset": len(positions),
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(vertices),
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5125,
                "count": len(faces) * 3,
                "type": "SCALAR",
            },
        ],
        "meshes": [
            {
                "primitives": [
                    {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}
                ]
            }
        ],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    json_chunk = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
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


def _artifact(path: Path, *, vertices=None, triangles=None):
    value = {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    if vertices is not None:
        value["vertex_count"] = vertices
        value["triangle_count"] = triangles
    return value


def _valid_result(workspace: Path, request: dict):
    vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    faces = [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]
    for name in ("raw_mesh.ply", "repaired_mesh.ply"):
        (workspace / name).write_bytes(_ply(vertices, faces))
    (workspace / "preview_mesh.glb").write_bytes(_glb(vertices, faces))
    metrics = {
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
    metrics_path = workspace / "mesh_metrics.json"
    metrics_path.write_bytes(_canonical_bytes(metrics))
    result = {
        "schema_version": "1.0",
        "status": "completed",
        "request_fingerprint": request["request_fingerprint"],
        "algorithm": request["algorithm"],
        "worker_version": request["worker_version"],
        "artifacts": {
            "raw_mesh": _artifact(workspace / "raw_mesh.ply", vertices=4, triangles=4),
            "repaired_mesh": _artifact(workspace / "repaired_mesh.ply", vertices=4, triangles=4),
            "preview_mesh": _artifact(workspace / "preview_mesh.glb", vertices=4, triangles=4),
            "mesh_metrics": _artifact(metrics_path),
        },
        "metrics_fingerprint": _digest(metrics),
    }
    (workspace / "worker_result.json").write_bytes(_canonical_bytes(result))
    return result


def _rewrite_result(workspace, result):
    (workspace / "worker_result.json").write_bytes(_canonical_bytes(result))


def test_valid_worker_manifest_ply_and_glb_are_verified(tmp_path):
    request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    expected = _valid_result(tmp_path, request)

    verified = validate_mesh_worker_result(tmp_path, request)

    assert verified == expected
    assert request["input"]["path"] == "source_points.json"
    assert request["limits"]["maximum_triangles"] == 100


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda result: result["artifacts"]["raw_mesh"].__setitem__("path", "../escape.ply"), "mesh_worker_result_invalid"),
        (lambda result: result["artifacts"]["raw_mesh"].__setitem__("path", "C:/escape.ply"), "mesh_worker_result_invalid"),
        (lambda result: result["artifacts"].__setitem__("unknown", copy.deepcopy(result["artifacts"]["raw_mesh"])), "mesh_worker_result_invalid"),
        (lambda result: result.__setitem__("algorithm", "ball_pivoting"), "mesh_worker_result_invalid"),
        (lambda result: result["artifacts"]["raw_mesh"].__setitem__("vertex_count", 101), "mesh_worker_limit_exceeded"),
    ],
)
def test_worker_manifest_rejects_paths_unknown_artifacts_algorithm_or_limits(
    tmp_path, mutation, code
):
    request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    result = _valid_result(tmp_path, request)
    mutation(result)
    _rewrite_result(tmp_path, result)

    with pytest.raises(ScanToMeshError) as caught:
        validate_mesh_worker_result(tmp_path, request)

    assert caught.value.code == code


@pytest.mark.parametrize("field", ["bytes", "sha256"])
def test_worker_artifact_rejects_declared_size_or_hash_mismatch(tmp_path, field):
    request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    result = _valid_result(tmp_path, request)
    result["artifacts"]["raw_mesh"][field] = (
        1 if field == "bytes" else "0" * 64
    )
    _rewrite_result(tmp_path, result)

    with pytest.raises(ScanToMeshError) as caught:
        validate_mesh_worker_result(tmp_path, request)

    assert caught.value.code == "mesh_worker_artifact_invalid"


@pytest.mark.parametrize("mutation", ["magic", "total_length", "accessor_count"])
def test_preview_glb_rejects_invalid_container_or_geometry_contract(
    tmp_path, mutation
):
    request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    result = _valid_result(tmp_path, request)
    path = tmp_path / "preview_mesh.glb"
    payload = bytearray(path.read_bytes())
    if mutation == "magic":
        payload[:4] = b"BAD!"
    elif mutation == "total_length":
        struct.pack_into("<I", payload, 8, len(payload) + 4)
    else:
        original = b'"count":4'
        offset = payload.find(original)
        assert offset >= 0
        payload[offset : offset + len(original)] = b'"count":5'
    path.write_bytes(payload)
    result["artifacts"]["preview_mesh"] = _artifact(
        path, vertices=4, triangles=4
    )
    _rewrite_result(tmp_path, result)

    with pytest.raises(ScanToMeshError) as caught:
        validate_mesh_worker_result(tmp_path, request)

    assert caught.value.code == "mesh_worker_artifact_invalid"


def test_worker_metrics_reject_non_finite_values_even_with_matching_hash(tmp_path):
    request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    result = _valid_result(tmp_path, request)
    path = tmp_path / "mesh_metrics.json"
    path.write_bytes(path.read_bytes().replace(b"0.002", b"NaN"))
    result["artifacts"]["mesh_metrics"] = _artifact(path)
    result["metrics_fingerprint"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _rewrite_result(tmp_path, result)

    with pytest.raises(ScanToMeshError) as caught:
        validate_mesh_worker_result(tmp_path, request)

    assert caught.value.code == "mesh_worker_result_invalid"


def test_worker_artifact_rejects_directory_and_link(tmp_path):
    request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    result = _valid_result(tmp_path, request)
    raw_path = tmp_path / "raw_mesh.ply"
    raw_path.unlink()
    raw_path.mkdir()
    with pytest.raises(ScanToMeshError) as directory_error:
        validate_mesh_worker_result(tmp_path, request)
    assert directory_error.value.code == "mesh_worker_artifact_invalid"

    raw_path.rmdir()
    try:
        os.symlink(tmp_path / "repaired_mesh.ply", raw_path)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 测试主体不能创建符号链接")
    with pytest.raises(ScanToMeshError) as link_error:
        validate_mesh_worker_result(tmp_path, request)
    assert link_error.value.code == "mesh_worker_artifact_invalid"


def test_request_rejects_changed_source_points_before_worker_consumption(tmp_path):
    prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    path = tmp_path / "source_points.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["points"][0][0] = -0.5
    path.write_bytes(_canonical_bytes(value))

    with pytest.raises(ScanToMeshError) as caught:
        load_mesh_worker_request(tmp_path)

    assert caught.value.code == "mesh_worker_request_invalid"


def test_worker_input_returns_points_from_the_validated_file_handle(tmp_path):
    expected_points = _source_snapshot()["points"]
    expected_request = prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )

    request, points_document = load_mesh_worker_input(tmp_path)

    assert request == expected_request
    assert points_document["points"] == expected_points


def test_request_rejects_mixed_algorithm_parameters_even_with_new_fingerprint(tmp_path):
    prepare_mesh_worker_request(
        tmp_path, source_snapshot=_source_snapshot(), config=_config()
    )
    path = tmp_path / "worker_request.json"
    request = json.loads(path.read_text(encoding="utf-8"))
    request["algorithm"] = "ball_pivoting"
    payload = {key: value for key, value in request.items() if key != "request_fingerprint"}
    request["request_fingerprint"] = _digest(payload)
    path.write_bytes(_canonical_bytes(request))

    with pytest.raises(ScanToMeshError) as caught:
        load_mesh_worker_request(tmp_path)

    assert caught.value.code == "mesh_worker_request_invalid"
