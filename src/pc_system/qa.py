from html import escape
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


def _check(status: str, message: str) -> dict[str, str]:
    """生成单个质检项的统一结构。"""

    return {"status": status, "message": message}


def build_quality_report(metadata: dict[str, Any]) -> dict[str, Any]:
    """根据 asset.json 生成阶段一质检结果。

    当前质检只做轻量检查，目标是尽早发现会影响后续处理的基础问题。
    例如：没有 RGB 会影响彩色展示，没有 CRS 会影响坐标系交付。
    """

    las = metadata["las"]
    checks = {
        "point_count": _check(
            "pass" if las.get("point_count", 0) > 0 else "fail",
            f"Point count is {las.get('point_count', 0)}.",
        ),
        "rgb": _check(
            "pass" if las.get("has_rgb") else "warning",
            "RGB fields are present." if las.get("has_rgb") else "RGB fields are missing.",
        ),
        "classification": _check(
            "pass" if las.get("has_classification") else "warning",
            "Classification field is present."
            if las.get("has_classification")
            else "Classification field is missing.",
        ),
        "crs": _check(
            "pass" if las.get("has_crs") else "warning",
            "CRS information is present." if las.get("has_crs") else "CRS information is missing.",
        ),
    }
    # 只要有硬失败项，整体就是 fail；否则存在 warning 时整体为 warning。
    status = "fail" if any(item["status"] == "fail" for item in checks.values()) else "warning"
    if all(item["status"] == "pass" for item in checks.values()):
        status = "pass"
    return {
        "asset_id": metadata["asset_id"],
        "status": status,
        "checks": checks,
        "summary": {
            "file_name": metadata["file"]["name"],
            "point_count": las.get("point_count", 0),
            "bounds": las.get("bounds"),
        },
    }


def write_quality_report(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """同时写出 JSON 和 HTML 质检报告。

    JSON 给后续程序读取，HTML 给人工快速检查。
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(report, output_dir / "quality_report.json")
    html_path = output_dir / "quality_report.html"
    rows = "\n".join(
        f"<tr><td>{escape(str(name))}</td><td>{escape(str(result['status']))}</td>"
        f"<td>{escape(str(result['message']))}</td></tr>"
        for name, result in report["checks"].items()
    )
    html_path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html><head><meta charset=\"utf-8\"><title>Quality Report</title></head><body>",
                f"<h1>Quality Report: {escape(str(report['asset_id']))}</h1>",
                f"<p>Status: {escape(str(report['status']))}</p>",
                "<table><thead><tr><th>Check</th><th>Status</th><th>Message</th></tr></thead>",
                f"<tbody>{rows}</tbody></table>",
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )
    return {"json": json_path, "html": html_path}
