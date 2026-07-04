import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.object_segmentation import (
    segment_object_candidates,
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
