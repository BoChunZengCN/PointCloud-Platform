import json
from pathlib import Path
from uuid import uuid4

from pc_system.asset_registry import build_asset_registry, discover_asset_metadata, write_asset_registry
from pc_system.cli import main


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_asset(asset_dir: Path, asset_id: str, point_count: int = 100) -> None:
    target = asset_dir / asset_id
    target.mkdir(parents=True)
    (target / "asset.json").write_text(
        json.dumps(
            {
                "asset_id": asset_id,
                "file": {"path": f"C:/data/{asset_id}.las", "name": f"{asset_id}.las"},
                "las": {
                    "point_count": point_count,
                    "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
                    "has_rgb": True,
                },
            }
        ),
        encoding="utf-8",
    )


def test_discover_asset_metadata_reads_all_asset_json_files():
    workspace = case_dir("asset-discovery")
    assets_dir = workspace / "data" / "assets"
    write_asset(assets_dir, "scan-a", 10)
    write_asset(assets_dir, "scan-b", 20)

    assets = discover_asset_metadata(assets_dir)

    assert [asset["asset_id"] for asset in assets] == ["scan-a", "scan-b"]


def test_build_asset_registry_summarizes_assets_for_frontend_and_api():
    registry = build_asset_registry([
        {
            "asset_id": "scan-a",
            "file": {"path": "C:/data/scan-a.las", "name": "scan-a.las"},
            "las": {"point_count": 10, "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}, "has_rgb": True},
        }
    ])

    assert registry["schema_version"] == "1.0"
    assert registry["asset_count"] == 1
    assert registry["assets"][0]["asset_id"] == "scan-a"
    assert registry["assets"][0]["point_count"] == 10
    assert registry["assets"][0]["report_paths"]["quality_report"].endswith("quality_report.html")


def test_write_asset_registry_outputs_json_and_markdown():
    workspace = case_dir("asset-registry-write")
    registry = build_asset_registry([
        {
            "asset_id": "scan-a",
            "file": {"path": "C:/data/scan-a.las", "name": "scan-a.las"},
            "las": {"point_count": 10, "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}, "has_rgb": True},
        }
    ])

    outputs = write_asset_registry(registry, workspace)

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert payload["asset_count"] == 1
    assert outputs["json"].name == "asset_index.json"
    assert outputs["markdown"].name == "asset_index.md"
    assert "| scan-a | scan-a.las | 10 |" in markdown


def test_cli_index_assets_writes_project_registry():
    workspace = case_dir("cli-asset-registry")
    project = workspace / "workspace"
    assets_dir = project / "data" / "assets"
    write_asset(assets_dir, "scan-a", 10)
    write_asset(assets_dir, "scan-b", 20)

    exit_code = main(["index-assets", "--project-root", str(project)])

    index_path = assets_dir / "asset_index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["asset_count"] == 2
    assert (assets_dir / "asset_index.md").exists()


def test_build_asset_registry_includes_normalized_viewer_paths():
    registry = build_asset_registry([
        {
            "asset_id": "scan-a",
            "file": {"path": "C:/data/scan-a.las", "name": "scan-a.las"},
            "las": {"point_count": 10, "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]}, "has_rgb": True},
        }
    ])

    viewer_paths = registry["assets"][0]["viewer_paths"]
    assert viewer_paths["viewer_url"].endswith("phase2_viewer.html")
    assert viewer_paths["viewer_html_path"].endswith("phase2_viewer.html")
    assert viewer_paths["manifest_path"].endswith("phase2_viewer_manifest.json")
    assert viewer_paths["report_path"].endswith("production_run_report.md")
