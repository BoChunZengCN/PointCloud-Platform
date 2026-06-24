from html import escape
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


def publish_preview(metadata: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """生成阶段一预览资产。

    目前先输出 manifest 和 HTML 占位页。后续接入 COPC/Potree 转换器后，
    可以继续在这个目录下放切片结果，并保持外部调用接口不变。
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "asset_id": metadata["asset_id"],
        "source_file": metadata["file"],
        "point_count": metadata["las"].get("point_count", 0),
        "has_rgb": metadata["las"].get("has_rgb", False),
        "has_classification": metadata["las"].get("has_classification", False),
        # manifest_only 表示还没有真正生成 COPC/Potree 切片。
        "preview_status": "manifest_only",
        "next_converter": "COPC or Potree converter",
    }
    manifest_path = write_json(manifest, output_dir / "preview_manifest.json")
    html_path = output_dir / "index.html"
    html_path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html><head><meta charset=\"utf-8\"><title>Point Cloud Preview</title></head><body>",
                f"<h1>Open {escape(str(metadata['file']['name']))}</h1>",
                f"<p>Asset ID: {escape(str(metadata['asset_id']))}</p>",
                f"<p>Point count: {escape(str(metadata['las'].get('point_count', 0)))}</p>",
                "<p>Phase 1 preview manifest generated. COPC/Potree conversion can attach here.</p>",
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    return {"manifest": manifest_path, "html": html_path}
