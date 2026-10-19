"""独立 Open3D 网格工作器入口。"""

import argparse
import hashlib
import importlib
import json
import struct
from pathlib import Path

import numpy as np

from pc_system.mesh_worker_contract import load_mesh_worker_input
from pc_system.model_sampling import _canonical_json_bytes
from pc_system.scan_to_mesh_errors import ScanToMeshError


def load_open3d():
    try:
        return importlib.import_module("open3d")
    except (ImportError, OSError) as exc:
        raise ScanToMeshError(
            "mesh_worker_unavailable", "Open3D 工作器依赖不可用。"
        ) from exc


def _write_ascii_ply(path: Path, vertices: np.ndarray, triangles: np.ndarray) -> None:
    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(vertices)}",
        "property double x",
        "property double y",
        "property double z",
        f"element face {len(triangles)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    lines.extend(" ".join(format(float(value), ".17g") for value in point) for point in vertices)
    lines.extend("3 " + " ".join(str(int(index)) for index in face) for face in triangles)
    path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))


def _write_glb(path: Path, vertices: np.ndarray, triangles: np.ndarray) -> None:
    positions = np.asarray(vertices, dtype="<f4").reshape((-1, 3)).tobytes(order="C")
    indices = np.asarray(triangles, dtype="<u4").reshape((-1, 3)).tobytes(order="C")
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
                "count": len(triangles) * 3,
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
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def _bounded_component_removal_mask(
    cluster_ids: np.ndarray,
    cluster_sizes: np.ndarray,
    *,
    minimum_triangles: int,
    maximum_removed_fraction: float,
) -> np.ndarray:
    mask = np.zeros(len(cluster_ids), dtype=bool)
    if minimum_triangles <= 0 or not len(cluster_ids):
        return mask
    budget = int(len(cluster_ids) * maximum_removed_fraction)
    removed = 0
    selected = []
    candidates = sorted(
        (
            (int(size), cluster_id)
            for cluster_id, size in enumerate(cluster_sizes)
            if int(size) < minimum_triangles
        ),
        key=lambda item: (item[0], item[1]),
    )
    for size, cluster_id in candidates:
        if removed + size > budget:
            break
        selected.append(cluster_id)
        removed += size
    if selected:
        mask = np.isin(cluster_ids, np.asarray(selected, dtype=np.int64))
    return mask


def _descriptor(path: Path, vertices: np.ndarray | None = None, triangles: np.ndarray | None = None) -> dict:
    payload = path.read_bytes()
    value = {"path": path.name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    if vertices is not None and triangles is not None:
        value.update(vertex_count=len(vertices), triangle_count=len(triangles))
    return value


def _geometry_metrics(raw, repaired, points, o3d) -> dict:
    raw_vertices = np.asarray(raw.vertices, dtype=np.float64)
    raw_triangles = np.asarray(raw.triangles, dtype=np.int64)
    vertices = np.asarray(repaired.vertices, dtype=np.float64)
    triangles = np.asarray(repaired.triangles, dtype=np.int64)
    finite_vertices = np.all(np.isfinite(vertices), axis=1) if len(vertices) else np.asarray([], dtype=bool)
    valid_faces = np.all((triangles >= 0) & (triangles < len(vertices)), axis=1) if len(triangles) else np.asarray([], dtype=bool)
    non_finite_faces = int(np.sum(~valid_faces))
    duplicate_vertices = len(vertices) - len(np.unique(vertices, axis=0)) if len(vertices) else 0
    degenerate = 0
    edge_counts = {}
    for face in triangles[valid_faces]:
        a, b, c = vertices[face]
        if len(set(map(int, face))) < 3 or np.linalg.norm(np.cross(b - a, c - a)) <= 1e-15:
            degenerate += 1
        for left, right in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = tuple(sorted((int(left), int(right))))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    boundary = sum(value == 1 for value in edge_counts.values())
    non_manifold = sum(value > 2 for value in edge_counts.values())
    if len(triangles):
        clusters, counts, _areas = repaired.cluster_connected_triangles()
        component_counts = list(counts)
    else:
        component_counts = []
    component_count = len(component_counts)
    largest_fraction = max(component_counts, default=0) / max(len(triangles), 1)
    distances = np.zeros(len(points), dtype=np.float64)
    if len(triangles) and len(points):
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(repaired))
        tensor = o3d.core.Tensor(np.asarray(points, dtype=np.float32))
        distances = scene.compute_distance(tensor).numpy().astype(np.float64)
    coverage = 0.0
    if len(vertices) and len(points):
        cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(points)))
        tree = o3d.geometry.KDTreeFlann(cloud)
        threshold = max(float(np.linalg.norm(np.ptp(points, axis=0))) * 0.01, 1e-9)
        covered = 0
        for vertex in vertices:
            count, _indices, squared = tree.search_knn_vector_3d(vertex, 1)
            if count and squared[0] <= threshold * threshold:
                covered += 1
        coverage = covered / len(vertices)
    return {
        "schema_version": "1.0",
        "raw_vertex_count": len(raw_vertices),
        "raw_triangle_count": len(raw_triangles),
        "repaired_vertex_count": len(vertices),
        "repaired_triangle_count": len(triangles),
        "non_finite_vertex_count": int(np.sum(~finite_vertices)),
        "non_finite_face_count": non_finite_faces,
        "duplicate_vertex_count": int(duplicate_vertices),
        "degenerate_triangle_count": int(degenerate),
        "non_manifold_edge_count": int(non_manifold),
        "boundary_edge_count": int(boundary),
        "component_count": int(component_count),
        "largest_component_fraction": float(largest_fraction),
        "normal_consistency_fraction": 1.0 if repaired.has_triangle_normals() else 0.0,
        "point_to_mesh_p50_m": float(np.percentile(distances, 50)) if len(distances) else 0.0,
        "point_to_mesh_p95_m": float(np.percentile(distances, 95)) if len(distances) else 0.0,
        "point_to_mesh_max_m": float(np.max(distances)) if len(distances) else 0.0,
        "mesh_to_point_coverage": float(coverage),
        "repair_max_displacement_m": 0.0,
    }


