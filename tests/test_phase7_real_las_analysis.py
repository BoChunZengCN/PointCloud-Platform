import json
from pathlib import Path
from uuid import uuid4


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_points(path: Path, count: int = 5) -> list[dict]:
    points = [
        {"x": float(index), "y": float(index % 2), "z": float(index * 2), "red": index, "green": index + 1, "blue": index + 2, "classification": index % 3}
        for index in range(count)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(points), encoding="utf-8")
    return points


def test_p7_m1_samples_lightweight_las_points_with_limit():
    from pc_system.las_sampling import sample_points_from_source

    sample_path = case_dir("p7-sampling") / "scan.points.json"
    write_points(sample_path, count=5)

    points = sample_points_from_source(sample_path, max_points=3)

    assert len(points) == 3
    assert points[0] == {"x": 0.0, "y": 0.0, "z": 0.0, "red": 0, "green": 1, "blue": 2, "classification": 0}
    assert points[-1]["z"] == 4.0


def test_p7_m2_cli_analyzes_existing_asset_source():
    from pc_system.cli import main

    project = case_dir("p7-analyze-asset") / "workspace"
    sample_path = project / "data" / "raw" / "scan.points.json"
    write_points(sample_path, count=4)
    asset_dir = project / "data" / "assets" / "scan"
    asset_dir.mkdir(parents=True)
    (asset_dir / "asset.json").write_text(
        json.dumps({"asset_id": "scan", "file": {"path": str(sample_path), "name": sample_path.name}, "las": {"point_count": 4}}),
        encoding="utf-8",
    )

    exit_code = main([
        "analyze-asset",
        "--project-root",
        str(project),
        "--asset-id",
        "scan",
        "--max-points",
        "2",
        "--grid-cell-size",
        "5",
    ])

    report_path = project / "reports" / "analysis" / "scan" / "point_cloud_analysis.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["asset_id"] == "scan"
    assert report["point_count"] == 2
    assert report["rgb_coverage"] == 1.0


def test_p7_m3_asset_registry_includes_analysis_status_when_report_exists():
    from pc_system.asset_registry import build_asset_registry

    project = case_dir("p7-registry-analysis") / "workspace"
    report_path = project / "reports" / "analysis" / "scan" / "point_cloud_analysis.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({"asset_id": "scan", "point_count": 3, "rgb_coverage": 1.0}), encoding="utf-8")
    metadata = {"asset_id": "scan", "file": {"path": "scan.las", "name": "scan.las"}, "las": {"point_count": 3}}

    registry = build_asset_registry([metadata], project_root=project)

    asset = registry["assets"][0]
    assert asset["analysis_status"] == "ready"
    assert asset["analysis_report_path"] == "reports/analysis/scan/point_cloud_analysis.json"
    assert asset["report_paths"]["analysis_report"] == "reports/analysis/scan/point_cloud_analysis.md"


def test_p7_m4_api_lists_analysis_overview():
    from fastapi.testclient import TestClient

    from pc_system.api import create_app

    project = case_dir("p7-api-analysis-overview") / "workspace"
    report_dir = project / "reports" / "analysis" / "scan"
    report_dir.mkdir(parents=True)
    (report_dir / "point_cloud_analysis.json").write_text(
        json.dumps({"asset_id": "scan", "point_count": 8, "rgb_coverage": 0.75, "findings": [{"code": "low_rgb_coverage"}]}),
        encoding="utf-8",
    )

    response = TestClient(create_app(project)).get("/analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_count"] == 1
    assert payload["analyses"][0] == {"asset_id": "scan", "point_count": 8, "rgb_coverage": 0.75, "finding_count": 1}


def test_p7_m5_frontend_has_analysis_overview_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    css = (root / "frontend" / "app.css").read_text(encoding="utf-8")

    assert "analysis-overview-panel" in html
    assert "fetchAnalysisOverview" in script
    assert "renderAnalysisOverview" in script
    assert "${API_BASE_URL}/analysis" in script
    assert "analysis_status" in script
    assert ".analysis-overview-panel" in css


def test_p7_m6_docs_describe_real_las_analysis_modules():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    doc_path = root / "docs" / "phase7-real-las-analysis.md"

    assert "P7-M1" in readme
    assert "P7-M6" in readme
    assert "analyze-asset" in readme
    assert "GET /analysis" in readme
    assert doc_path.exists()
    doc = doc_path.read_text(encoding="utf-8")
    assert "P7-M1" in doc
    assert "P7-M6" in doc
    assert "analysis_status" in doc
