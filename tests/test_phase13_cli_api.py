import json
from pathlib import Path

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.segmentation_service import run_segmentation


ROOT = Path(__file__).resolve().parents[1]


def sample_points():
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.0, "z": 0.0},
    ]


def write_asset(project: Path) -> None:
    source = project / "samples" / "scan.points.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(sample_points()), encoding="utf-8")
    asset_dir = project / "data" / "assets" / "scan"
    asset_dir.mkdir(parents=True)
    (asset_dir / "asset.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "asset_id": "scan",
                "asset_version": "v1",
                "file": {"path": str(source)},
            }
        ),
        encoding="utf-8",
    )


def test_run_segmentation_cli_writes_versioned_run(tmp_path):
    write_asset(tmp_path)

    exit_code = main(
        [
            "run-segmentation",
            "--project-root",
            str(tmp_path),
            "--asset-id",
            "scan",
            "--run-id",
            "seg-run-001",
            "--engine",
            "builtin_geometric",
            "--distance-threshold",
            "0.2",
            "--min-points",
            "1",
        ]
    )

    run_path = (
        tmp_path
        / "reports"
        / "segmentation_runs"
        / "scan"
        / "seg-run-001"
        / "segmentation_run.json"
    )
    assert exit_code == 0
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "completed"


def test_segmentation_run_read_apis_list_detail_and_quality(tmp_path):
    run_segmentation(
        tmp_path,
        asset_id="scan",
        asset_version="v1",
        source_uri="scan.points.json",
        points=sample_points(),
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 0.2,
            "min_points": 1,
        },
        run_id="seg-run-001",
    )
    client = TestClient(create_app(tmp_path))

    listing = client.get("/segmentation-runs/scan")
    detail = client.get("/segmentation-runs/scan/seg-run-001")
    quality = client.get("/segmentation-runs/scan/seg-run-001/quality")

    assert listing.status_code == 200
    assert listing.json()["run_count"] == 1
    assert listing.json()["runs"][0]["run_id"] == "seg-run-001"
    assert detail.json()["executed_engine"] == "builtin_geometric"
    assert quality.json()["evaluation_kind"] == "operational_proxy"


def test_segmentation_run_api_validates_identifiers_and_missing_runs(tmp_path):
    client = TestClient(create_app(tmp_path))

    assert client.get("/segmentation-runs/bad$id").status_code == 400
    assert client.get("/segmentation-runs/scan/missing").status_code == 404
    assert client.get("/segmentation-runs/scan/bad$id").status_code == 400


def test_phase13_frontend_and_docs_use_operational_proxy_language():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "app.css").read_text(encoding="utf-8")
    doc = ROOT / "docs" / "phase13-segmentation-foundation.md"

    assert "segmentation-run-panel" in html
    assert "segmentation-run-summary" in html
    assert "fetchSegmentationRuns" in script
    assert "renderSegmentationRun" in script
    assert "运行质量代理指标" in script
    assert ".segmentation-run-panel" in css
    assert doc.exists()
    assert "requested_engine" in doc.read_text(encoding="utf-8")
