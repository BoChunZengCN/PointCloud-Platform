import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pc_system.json_io import write_json


@dataclass(frozen=True)
class DeliverySource:
    """交付包中的一个候选产物。"""

    name: str
    path: str


def _asset_or_raise(registry: dict, asset_id: str) -> dict:
    """从 asset registry 中查找资产；缺失时抛出清晰错误。"""

    for asset in registry.get("assets", []):
        if asset.get("asset_id") == asset_id:
            return asset
    raise KeyError(f"Asset not found in registry: {asset_id}")


def _delivery_sources(asset: dict) -> list[DeliverySource]:
    """按交付清单顺序收集该资产可能需要交付的文件。"""

    viewer_paths = asset.get("viewer_paths", {})
    report_paths = asset.get("report_paths", {})
    candidates = [
        DeliverySource("asset_metadata", asset.get("metadata_path", "")),
        DeliverySource(
            "phase2_viewer_html",
            viewer_paths.get("viewer_url") or viewer_paths.get("viewer_html_path", ""),
        ),
        DeliverySource("phase2_viewer_manifest", viewer_paths.get("manifest_path", "")),
        DeliverySource("potree_manifest", viewer_paths.get("potree_manifest_path", "")),
        DeliverySource("production_plan", report_paths.get("production_plan", "")),
        DeliverySource(
            "production_report",
            report_paths.get("production_report") or viewer_paths.get("report_path", ""),
        ),
        DeliverySource("quality_report", report_paths.get("quality_report", "")),
    ]
    return [source for source in candidates if source.path]


def _safe_relative_path(project_root: Path, relative_path: str) -> Path | None:
    """解析交付源路径，拒绝跳出项目目录的相对路径。"""

    root = project_root.resolve()
    candidate = (project_root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def _item(project_root: Path, source: DeliverySource) -> dict:
    """生成 manifest 单项，记录源文件是否存在以及交付包内路径。"""

    source_path = _safe_relative_path(project_root, source.path)
    exists = bool(source_path and source_path.exists())
    return {
        "name": source.name,
        "source_path": source.path,
        "delivery_path": f"files/{source.path}" if exists else "",
        "status": "ready" if exists else "missing",
    }


def build_delivery_package(project_root: Path, registry: dict, asset_id: str, gate_decision: dict | None = None) -> dict:
    """构建交付包 manifest，不复制文件。

    该函数只负责计算交付清单，便于测试和前端/API 复用。
    """

    asset = _asset_or_raise(registry, asset_id)
    items = [_item(project_root, source) for source in _delivery_sources(asset)]
    ready_count = sum(1 for item in items if item["status"] == "ready")
    missing_count = sum(1 for item in items if item["status"] == "missing")
    status = "ready" if items and missing_count == 0 else "partial"
    package = {
        "schema_version": "1.0",
        "module": "Delivery Package Export",
        "asset_id": asset_id,
        "status": status,
        "summary": {
            "total": len(items),
            "ready": ready_count,
            "missing": missing_count,
        },
        "items": items,
    }
    if gate_decision is not None:
        package["delivery_gate_decision"] = gate_decision
    return package


def _copy_ready_files(project_root: Path, package: dict, output_dir: Path) -> None:
    """把 ready 文件复制到 delivery/files 下，保留相对目录结构。"""

    for item in package["items"]:
        if item["status"] != "ready":
            continue
        source = _safe_relative_path(project_root, item["source_path"])
        if source is None:
            continue
        target = output_dir / item["delivery_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _render_markdown(package: dict) -> str:
    """把交付包 manifest 渲染为 Markdown，方便人工交接检查。"""

    lines = [
        "# Delivery Package",
        "",
        f"Asset: {package['asset_id']}",
        f"Status: {package['status']}",
        "",
        "| Item | Status | Source |",
        "| --- | --- | --- |",
    ]
    for item in package["items"]:
        lines.append(f"| {item['name']} | {item['status']} | {item['source_path']} |")
    return "\n".join(lines) + "\n"


def _write_zip_archive(output_dir: Path, zip_path: Path) -> Path:
    """把交付目录压缩为 zip，归档内路径保持相对交付目录。"""

    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir).as_posix())
    return zip_path


def export_delivery_package(
    project_root: Path,
    registry: dict,
    asset_id: str,
    output_dir: Path,
    make_zip: bool = False,
    gate_decision: dict | None = None,
) -> dict[str, Path]:
    """复制交付文件，并按需写出 zip 归档。"""

    package = build_delivery_package(project_root, registry, asset_id, gate_decision=gate_decision)
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_ready_files(project_root, package, output_dir)
    json_path = write_json(package, output_dir / "delivery_manifest.json")
    markdown_path = output_dir / "delivery_manifest.md"
    markdown_path.write_text(_render_markdown(package), encoding="utf-8")
    outputs = {"root": output_dir, "json": json_path, "markdown": markdown_path}
    if make_zip:
        outputs["zip"] = _write_zip_archive(output_dir, output_dir.with_suffix(".zip"))
    return outputs


