from pathlib import Path

from pc_system.json_io import write_json


def build_operational_quality(
    *,
    report: dict,
    preprocessing: dict,
    execution: dict,
    thresholds: dict,
) -> dict:
    """构建无黄金标注时的运行质量代理指标。"""

    point_count = int(report.get("point_count", 0) or 0)
    noise_count = int(report.get("noise_point_count", 0) or 0)
    object_sizes = [int(item.get("point_count", 0) or 0) for item in report.get("objects", [])]
    tiny_object_points = int(thresholds.get("tiny_object_points", 10))
    noise_ratio = noise_count / point_count if point_count else 0.0
    largest_object_ratio = max(object_sizes, default=0) / point_count if point_count else 0.0
    tiny_points = (
        sum(size for size in object_sizes if size < tiny_object_points)
        if len(object_sizes) > 1
        else 0
    )
    tiny_fragment_ratio = tiny_points / point_count if point_count else 0.0
    findings = [dict(item) for item in preprocessing.get("findings", [])]

    if noise_ratio > float(thresholds.get("max_noise_ratio", 0.2)):
        findings.append(
            {
                "code": "high_noise_ratio",
                "severity": "warning",
                "message": "Segmentation noise ratio is above the operational threshold.",
            }
        )
    if largest_object_ratio > float(thresholds.get("max_largest_object_ratio", 0.9)):
        findings.append(
            {
                "code": "suspected_under_segmentation",
                "severity": "warning",
                "message": "The largest object may contain multiple connected objects.",
            }
        )
    if tiny_fragment_ratio > float(thresholds.get("max_tiny_fragment_ratio", 0.2)):
        findings.append(
            {
                "code": "suspected_over_segmentation",
                "severity": "warning",
                "message": "Too many points belong to tiny object fragments.",
            }
        )
    if execution.get("fallback_reason"):
        findings.append(
            {
                "code": "engine_fallback",
                "severity": "warning",
                "message": "The requested engine was unavailable and an explicit fallback was used.",
            }
        )

    return {
        "schema_version": "1.0",
        "asset_id": report.get("asset_id", ""),
        "evaluation_kind": "operational_proxy",
        "status": "review_required" if findings else "passed",
        "metrics": {
            "noise_ratio": round(noise_ratio, 4),
            "largest_object_ratio": round(largest_object_ratio, 4),
            "tiny_fragment_ratio": round(tiny_fragment_ratio, 4),
            "retention_ratio": float(preprocessing.get("retention_ratio", 1.0)),
        },
        "findings": findings,
    }


def _markdown(quality: dict) -> str:
    lines = [
        f"# Segmentation Operational Proxy: {quality.get('asset_id', '')}",
        "",
        f"Status: {quality['status']}",
        "",
        "## Metrics",
    ]
    for name, value in quality.get("metrics", {}).items():
        lines.append(f"- {name}: {value}")
    lines.extend(["", "## Findings"])
    findings = quality.get("findings", [])
    if findings:
        for finding in findings:
            lines.append(f"- {finding['code']}: {finding['message']}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_operational_quality(quality: dict, output_dir: Path) -> dict[str, Path]:
    """写出运行质量代理 JSON 和 Markdown。"""

    json_path = write_json(quality, output_dir / "segmentation_quality.json")
    markdown_path = output_dir / "segmentation_quality.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_markdown(quality), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
