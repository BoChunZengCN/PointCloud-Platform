import json

from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_service import run_segmentation


def test_point_fingerprint_is_deterministic_and_order_sensitive():
    points = [{"x": 0, "y": 1, "z": 2}, {"x": 3, "y": 4, "z": 5}]

    assert fingerprint_points(points) == fingerprint_points([dict(item) for item in points])
    assert fingerprint_points(points) != fingerprint_points(list(reversed(points)))


def test_point_fingerprint_ignores_non_geometric_attributes():
    basic = [{"x": 0, "y": 1, "z": 2}]
    colored = [{"x": 0, "y": 1, "z": 2, "red": 65535}]

    assert fingerprint_points(basic) == fingerprint_points(colored)


def test_run_records_source_fingerprint_and_membership_indices(tmp_path):
    points = [{"x": 0, "y": 0, "z": 0}, {"x": 0.1, "y": 0, "z": 0}]

    run = run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=points,
        config={"engine": "builtin_geometric", "distance_threshold": 0.2, "min_points": 1},
        run_id="run-001",
    )

    artifact = (
        tmp_path
        / "reports"
        / "segmentation_runs"
        / "scan"
        / "run-001"
        / run["artifacts"]["memberships"]["obj-001"]
    )
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert run["source_fingerprint"] == fingerprint_points(points)
    assert payload["source_point_indices"] == [0, 1]
    assert all("_source_index" not in point for point in payload["points"])


def test_membership_indices_survive_deduplication(tmp_path):
    points = [
        {"x": 0, "y": 0, "z": 0},
        {"x": 0, "y": 0, "z": 0},
        {"x": 1, "y": 0, "z": 0},
    ]

    run = run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=points,
        config={"engine": "builtin_geometric", "distance_threshold": 2.0, "min_points": 1},
        run_id="run-001",
    )

    artifact = (
        tmp_path
        / "reports"
        / "segmentation_runs"
        / "scan"
        / "run-001"
        / run["artifacts"]["memberships"]["obj-001"]
    )
    payload = json.loads(artifact.read_text(encoding="utf-8"))

    assert payload["source_point_indices"] == [0, 2]

