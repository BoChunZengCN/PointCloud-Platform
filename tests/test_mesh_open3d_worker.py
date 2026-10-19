import importlib
import importlib.util
import hashlib
import json
import math

import numpy as np
import pytest

from pc_system.scan_to_mesh_errors import ScanToMeshError
from pc_system.mesh_reconstruction_config import build_mesh_config
from pc_system.mesh_worker_contract import (
    prepare_mesh_worker_request,
    validate_mesh_worker_result,
)
from workers.open3d_mesh import worker


def test_small_component_removal_is_deterministic_and_bounded():
    cluster_sizes = np.asarray([90, 3, 2, 5], dtype=np.int64)
    cluster_ids = np.concatenate(
        [np.full(size, cluster_id) for cluster_id, size in enumerate(cluster_sizes)]
    )

    mask = worker._bounded_component_removal_mask(
        cluster_ids,
        cluster_sizes,
        minimum_triangles=6,
        maximum_removed_fraction=0.07,
    )

    assert int(np.sum(mask)) == 5
    assert set(cluster_ids[mask]) == {1, 2}
    assert not np.any(mask[cluster_ids == 3])


def test_small_component_removal_can_be_disabled():
    mask = worker._bounded_component_removal_mask(
        np.asarray([0, 0, 1]),
        np.asarray([2, 1]),
        minimum_triangles=0,
        maximum_removed_fraction=1.0,
    )

    assert not np.any(mask)


def test_missing_open3d_dependency_is_explicit_and_never_falls_back(monkeypatch):
    real_import = importlib.import_module

    def missing(name, *args, **kwargs):
        if name == "open3d":
            raise ImportError("Open3D is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(worker.importlib, "import_module", missing)

    with pytest.raises(ScanToMeshError) as caught:
        worker.load_open3d()

    assert caught.value.code == "mesh_worker_unavailable"


@pytest.mark.skipif(
    importlib.util.find_spec("open3d") is None,
    reason="可选 Open3D 依赖未安装",
)
@pytest.mark.parametrize("algorithm", ["poisson", "ball_pivoting"])
def test_real_open3d_worker_keeps_the_explicit_algorithm(tmp_path, algorithm):
    points = []
    for latitude in range(1, 8):
        phi = math.pi * latitude / 8
        for longitude in range(16):
            theta = 2 * math.pi * longitude / 16
            points.append(
                [
                    math.sin(phi) * math.cos(theta),
                    math.sin(phi) * math.sin(theta),
                    math.cos(phi),
                ]
            )
    source = {
        "schema_version": "1.0",
        "source_kind": "phase15e_scanned_reference",
        "source_identity": {"model_id": "sphere", "version_id": "v1"},
        "coordinate_unit": "m",
        "points": points,
        "point_count": len(points),
        "bounds_m": {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
        "source_to_local_4x4": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
        "source_evidence": {"source_fingerprint": "a" * 64},
    }
    source["fingerprint"] = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    raw_config = {
        "schema_version": "1.0",
        "algorithm": algorithm,
        "worker_version": "open3d-mesh-0.19-v1",
        "normal_estimation": {"radius_m": 0.5, "max_nn": 30, "orientation": "consistent_tangent_plane", "orientation_k": 20},
        "voxel_size_m": 0.0,
        "poisson": {"depth": 5, "density_quantile": 0.0} if algorithm == "poisson" else None,
        "ball_pivoting": {"radii_m": [0.2, 0.4]} if algorithm == "ball_pivoting" else None,
        "repair": {"remove_duplicate_vertices": True, "remove_degenerate_triangles": True, "remove_non_manifold_duplicates": True, "minimum_component_triangles": 0, "maximum_removed_component_fraction": 0.02, "fill_holes": False},
        "preview": {"target_triangles": 200},
        "limits": {"maximum_input_points": 1000, "maximum_vertices": 100000, "maximum_triangles": 200000, "maximum_artifact_bytes": 10485760, "timeout_seconds": 120},
        "random_seed": 7,
    }
    config = build_mesh_config(f"mesh-{algorithm}", raw_config)
    request = prepare_mesh_worker_request(tmp_path, source_snapshot=source, config=config)

    result = worker.execute_request(tmp_path)

    assert result["algorithm"] == algorithm
    assert validate_mesh_worker_result(tmp_path, request) == result
