from pathlib import Path
from uuid import uuid4

from pc_system.asset import LasAssetInfo, build_asset_metadata, write_asset_metadata
from pc_system.config import ProjectConfig
from pc_system.preview import publish_preview
from pc_system.qa import build_quality_report, write_quality_report


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_project_config_creates_standard_directories():
    project = case_dir("project-config") / "demo_project"
    config = ProjectConfig(project_root=project)

    created = config.ensure_directories()

    assert created == {
        "data_raw": project / "data" / "raw",
        "assets": project / "data" / "assets",
        "reports": project / "reports",
        "previews": project / "previews",
        "logs": project / "logs",
    }
    assert all(path.is_dir() for path in created.values())


def test_asset_metadata_contains_las_fields():
    workspace = case_dir("asset-metadata")
    las_path = workspace / "scan.las"
    las_path.write_bytes(b"LAS placeholder")
    info = LasAssetInfo(
        point_count=1200,
        bounds={"min": [1.0, 2.0, 3.0], "max": [10.0, 20.0, 30.0]},
        has_rgb=True,
        has_classification=True,
        has_crs=False,
        scale=[0.001, 0.001, 0.001],
        offset=[100.0, 200.0, 300.0],
        point_format="3",
    )

    metadata = build_asset_metadata(las_path, info)
    output = write_asset_metadata(metadata, workspace / "asset.json")

    assert output.exists()
    assert metadata["file"]["name"] == "scan.las"
    assert metadata["las"]["point_count"] == 1200
    assert metadata["las"]["has_rgb"] is True
    assert metadata["las"]["has_classification"] is True
    assert metadata["las"]["has_crs"] is False


def test_quality_report_flags_missing_crs():
    workspace = case_dir("quality-report")
    metadata = {
        "asset_id": "scan",
        "file": {"path": str(workspace / "scan.las"), "name": "scan.las"},
        "las": {
            "point_count": 1200,
            "bounds": {"min": [1.0, 2.0, 3.0], "max": [10.0, 20.0, 30.0]},
            "has_rgb": True,
            "has_classification": True,
            "has_crs": False,
        },
    }

    report = build_quality_report(metadata)
    paths = write_quality_report(report, workspace)

    assert report["status"] == "warning"
    assert report["checks"]["crs"]["status"] == "warning"
    assert "CRS" in report["checks"]["crs"]["message"]
    assert paths["json"].exists()
    assert paths["html"].exists()


def test_preview_publish_writes_manifest_and_html():
    workspace = case_dir("preview")
    metadata = {
        "asset_id": "scan",
        "file": {"path": str(workspace / "scan.las"), "name": "scan.las"},
        "las": {
            "point_count": 1200,
            "has_rgb": True,
            "has_classification": True,
        },
    }

    result = publish_preview(metadata, workspace / "preview")

    assert result["manifest"].exists()
    assert result["html"].exists()
    assert "Open scan.las" in result["html"].read_text(encoding="utf-8")
