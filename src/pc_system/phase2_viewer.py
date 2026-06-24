from html import escape
from pathlib import Path

from pc_system.json_io import write_json


def build_phase2_viewer_manifest(
    asset_id: str,
    potree_path: Path | None,
    splat_path: Path | None,
    reports: list[Path],
) -> dict:
    """生成 Phase 2 统一预览入口 manifest。"""

    return {
        "asset_id": asset_id,
        "views": {
            "potree": potree_path.as_posix() if potree_path is not None else None,
            "gaussian_splat": splat_path.as_posix() if splat_path is not None else None,
        },
        "reports": [path.as_posix() for path in reports],
    }


def _render_viewer_html(manifest: dict) -> str:
    """渲染轻量 HTML 入口，真实 viewer 可在后续替换。"""

    potree = manifest["views"].get("potree") or ""
    splat = manifest["views"].get("gaussian_splat") or ""
    reports = "\n".join(f"<li>{escape(str(path))}</li>" for path in manifest["reports"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Phase 2 Viewer</title>
</head>
<body>
  <h1>{escape(str(manifest["asset_id"]))}</h1>
  <section><h2>Potree</h2><p>{escape(str(potree))}</p></section>
  <section><h2>Gaussian Splat</h2><p>{escape(str(splat))}</p></section>
  <section><h2>Reports</h2><ul>{reports}</ul></section>
</body>
</html>
"""


def publish_phase2_viewer(manifest: dict, output_dir: Path) -> dict[str, Path]:
    """写出 Phase 2 viewer manifest 和 HTML 入口。"""

    manifest_path = write_json(manifest, output_dir / "phase2_viewer_manifest.json")
    html_path = output_dir / "phase2_viewer.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(_render_viewer_html(manifest), encoding="utf-8")
    return {"manifest": manifest_path, "html": html_path}
