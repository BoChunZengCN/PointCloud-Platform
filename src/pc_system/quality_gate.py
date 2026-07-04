from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


ACTION_BY_CODE = {
    "low_rgb_coverage": "Review RGB colorization before delivery.",
    "high_z_span": "Block delivery until high elevation span is reviewed.",
    "low_density_cells": "Review sparse grid cells before final delivery.",
}


def _status_from_findings(findings: list[dict[str, Any]]) -> tuple[str, str]:
    """根据 finding 严重程度给出门禁状态，critical 优先阻塞。"""

    if any(finding.get("severity") == "critical" for finding in findings):
        return "blocked", "critical"
    if findings:
        return "review_required", "warning"
    return "passed", "info"


def build_quality_gate(asset_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """把点云分析 findings 转成生产可读的质量门禁决策。"""

    findings = list(analysis.get("findings", []))
    status, severity = _status_from_findings(findings)
    actions = [ACTION_BY_CODE.get(finding.get("code"), f"Review finding: {finding.get('code', 'unknown')}") for finding in findings]
    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "status": status,
        "severity": severity,
        "finding_count": len(findings),
        "actions": actions,
        "source_analysis": f"reports/analysis/{asset_id}/point_cloud_analysis.json",
    }


def _markdown(gate: dict[str, Any]) -> str:
    """渲染人工可读的质量门禁摘要。"""

    lines = [
        f"# Quality Gate: {gate['asset_id']}",
        "",
        f"Status: {gate['status']}",
        f"Severity: {gate['severity']}",
        f"Finding count: {gate['finding_count']}",
        "",
        "## Actions",
    ]
    actions = gate.get("actions", [])
    if actions:
        lines.extend(f"- {action}" for action in actions)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_quality_gate_report(gate: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """写出质量门禁 JSON 与 Markdown 报告。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(gate, output_dir / "quality_gate.json")
    markdown_path = output_dir / "quality_gate.md"
    markdown_path.write_text(_markdown(gate), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


