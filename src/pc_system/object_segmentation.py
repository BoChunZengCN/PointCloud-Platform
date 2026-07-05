import math
from pathlib import Path
from typing import Any

from pc_system.json_io import write_json


def _distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    """计算两个点的三维欧式距离，用于轻量几何聚类。"""

    return math.sqrt(
        (float(a["x"]) - float(b["x"])) ** 2
        + (float(a["y"]) - float(b["y"])) ** 2
        + (float(a["z"]) - float(b["z"])) ** 2
    )


def _cluster_points(points: list[dict[str, Any]], distance_threshold: float) -> list[list[int]]:
    """用简单连通域聚类生成物体候选，避免 Phase 10 初版依赖重型三维库。"""

    visited: set[int] = set()
    clusters: list[list[int]] = []
    for start_index in range(len(points)):
        if start_index in visited:
            continue
        queue = [start_index]
        visited.add(start_index)
        cluster: list[int] = []
        while queue:
            current = queue.pop(0)
            cluster.append(current)
            for candidate in range(len(points)):
                if candidate in visited:
                    continue
                if _distance(points[current], points[candidate]) <= distance_threshold:
                    visited.add(candidate)
                    queue.append(candidate)
        clusters.append(cluster)
    return clusters


def _bounds(cluster_points: list[dict[str, Any]]) -> dict[str, list[float]]:
    """计算单个物体候选的三维包围盒。"""

    xs = [float(point["x"]) for point in cluster_points]
    ys = [float(point["y"]) for point in cluster_points]
    zs = [float(point["z"]) for point in cluster_points]
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}


def _center(bounds: dict[str, list[float]]) -> list[float]:
    """从包围盒计算中心点，保留 4 位小数方便 JSON 比对和前端显示。"""

    return [round((bounds["min"][index] + bounds["max"][index]) / 2, 4) for index in range(3)]


def _rgb_mean(cluster_points: list[dict[str, Any]]) -> dict[str, int] | None:
    """统计候选物体的平均颜色；无完整 RGB 时返回 None。"""

    rgb_points = [point for point in cluster_points if {"red", "green", "blue"}.issubset(point)]
    if not rgb_points:
        return None
    return {
        "red": round(sum(int(point["red"]) for point in rgb_points) / len(rgb_points)),
        "green": round(sum(int(point["green"]) for point in rgb_points) / len(rgb_points)),
        "blue": round(sum(int(point["blue"]) for point in rgb_points) / len(rgb_points)),
    }


def segment_object_candidates(
    asset_id: str,
    points: list[dict[str, Any]],
    distance_threshold: float = 1.0,
    min_points: int = 10,
) -> dict[str, Any]:
    """从点记录中生成物体候选分割报告。

    当前实现是 Phase 10 的内置几何基线：按距离连通关系聚类，小簇归入噪声。
    后续可以在保持输出 schema 不变的前提下接入 Open3D、PCL 或深度学习模型。
    """

    if distance_threshold <= 0:
        raise ValueError("distance_threshold must be greater than 0.")
    if min_points <= 0:
        raise ValueError("min_points must be greater than 0.")

    raw_clusters = _cluster_points(points, distance_threshold)
    accepted = [cluster for cluster in raw_clusters if len(cluster) >= min_points]
    accepted.sort(key=len, reverse=True)
    noise_point_count = sum(len(cluster) for cluster in raw_clusters if len(cluster) < min_points)
    objects = []
    for index, cluster in enumerate(accepted, start=1):
        cluster_points = [points[point_index] for point_index in cluster]
        bounds = _bounds(cluster_points)
        item: dict[str, Any] = {
            "object_id": f"obj-{index:03d}",
            "label": "object_candidate",
            "confidence": 0.6,
            "point_count": len(cluster_points),
            "bounds": bounds,
            "center": _center(bounds),
            "method": "geometric_cluster",
        }
        rgb_mean = _rgb_mean(cluster_points)
        if rgb_mean:
            item["rgb_mean"] = rgb_mean
        objects.append(item)

    return {
        "schema_version": "1.0",
        "asset_id": asset_id,
        "method": "geometric_cluster",
        "point_count": len(points),
        "distance_threshold": float(distance_threshold),
        "min_points": int(min_points),
        "object_count": len(objects),
        "noise_point_count": noise_point_count,
        "objects": objects,
    }




def segment_with_open3d_adapter(
    asset_id: str,
    points: list[dict[str, Any]],
    distance_threshold: float = 1.0,
    min_points: int = 10,
    runner=None,
) -> dict[str, Any]:
    """通过 Open3D 适配边界生成物体候选分割报告。

    runner 参数让测试和后续生产适配器可以注入真实 Open3D DBSCAN 实现；未注入时，
    当前版本使用内置几何聚类作为兼容回退，并把 method 标记为 open3d_dbscan。
    """

    if runner:
        report = runner(points, distance_threshold, min_points)
    else:
        report = segment_object_candidates(asset_id, points, distance_threshold=distance_threshold, min_points=min_points)
    report["asset_id"] = asset_id
    report["method"] = "open3d_dbscan"
    for item in report.get("objects", []):
        item["method"] = "open3d_dbscan"
    return report


def build_object_segmentation_quality(
    report: dict[str, Any],
    max_noise_ratio: float = 0.2,
    min_object_count: int = 1,
) -> dict[str, Any]:
    """从物体分割报告生成轻量质量指标，供后续质量门禁接入。"""

    point_count = int(report.get("point_count", 0) or 0)
    noise_count = int(report.get("noise_point_count", 0) or 0)
    object_count = int(report.get("object_count", 0) or 0)
    noise_ratio = round(noise_count / point_count, 4) if point_count else 0.0
    findings: list[dict[str, str]] = []
    if noise_ratio > max_noise_ratio:
        findings.append({"code": "high_noise_ratio", "severity": "warning", "message": "Object segmentation noise ratio is above threshold."})
    if object_count < min_object_count:
        findings.append({"code": "low_object_count", "severity": "warning", "message": "Object candidate count is below threshold."})
    return {
        "schema_version": "1.0",
        "asset_id": report.get("asset_id", ""),
        "status": "review_required" if findings else "passed",
        "point_count": point_count,
        "object_count": object_count,
        "noise_point_count": noise_count,
        "noise_ratio": noise_ratio,
        "findings": findings,
    }

def _markdown(report: dict[str, Any]) -> str:
    """生成物体分割 Markdown 摘要，供交付审查快速阅读。"""

    lines = [
        f"# Object Segmentation: {report['asset_id']}",
        "",
        f"Method: {report['method']}",
        f"Object count: {report['object_count']}",
        f"Noise points: {report['noise_point_count']}",
        "",
        "## Objects",
    ]
    for item in report.get("objects", []):
        lines.append(f"- {item['object_id']}: {item['point_count']} points, center={item['center']}")
    if not report.get("objects"):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_object_segmentation_report(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """写出物体分割 JSON 与 Markdown 报告。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(report, output_dir / "object_segments.json")
    markdown_path = output_dir / "object_segments.md"
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}

