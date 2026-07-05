from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


STATUS_ORDER = {"passed": 0, "review_required": 1, "missing": 1, "blocked": 2}


def _asset_status(asset_id: str, gates: dict[str, dict[str, Any]]) -> str:
    """读取单资产 gate 状态，缺失时按 missing 处理。"""

    return gates.get(asset_id, {}).get("status", "missing")


def build_project_gate(registry: dict[str, Any], gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """汇总多个资产的质量门禁，生成项目级最严重状态。"""

    assets = []
    summary = {"passed": 0, "review_required": 0, "blocked": 0, "missing": 0}
    for asset in registry.get("assets", []):
        asset_id = asset.get("asset_id", "")
        status = _asset_status(asset_id, gates)
        if status not in summary:
            status = "missing"
        summary[status] += 1
        assets.append({"asset_id": asset_id, "status": status})
    worst = "passed"
    for status in summary:
        if summary[status] and STATUS_ORDER[status] > STATUS_ORDER[worst]:
            worst = status
    return {
        "schema_version": "1.0",
        "module": "Project Gate",
        "status": worst,
        "asset_count": len(assets),
        "status_summary": summary,
        "assets": assets,
    }


def _markdown(gate: dict[str, Any]) -> str:
    """渲染项目级门禁 Markdown 摘要。"""

    lines = ["# Project Gate", "", f"Status: {gate['status']}", f"Assets: {gate['asset_count']}", "", "| Asset | Status |", "| --- | --- |"]
    for asset in gate.get("assets", []):
        lines.append(f"| {asset['asset_id']} | {asset['status']} |")
    return "\n".join(lines) + "\n"


def write_project_gate_report(gate: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """写出项目级门禁 JSON 与 Markdown 报告。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(gate, output_dir / "project_gate.json")
    markdown_path = output_dir / "project_gate.md"
    markdown_path.write_text(_markdown(gate), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
