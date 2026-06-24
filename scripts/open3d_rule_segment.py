import json
import math
import sys
from pathlib import Path
from typing import Iterable


def _as_point(value: Iterable[float]) -> list[float]:
    """把输入点规范成三维浮点坐标。"""

    point = [float(item) for item in value]
    if len(point) < 3:
        raise ValueError("Point must contain at least x, y, z")
    return point[:3]


def load_points(source_path: Path) -> list[list[float]]:
    """读取参考脚本支持的点数据。

    第一版支持 JSON 点数组和简单 ASCII PLY；真实工程中 LAS 通常先经 M5 切成 PLY，
    或由后续 Open3D/PDAL 读取器扩展这里的输入格式。
    """

    suffix = source_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        points = payload["points"] if isinstance(payload, dict) else payload
        return [_as_point(point) for point in points]
    if suffix == ".ply":
        return _load_ascii_ply_points(source_path)
    raise ValueError(f"Unsupported source point format for reference script: {source_path.suffix}")


def _load_ascii_ply_points(source_path: Path) -> list[list[float]]:
    """读取只包含 x/y/z 顶点的 ASCII PLY 文件。"""

    lines = source_path.read_text(encoding="utf-8").splitlines()
    vertex_count = None
    data_start = None
    for index, line in enumerate(lines):
        if line.startswith("element vertex "):
            vertex_count = int(line.split()[-1])
        if line.strip() == "end_header":
            data_start = index + 1
            break
    if vertex_count is None or data_start is None:
        raise ValueError("Invalid ASCII PLY header")
    points = []
    for line in lines[data_start : data_start + vertex_count]:
        parts = line.split()
        points.append(_as_point(parts[:3]))
    return points


def build_labels(points: list[list[float]], rules: list[dict]) -> list[dict]:
    """根据规则生成 point-level labels。

    第一版实现 `noise` 的统计离群点识别，其余规则保留稳定标签占位，
    让系统链路能先处理真实 labels JSON。
    """

    labels = ["unclassified" for _ in points]
    for rule in rules:
        if rule.get("label") == "noise" or rule.get("method") == "statistical_outlier":
            for index in _noise_indices(points):
                labels[index] = "noise"
        elif rule.get("label") == "ground" or rule.get("method") == "height_threshold":
            for index in _ground_indices(points):
                if labels[index] == "unclassified":
                    labels[index] = "ground"
        elif rule.get("label") == "cluster" or rule.get("method") == "euclidean_cluster":
            for index, label in enumerate(labels):
                if label == "unclassified":
                    labels[index] = "cluster"
    return [{"point_index": index, "label": label} for index, label in enumerate(labels)]


def _noise_indices(points: list[list[float]]) -> list[int]:
    """用到质心距离的均值和标准差识别明显离群点。"""

    if len(points) < 3:
        return []
    centroid = [sum(point[axis] for point in points) / len(points) for axis in range(3)]
    distances = [_distance(point, centroid) for point in points]
    mean = sum(distances) / len(distances)
    variance = sum((distance - mean) ** 2 for distance in distances) / len(distances)
    threshold = mean + math.sqrt(variance)
    return [index for index, distance in enumerate(distances) if distance > threshold]


def _ground_indices(points: list[list[float]]) -> list[int]:
    """把接近最低高程的一组点标记为 ground。"""

    if not points:
        return []
    z_values = [point[2] for point in points]
    min_z = min(z_values)
    max_z = max(z_values)
    threshold = min_z + max((max_z - min_z) * 0.05, 0.05)
    return [index for index, point in enumerate(points) if point[2] <= threshold]


def _distance(left: list[float], right: list[float]) -> float:
    """计算两个三维点的欧氏距离。"""

    return math.sqrt(sum((left[axis] - right[axis]) ** 2 for axis in range(3)))


def run(config_path: Path) -> Path:
    """执行配置文件描述的分割任务并写出 labels JSON。"""

    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_slice = Path(config["source_slice"])
    labels_path = Path(config["labels_path"])
    points = load_points(source_slice)
    payload = {
        "segmentation_id": config["segmentation_id"],
        "source_slice": config["source_slice"],
        "rules": config.get("rules", []),
        "labels": build_labels(points, config.get("rules", [])),
    }
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return labels_path


def main(argv: list[str] | None = None) -> int:
    """命令行入口：python scripts/open3d_rule_segment.py <config.json>。"""

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: open3d_rule_segment.py <open3d_rule_segmentation_config.json>", file=sys.stderr)
        return 2
    try:
        run(Path(args[0]))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
