import json
from pathlib import Path

from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_service import run_segmentation


def sample_points() -> list[dict]:
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.0, "z": 0.0},
        {"x": 5.0, "y": 0.0, "z": 0.0},
        {"x": 5.1, "y": 0.0, "z": 0.0},
    ]


def write_completed_run(
    project: Path,
    *,
    asset_id: str = "scan",
    run_id: str = "run-001",
) -> Path:
    points = sample_points()
    source = project / "samples" / "scan.points.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(points), encoding="utf-8")
    run_segmentation(
        project,
        asset_id=asset_id,
        asset_version="v1",
        source_uri=str(source),
        points=points,
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 0.2,
            "min_points": 1,
            "max_points": 100,
        },
        run_id=run_id,
    )
    return source


def write_development_benchmark(project: Path) -> None:
    points = sample_points()
    labels = {
        "schema_version": "1.0",
        "point_labels": [
            {
                "point_index": 0,
                **points[0],
                "instance_id": "verified-pipe",
                "class_id": "pipe",
                "is_noise": False,
            }
        ],
        "boxes": [
            {
                "instance_id": "verified-pipe",
                "class_id": "pipe",
                "center": [0.0, 0.0, 0.0],
                "size": [0.001, 0.001, 0.001],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        ],
    }
    root = project / "benchmarks" / "bench-dev"
    (root / "samples" / "sample-001").mkdir(parents=True)
    manifest = {
        "schema_version": "1.0",
        "benchmark_id": "bench-dev",
        "benchmark_version": "v1",
        "split": "development",
        "scene_type": "pipe-rack",
        "point_density": 10.0,
        "coordinate_unit": "m",
        "label_version": "labels-v1",
        "license": "internal",
        "samples": [
            {
                "sample_id": "sample-001",
                "asset_id": "scan",
                "asset_version": "v1",
                "source_uri": str(project / "samples" / "scan.points.json"),
                "source_fingerprint": fingerprint_points(points),
                "labels_path": "samples/sample-001/labels.json",
                "labels_format": "json",
            }
        ],
    }
    (root / "benchmark.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "samples" / "sample-001" / "labels.json").write_text(
        json.dumps(labels), encoding="utf-8"
    )


def correction_session(project: Path, *, actor: str = "alice") -> dict:
    from pc_system.segmentation_corrections import create_correction_session

    write_completed_run(project)
    return create_correction_session(
        project,
        asset_id="scan",
        run_id="run-001",
        session_id="session-001",
        sample_id="sample-001",
        actor=actor,
    )
