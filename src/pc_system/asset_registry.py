import json
from pathlib import Path

from pc_system.json_io import write_json


SCHEMA_VERSION = "1.0"


def discover_asset_metadata(assets_dir: Path) -> list[dict]:
    """扫描资产目录，读取每个资产的 asset.json。"""

    if not assets_dir.exists():
        return []
    assets = []
    for asset_path in sorted(assets_dir.iterdir(), key=lambda path: path.name):
        metadata_path = asset_path / "asset.json"
        if metadata_path.is_file():
            assets.append(json.loads(metadata_path.read_text(encoding="utf-8")))
    return assets


def _asset_entry(metadata: dict) -> dict:
    """把完整 asset.json 压缩成前端/API 常用的索引条目。"""

    asset_id = metadata["asset_id"]
    file_info = metadata.get("file", {})
    las_info = metadata.get("las", {})
    return {
        "asset_id": asset_id,
        "file_name": file_info.get("name", ""),
        "source_path": file_info.get("path", ""),
        "point_count": las_info.get("point_count", 0),
        "bounds": las_info.get("bounds", {}),
        "has_rgb": las_info.get("has_rgb", metadata.get("has_rgb", False)),
        "metadata_path": f"data/assets/{asset_id}/asset.json",
        "preview_paths": {
            "preview_manifest": f"previews/{asset_id}/preview_manifest.json",
            "potree_manifest": f"previews/{asset_id}/potree_manifest.json",
            "phase2_viewer": f"previews/{asset_id}/phase2_viewer_manifest.json",
        },
        "viewer_paths": {
            "viewer_url": f"previews/{asset_id}/phase2_viewer.html",
            "viewer_html_path": f"previews/{asset_id}/phase2_viewer.html",
            "manifest_path": f"previews/{asset_id}/phase2_viewer_manifest.json",
            "potree_manifest_path": f"previews/{asset_id}/potree_manifest.json",
            "report_path": f"reports/production_runs/{asset_id}/production_run_report.md",
        },
        "report_paths": {
            "quality_report": f"reports/{asset_id}/quality_report.html",
            "production_plan": f"reports/production_runs/{asset_id}/production_run_plan.md",
            "production_report": f"reports/production_runs/{asset_id}/production_run_report.md",
        },
    }


def build_asset_registry(asset_metadata: list[dict]) -> dict:
    """构建项目级资产索引，供前端工作台和 API 读取。"""

    entries = [_asset_entry(metadata) for metadata in sorted(asset_metadata, key=lambda item: item["asset_id"])]
    return {
        "schema_version": SCHEMA_VERSION,
        "asset_count": len(entries),
        "assets": entries,
    }


def _render_markdown(registry: dict) -> str:
    """把资产索引渲染为 Markdown。"""

    lines = [
        "# Asset Registry",
        "",
        f"Asset count: {registry['asset_count']}",
        "",
        "| Asset | File | Points |",
        "| --- | --- | --- |",
    ]
    for asset in registry["assets"]:
        lines.append(f"| {asset['asset_id']} | {asset['file_name']} | {asset['point_count']} |")
    return "\n".join(lines) + "\n"


def write_asset_registry(registry: dict, output_dir: Path) -> dict[str, Path]:
    """写出资产索引 JSON 和 Markdown。"""

    json_path = write_json(registry, output_dir / "asset_index.json")
    markdown_path = output_dir / "asset_index.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(registry), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}