def execute_request(workspace: Path) -> dict:
    workspace = Path(workspace)
    request, points_document = load_mesh_worker_input(workspace)
    points = np.asarray(points_document["points"], dtype=np.float64)
    o3d = load_open3d()
    o3d.utility.random.seed(request["parameters"]["random_seed"])
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    voxel = request["parameters"]["voxel_size_m"]
    if voxel > 0:
        cloud = cloud.voxel_down_sample(voxel)
    normal = request["parameters"]["normal_estimation"]
    cloud.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=normal["radius_m"], max_nn=normal["max_nn"]))
    if normal["orientation"] == "consistent_tangent_plane":
        cloud.orient_normals_consistent_tangent_plane(normal["orientation_k"])
    else:
        cloud.orient_normals_towards_camera_location(np.zeros(3))
    if request["algorithm"] == "poisson":
        raw, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(cloud, depth=request["parameters"]["poisson"]["depth"])
        quantile = request["parameters"]["poisson"]["density_quantile"]
        if quantile > 0 and len(densities):
            raw.remove_vertices_by_mask(np.asarray(densities) < np.quantile(np.asarray(densities), quantile))
    elif request["algorithm"] == "ball_pivoting":
        radii = o3d.utility.DoubleVector(request["parameters"]["ball_pivoting"]["radii_m"])
        raw = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(cloud, radii)
    else:
        raise ScanToMeshError("mesh_worker_request_invalid", "工作器算法无效。")
    repaired = o3d.geometry.TriangleMesh(raw)
    repair = request["parameters"]["repair"]
    if repair["remove_duplicate_vertices"]:
        repaired.remove_duplicated_vertices()
    if repair["remove_degenerate_triangles"]:
        repaired.remove_degenerate_triangles()
    repaired.remove_duplicated_triangles()
    if repair["remove_non_manifold_duplicates"]:
        repaired.remove_non_manifold_edges()
    if repair["minimum_component_triangles"] > 0 and len(repaired.triangles):
        cluster_ids, cluster_sizes, _areas = repaired.cluster_connected_triangles()
        removal_mask = _bounded_component_removal_mask(
            np.asarray(cluster_ids, dtype=np.int64),
            np.asarray(cluster_sizes, dtype=np.int64),
            minimum_triangles=repair["minimum_component_triangles"],
            maximum_removed_fraction=repair["maximum_removed_component_fraction"],
        )
        if np.any(removal_mask):
            repaired.remove_triangles_by_mask(removal_mask.tolist())
            repaired.remove_unreferenced_vertices()
    repaired.compute_triangle_normals()
    target = request["parameters"]["preview"]["target_triangles"]
    preview = repaired if len(repaired.triangles) <= target else repaired.simplify_quadric_decimation(target)
    artifacts = {}
    for key, name, mesh in (
        ("raw_mesh", "raw_mesh.ply", raw),
        ("repaired_mesh", "repaired_mesh.ply", repaired),
    ):
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        triangles = np.asarray(mesh.triangles, dtype=np.int64)
        path = workspace / name
        _write_ascii_ply(path, vertices, triangles)
        artifacts[key] = _descriptor(path, vertices, triangles)
    preview_vertices = np.asarray(preview.vertices, dtype=np.float64)
    preview_triangles = np.asarray(preview.triangles, dtype=np.int64)
    preview_path = workspace / "preview_mesh.glb"
    _write_glb(preview_path, preview_vertices, preview_triangles)
    artifacts["preview_mesh"] = _descriptor(
        preview_path, preview_vertices, preview_triangles
    )
    metrics = _geometry_metrics(raw, repaired, points, o3d)
    metrics_path = workspace / "mesh_metrics.json"
    metrics_path.write_bytes(_canonical_json_bytes(metrics))
    artifacts["mesh_metrics"] = _descriptor(metrics_path)
    metrics_fingerprint = hashlib.sha256(json.dumps(metrics, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    result = {
        "schema_version": "1.0",
        "status": "completed",
        "request_fingerprint": request["request_fingerprint"],
        "algorithm": request["algorithm"],
        "worker_version": request["worker_version"],
        "artifacts": artifacts,
        "metrics_fingerprint": metrics_fingerprint,
    }
    (workspace / "worker_result.json").write_bytes(_canonical_json_bytes(result))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    if args.request != "worker_request.json":
        raise ScanToMeshError("mesh_worker_request_invalid", "请求文件名必须固定。")
    execute_request(Path.cwd())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
