import json
import random
from pathlib import Path
from typing import Any


LIGHTWEIGHT_POINT_SUFFIXES = {".json", ".points.json"}


def _normalize_point(point: dict[str, Any]) -> dict[str, Any]:
    """把外部点记录收敛到 Phase 6 分析模型需要的字段类型。"""

    normalized: dict[str, Any] = {
        "x": float(point["x"]),
        "y": float(point["y"]),
        "z": float(point["z"]),
    }
    for channel in ("red", "green", "blue", "classification"):
        if channel in point:
            normalized[channel] = point[channel]
    return normalized


def _sample_json_points(path: Path, max_points: int) -> list[dict[str, Any]]:
    """读取测试和轻量工作流导出的点记录 JSON，并按上限截断。"""

    raw_points = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_points, list):
        raise ValueError("Point sample JSON must be an array of point records.")
    if len(raw_points) <= max_points:
        selected = raw_points
    elif max_points == 1:
        selected = [raw_points[len(raw_points) // 2]]
    else:
        step = (len(raw_points) - 1) / (max_points - 1)
        selected = [raw_points[round(index * step)] for index in range(max_points)]
    return [_normalize_point(point) for point in selected]


def _sample_las_points(path: Path, max_points: int) -> list[dict[str, Any]]:
    """通过可选 laspy 读取真实 LAS/LAZ；未安装时给出明确动作提示。"""

    try:
        import laspy  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Reading LAS/LAZ samples requires optional dependency: python -m pip install laspy") from exc

    points: list[dict[str, Any]] = []
    randomizer = random.Random(0)
    seen = 0
    with laspy.open(path) as reader:
        chunk_size = min(max(max_points, 10_000), 1_000_000)
        for chunk in reader.chunk_iterator(chunk_size):
            for index in range(len(chunk.x)):
                point = {"x": float(chunk.x[index]), "y": float(chunk.y[index]), "z": float(chunk.z[index])}
                if hasattr(chunk, "red") and hasattr(chunk, "green") and hasattr(chunk, "blue"):
                    point.update({"red": int(chunk.red[index]), "green": int(chunk.green[index]), "blue": int(chunk.blue[index])})
                if hasattr(chunk, "classification"):
                    point["classification"] = int(chunk.classification[index])
                seen += 1
                if len(points) < max_points:
                    points.append(point)
                else:
                    replacement = randomizer.randrange(seen)
                    if replacement < max_points:
                        points[replacement] = point
    return points


def sample_points_from_source(path: Path, max_points: int = 10000) -> list[dict[str, Any]]:
    """从轻量 JSON 或真实 LAS/LAZ 中采样 Phase 6 点记录。"""

    if max_points <= 0:
        raise ValueError("max_points must be greater than 0.")
    if not path.exists():
        raise FileNotFoundError(path)
    lower_name = path.name.lower()
    if path.suffix.lower() == ".json" or lower_name.endswith(".points.json"):
        return _sample_json_points(path, max_points)
    if path.suffix.lower() in {".las", ".laz"}:
        return _sample_las_points(path, max_points)
    raise ValueError(f"Unsupported point sample source: {path}")
