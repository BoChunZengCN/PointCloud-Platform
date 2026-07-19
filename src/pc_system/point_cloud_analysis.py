import json
import math
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


def _bounds(points: list[dict]) -> dict[str, list[float]]:
    """计算点记录的 XYZ 包围盒。"""

    xs = [float(point["x"]) for point in points]
    ys = [float(point["y"]) for point in points]
    zs = [float(point["z"]) for point in points]
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def _area(bounds: dict[str, list[float]]) -> float:
    """按 XY 包围盒估算水平面积，避免点数很小时除零。"""

    width = max(bounds["max"][0] - bounds["min"][0], 1.0)
    depth = max(bounds["max"][1] - bounds["min"][1], 1.0)
    return width * depth


def _rgb_coverage(points: list[dict]) -> float:
    """统计同时具备 RGB 三通道的点占比。"""

    if not points:
        return 0.0
    rgb_count = sum(1 for point in points if {"red", "green", "blue"}.issubset(point))
    return round(rgb_count / len(points), 4)


def _classification_distribution(points: list[dict]) -> dict[str, int]:
    """统计 LAS classification 字段分布，键转成字符串便于 JSON/API 使用。"""

    distribution: dict[str, int] = {}
    for point in points:
        key = str(point.get("classification", "unclassified"))
        distribution[key] = distribution.get(key, 0) + 1
    return distribution


def build_spatial_grid(points: list[dict], cell_size: float = 5.0) -> dict[str, Any]:
    """按 XY 网格聚合点数与高程范围。"""

    if not math.isfinite(cell_size) or cell_size <= 0:
        raise ValueError("grid_cell_size must be a finite number greater than 0.")
    cells: dict[str, dict[str, Any]] = {}
    for point in points:
        cell_x = math.floor(float(point["x"]) / cell_size)
        cell_y = math.floor(float(point["y"]) / cell_size)
        key = f"{cell_x},{cell_y}"
        z = float(point["z"])
        cell = cells.setdefault(key, {"point_count": 0, "z_min": z, "z_max": z})
        cell["point_count"] += 1
        cell["z_min"] = min(cell["z_min"], z)
        cell["z_max"] = max(cell["z_max"], z)
    return {"cell_size": float(cell_size), "cell_count": len(cells), "cells": cells}


def analyze_point_records(asset_id: str, points: list[dict], grid_cell_size: float = 5.0) -> dict[str, Any]:
    """从轻量点记录生成 Phase 6 点云分析报告。"""

    if not math.isfinite(grid_cell_size) or grid_cell_size <= 0:
        raise ValueError("grid_cell_size must be a finite number greater than 0.")
    if not points:
        return {
            "schema_version": "1.0",
            "asset_id": asset_id,
            "point_count": 0,
            "bounds": {"min": [], "max": []},
            "density": {"area_square_meter": 0.0, "points_per_square_meter": 0.0},
            "rgb_coverage": 0.0,
            "classification_distribution": {},
            "grid": {"cell_size": float(grid_cell_size), "cell_count": 0, "cells": {}},
            "findings": [],
        }
    bounds = _bounds(points)
    area = _area(bounds)
    analysis = {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "point_count": len(points),
        "bounds": bounds,
        "density": {
            "area_square_meter": round(area, 4),
            "points_per_square_meter": round(len(points) / area, 4),
        },
        "rgb_coverage": _rgb_coverage(points),
        "classification_distribution": _classification_distribution(points),
        "grid": build_spatial_grid(points, grid_cell_size),
    }
    analysis["findings"] = build_quality_findings(analysis)
    return analysis


def build_quality_findings(
    analysis: dict[str, Any],
    min_rgb_coverage: float = 0.8,
    max_z_span: float = 50.0,
    min_points_per_cell: int = 2,
) -> list[dict[str, str]]:
    """基于分析结果生成质量异常列表。"""

    findings: list[dict[str, str]] = []
    if analysis.get("rgb_coverage", 0.0) < min_rgb_coverage:
        findings.append({"code": "low_rgb_coverage", "severity": "warning", "message": "RGB coverage is below threshold."})
    bounds = analysis.get("bounds", {})
    if bounds.get("min") and bounds.get("max"):
        z_span = float(bounds["max"][2]) - float(bounds["min"][2])
        if z_span > max_z_span:
            findings.append({"code": "high_z_span", "severity": "critical", "message": "Z span is above threshold."})
    low_cells = [key for key, cell in analysis.get("grid", {}).get("cells", {}).items() if cell.get("point_count", 0) < min_points_per_cell]
    if low_cells:
        findings.append({"code": "low_density_cells", "severity": "warning", "message": f"{len(low_cells)} grid cells are below density threshold."})
    return findings


def _markdown(analysis: dict[str, Any]) -> str:
    """生成点云分析 Markdown 摘要。"""

    lines = [
        f"# Point Cloud Analysis: {analysis['asset_id']}",
        "",
        f"Point count: {analysis['point_count']}",
        f"RGB coverage: {analysis['rgb_coverage']}",
        f"Grid cells: {analysis['grid']['cell_count']}",
        "",
        "## Findings",
    ]
    for finding in analysis.get("findings", []):
        lines.append(f"- {finding['severity']}: {finding['code']} - {finding['message']}")
    if not analysis.get("findings"):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_point_cloud_analysis(analysis: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """写出点云分析 JSON 与 Markdown 报告。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(analysis, output_dir / "point_cloud_analysis.json")
    markdown_path = output_dir / "point_cloud_analysis.md"
    markdown_path.write_text(_markdown(analysis), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def load_points_json(path: Path) -> list[dict]:
    """读取测试/轻量分析使用的点记录 JSON。"""

    return json.loads(path.read_text(encoding="utf-8"))

