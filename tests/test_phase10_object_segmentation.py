import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.object_segmentation import (
    build_object_segmentation_quality,
    segment_object_candidates,
    segment_with_open3d_adapter,
    write_object_segmentation_report,
)


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def sample_object_points() -> list[dict]:
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0, "red": 120, "green": 110, "blue": 100},
        {"x": 0.4, "y": 0.1, "z": 0.2, "red": 122, "green": 112, "blue": 102},
        {"x": 0.2, "y": 0.5, "z": 0.1, "red": 118, "green": 108, "blue": 98},
        {"x": 8.0, "y": 8.0, "z": 1.0, "red": 20, "green": 30, "blue": 40},
        {"x": 8.4, "y": 8.2, "z": 1.1, "red": 22, "green": 32, "blue": 42},
        {"x": 30.0, "y": 30.0, "z": 0.0, "red": 1, "green": 1, "blue": 1},
    ]


def test_p10_m1_segments_object_candidates_from_point_records():
    report = segment_object_candidates(
        "scan",
        sample_object_points(),
        distance_threshold=1.0,
        min_points=2,
    )

    assert report["schema_version"] == "1.0"
    assert report["asset_id"] == "scan"
    assert report["method"] == "geometric_cluster"
    assert report["object_count"] == 2
    assert report["noise_point_count"] == 1
    assert [item["point_count"] for item in report["objects"]] == [3, 2]
    assert report["objects"][0]["object_id"] == "obj-001"
    assert report["objects"][0]["label"] == "object_candidate"
    assert report["objects"][0]["bounds"]["max"] == [0.4, 0.5, 0.2]
    assert report["objects"][1]["center"] == [8.2, 8.1, 1.05]


def test_p10_m2_writes_object_segmentation_json_and_markdown():
    project = case_dir("p10-write") / "workspace"
    report = segment_object_candidates("scan", sample_object_points(), distance_threshold=1.0, min_points=2)

    outputs = write_object_segmentation_report(report, project / "reports" / "object_segments" / "scan")

    assert outputs["json"].exists()
    assert outputs["markdown"].exists()
    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["object_count"] == 2
    assert "obj-001" in outputs["markdown"].read_text(encoding="utf-8")


def test_p10_m3_cli_segments_object_candidates_from_points_json():
    project = case_dir("p10-cli") / "workspace"
    points_path = project / "samples" / "scan_points.json"
    points_path.parent.mkdir(parents=True)
    points_path.write_text(json.dumps(sample_object_points()), encoding="utf-8")

    exit_code = main([
        "segment-objects",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--points-json",
        str(points_path),
        "--distance-threshold",
        "1.0",
        "--min-points",
        "2",
    ])

    output = project / "reports" / "object_segments" / "scan" / "object_segments.json"
    assert exit_code == 0
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["object_count"] == 2


def test_p10_m4_api_reads_object_segmentation_report():
    project = case_dir("p10-api") / "workspace"
    report = segment_object_candidates("scan", sample_object_points(), distance_threshold=1.0, min_points=2)
    write_object_segmentation_report(report, project / "reports" / "object_segments" / "scan")

    client = TestClient(create_app(project))
    response = client.get("/segments/scan/objects")

    assert response.status_code == 200
    assert response.json()["asset_id"] == "scan"
    assert response.json()["object_count"] == 2


def test_p10_m5_frontend_has_object_segmentation_panel_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "object-segmentation-panel" in html
    assert "object-segmentation-summary" in html
    assert "fetchObjectSegmentation" in script
    assert "renderObjectSegmentation" in script
    assert "/segments/" in script
    assert "object_count" in script
    assert ".object-segmentation-panel" in css


def test_p10_m6_docs_describe_object_segmentation_modules():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    doc_path = ROOT / "docs" / "phase10-object-segmentation.md"

    assert "P10-M1" in readme
    assert "segment-objects" in readme
    assert "GET /segments/<asset_id>/objects" in readme
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    assert "P10-M1" in doc
    assert "P10-M6" in doc
    assert "object_segments.json" in doc


def test_p10_ex1_cli_segments_workspace_asset_source():
    project = case_dir("p10-ex1-asset") / "workspace"
    source = project / "samples" / "scan.points.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(sample_object_points()), encoding="utf-8")
    asset_dir = project / "data" / "assets" / "scan"
    asset_dir.mkdir(parents=True)
    (asset_dir / "asset.json").write_text(json.dumps({"asset_id": "scan", "file": {"path": str(source)}}), encoding="utf-8")

    exit_code = main([
        "segment-asset-objects",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--distance-threshold",
        "1.0",
        "--min-points",
        "2",
    ])

    output = project / "reports" / "object_segments" / "scan" / "object_segments.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["source_mode"] == "asset_source"
    assert payload["object_count"] == 2


def test_p10_ex2_cli_reads_segmentation_config_file():
    project = case_dir("p10-ex2-config") / "workspace"
    source = project / "samples" / "scan.points.json"
    config = project / "object-segmentation.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(sample_object_points()), encoding="utf-8")
    config.write_text(json.dumps({"distance_threshold": 1.0, "min_points": 2, "max_points": 50}), encoding="utf-8")
    asset_dir = project / "data" / "assets" / "scan"
    asset_dir.mkdir(parents=True)
    (asset_dir / "asset.json").write_text(json.dumps({"asset_id": "scan", "file": {"path": str(source)}}), encoding="utf-8")

    exit_code = main([
        "segment-asset-objects",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--config",
        str(config),
    ])

    payload = json.loads((project / "reports" / "object_segments" / "scan" / "object_segments.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["distance_threshold"] == 1.0
    assert payload["min_points"] == 2
    assert payload["max_points"] == 50


def test_p10_ex3_open3d_adapter_preserves_schema_with_injected_runner():
    def fake_runner(points, distance_threshold, min_points):
        report = segment_object_candidates("scan", points, distance_threshold=distance_threshold, min_points=min_points)
        report["method"] = "open3d_dbscan"
        for item in report["objects"]:
            item["method"] = "open3d_dbscan"
            item["confidence"] = 0.72
        return report

    report = segment_with_open3d_adapter(
        "scan",
        sample_object_points(),
        distance_threshold=1.0,
        min_points=2,
        runner=fake_runner,
    )

    assert report["method"] == "open3d_dbscan"
    assert report["object_count"] == 2
    assert report["objects"][0]["confidence"] == 0.72


def test_p10_ex4_builds_object_segmentation_quality_metrics():
    report = segment_object_candidates("scan", sample_object_points(), distance_threshold=1.0, min_points=2)
    quality = build_object_segmentation_quality(report, max_noise_ratio=0.1, min_object_count=3)

    assert quality["status"] == "review_required"
    assert quality["noise_ratio"] == 0.1667
    assert {finding["code"] for finding in quality["findings"]} == {"high_noise_ratio", "low_object_count"}


def test_p10_ex5_docs_describe_extension_modules():
    doc = (ROOT / "docs" / "phase10-object-segmentation.md").read_text(encoding="utf-8")

    assert "P10-EX1" in doc
    assert "segment-asset-objects" in doc
    assert "Open3D" in doc
    assert "segmentation_quality" in doc

