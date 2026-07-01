import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.point_cloud_analysis import (
    analyze_point_records,
    build_quality_findings,
    build_spatial_grid,
    write_point_cloud_analysis,
)


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def sample_points() -> list[dict]:
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0, "red": 10, "green": 20, "blue": 30, "classification": 2},
        {"x": 1.0, "y": 0.0, "z": 0.5, "red": 12, "green": 22, "blue": 32, "classification": 2},
        {"x": 3.0, "y": 3.0, "z": 8.0, "classification": 6},
        {"x": 8.0, "y": 8.0, "z": 80.0, "red": 1, "green": 1, "blue": 1, "classification": 7},
    ]


def test_p6_m1_analyzes_point_records_for_density_rgb_and_classification():
    analysis = analyze_point_records("scan", sample_points())

    assert analysis["asset_id"] == "scan"
    assert analysis["point_count"] == 4
    assert analysis["rgb_coverage"] == 0.75
    assert analysis["classification_distribution"] == {"2": 2, "6": 1, "7": 1}
    assert analysis["bounds"]["max"] == [8.0, 8.0, 80.0]
    assert analysis["density"]["points_per_square_meter"] > 0


def test_p6_m2_builds_spatial_grid_statistics():
    grid = build_spatial_grid(sample_points(), cell_size=5.0)

    assert grid["cell_size"] == 5.0
    assert grid["cell_count"] == 2
    assert grid["cells"]["0,0"]["point_count"] == 3
    assert grid["cells"]["1,1"]["z_max"] == 80.0


def test_p6_m3_detects_quality_findings_from_analysis():
    analysis = analyze_point_records("scan", sample_points(), grid_cell_size=5.0)
    findings = build_quality_findings(analysis, min_rgb_coverage=0.9, max_z_span=20.0, min_points_per_cell=4)

    assert {finding["code"] for finding in findings} == {"low_rgb_coverage", "high_z_span", "low_density_cells"}
    assert all(finding["severity"] in {"warning", "critical"} for finding in findings)


def test_p6_m4_cli_writes_point_cloud_analysis_from_sample_points():
    project = case_dir("p6-cli-analysis") / "workspace"
    points_path = project / "samples" / "scan_points.json"
    points_path.parent.mkdir(parents=True)
    points_path.write_text(json.dumps(sample_points()), encoding="utf-8")

    exit_code = main([
        "analyze-point-cloud",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--points-json",
        str(points_path),
        "--grid-cell-size",
        "5",
    ])

    output = project / "reports" / "analysis" / "scan" / "point_cloud_analysis.json"
    assert exit_code == 0
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["grid"]["cell_count"] == 2


def test_p6_m5_api_reads_point_cloud_analysis_report():
    project = case_dir("p6-api-analysis") / "workspace"
    analysis = analyze_point_records("scan", sample_points(), grid_cell_size=5.0)
    write_point_cloud_analysis(analysis, project / "reports" / "analysis" / "scan")

    client = TestClient(create_app(project))
    response = client.get("/analysis/scan")

    assert response.status_code == 200
    assert response.json()["asset_id"] == "scan"
    assert response.json()["grid"]["cell_count"] == 2


def test_p6_m6_frontend_has_quality_insight_panel_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")

    assert "quality-insight-panel" in html
    assert "fetchPointCloudAnalysis" in script
    assert "renderQualityInsights" in script
    assert "/analysis/" in script
    assert "rgb_coverage" in script
    assert ".quality-insight-panel" in css


def test_p6_docs_describe_point_cloud_analysis_modules():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    doc_path = ROOT / "docs" / "phase6-point-cloud-analysis.md"

    assert "P6-M1" in readme
    assert "analyze-point-cloud" in readme
    assert "GET /analysis/<asset_id>" in readme
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    assert "P6-M1" in doc
    assert "P6-M6" in doc
    assert "rgb_coverage" in doc

