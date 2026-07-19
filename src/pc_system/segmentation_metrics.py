import math
from collections import defaultdict
from collections.abc import Callable


def _safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return _safe_divide(2 * precision * recall, precision + recall)


def associate_instances(
    golden: dict[int, str],
    predicted: dict[int, str],
    threshold: float,
) -> dict:
    """按点集 IoU 做确定性一对一实例关联。"""

    if not 0 <= threshold <= 1:
        raise ValueError("Instance IoU threshold must be between 0 and 1.")
    golden_sets: dict[str, set[int]] = defaultdict(set)
    predicted_sets: dict[str, set[int]] = defaultdict(set)
    for point_index, instance_id in golden.items():
        golden_sets[str(instance_id)].add(int(point_index))
    for point_index, instance_id in predicted.items():
        predicted_sets[str(instance_id)].add(int(point_index))

    candidates = []
    golden_overlaps: dict[str, set[str]] = defaultdict(set)
    predicted_overlaps: dict[str, set[str]] = defaultdict(set)
    for golden_id, golden_points in golden_sets.items():
        for predicted_id, predicted_points in predicted_sets.items():
            intersection = len(golden_points & predicted_points)
            if not intersection:
                continue
            union = len(golden_points | predicted_points)
            iou = intersection / union
            candidates.append((iou, golden_id, predicted_id))
            golden_overlaps[golden_id].add(predicted_id)
            predicted_overlaps[predicted_id].add(golden_id)

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_golden: set[str] = set()
    used_predicted: set[str] = set()
    matches = []
    for iou, golden_id, predicted_id in candidates:
        if iou < threshold:
            continue
        if golden_id in used_golden or predicted_id in used_predicted:
            continue
        used_golden.add(golden_id)
        used_predicted.add(predicted_id)
        matches.append(
            {
                "golden_instance_id": golden_id,
                "predicted_instance_id": predicted_id,
                "iou": iou,
            }
        )

    return {
        "matches": matches,
        "unmatched_golden_instance_ids": sorted(set(golden_sets) - used_golden),
        "unmatched_predicted_instance_ids": sorted(
            set(predicted_sets) - used_predicted
        ),
        "golden_overlaps": {
            key: sorted(values) for key, values in sorted(golden_overlaps.items())
        },
        "predicted_overlaps": {
            key: sorted(values)
            for key, values in sorted(predicted_overlaps.items())
        },
    }


def build_instance_metrics(
    golden: dict[int, str],
    predicted: dict[int, str],
    iou_threshold: float = 0.5,
) -> dict:
    """计算实例 precision、recall、F1 以及拆分/粘连统计。"""

    association = associate_instances(golden, predicted, iou_threshold)
    true_positive_count = len(association["matches"])
    false_positive_count = len(association["unmatched_predicted_instance_ids"])
    false_negative_count = len(association["unmatched_golden_instance_ids"])
    precision = _safe_divide(
        true_positive_count, true_positive_count + false_positive_count
    )
    recall = _safe_divide(
        true_positive_count, true_positive_count + false_negative_count
    )
    matched_ious = [item["iou"] for item in association["matches"]]
    return {
        "schema_version": "1.0",
        "iou_threshold": iou_threshold,
        "true_positive_count": true_positive_count,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "instance_precision": precision,
        "instance_recall": recall,
        "instance_f1": _f1(precision, recall),
        "mean_matched_instance_iou": (
            sum(matched_ious) / len(matched_ious) if matched_ious else 0.0
        ),
        "over_segmentation_count": sum(
            len(values) > 1 for values in association["golden_overlaps"].values()
        ),
        "under_segmentation_count": sum(
            len(values) > 1
            for values in association["predicted_overlaps"].values()
        ),
        **association,
    }


def _label_key(item: dict) -> str:
    if item.get("is_noise"):
        return "__noise__"
    return str(item["instance_id"])


def build_point_metrics(
    golden: dict[int, dict],
    predicted: dict[int, dict],
    matches: dict,
) -> dict:
    """在匹配实例映射下计算点级实例、类别与噪声指标。"""

    predicted_to_golden = {
        item["predicted_instance_id"]: item["golden_instance_id"]
        for item in matches.get("matches", [])
    }
    golden_targets = {index: _label_key(item) for index, item in golden.items()}
    predicted_targets = {}
    for index in golden:
        item = predicted.get(index)
        if item is None or item.get("is_noise"):
            predicted_targets[index] = "__noise__"
            continue
        predicted_id = str(item["instance_id"])
        predicted_targets[index] = predicted_to_golden.get(
            predicted_id, f"predicted:{predicted_id}"
        )

    golden_instances = sorted(
        {value for value in golden_targets.values() if value != "__noise__"}
    )
    per_instance_iou = {}
    for instance_id in golden_instances:
        golden_points = {
            index for index, value in golden_targets.items() if value == instance_id
        }
        predicted_points = {
            index
            for index, value in predicted_targets.items()
            if value == instance_id
        }
        per_instance_iou[instance_id] = _safe_divide(
            len(golden_points & predicted_points),
            len(golden_points | predicted_points),
        )

    classes = sorted(
        {
            str(item["class_id"])
            for item in golden.values()
            if not item.get("is_noise")
        }
    )
    per_class_iou = {}
    for class_id in classes:
        golden_points = {
            index
            for index, item in golden.items()
            if not item.get("is_noise") and str(item["class_id"]) == class_id
        }
        predicted_points = {
            index
            for index, item in predicted.items()
            if index in golden
            and not item.get("is_noise")
            and str(item.get("class_id", "")) == class_id
        }
        per_class_iou[class_id] = _safe_divide(
            len(golden_points & predicted_points),
            len(golden_points | predicted_points),
        )

    correct = sum(
        golden_targets[index] == predicted_targets[index] for index in golden
    )
    golden_noise = {
        index for index, item in golden.items() if item.get("is_noise")
    }
    predicted_noise = {
        index
        for index in golden
        if index not in predicted or predicted[index].get("is_noise")
    }
    noise_true_positive = len(golden_noise & predicted_noise)
    noise_false_positive = len(predicted_noise - golden_noise)
    noise_false_negative = len(golden_noise - predicted_noise)
    noise_precision = _safe_divide(
        noise_true_positive, noise_true_positive + noise_false_positive
    )
    noise_recall = _safe_divide(
        noise_true_positive, noise_true_positive + noise_false_negative
    )

    return {
        "schema_version": "1.0",
        "labeled_point_count": len(golden),
        "labeled_point_accuracy": _safe_divide(correct, len(golden)),
        "per_instance_iou": per_instance_iou,
        "point_miou": (
            sum(per_instance_iou.values()) / len(per_instance_iou)
            if per_instance_iou
            else 0.0
        ),
        "per_class_iou": per_class_iou,
        "class_miou": (
            sum(per_class_iou.values()) / len(per_class_iou)
            if per_class_iou
            else 0.0
        ),
        "noise_precision": noise_precision,
        "noise_recall": noise_recall,
        "noise_f1": _f1(noise_precision, noise_recall),
    }


def _rotation_matrix(quaternion: list[float]) -> list[list[float]]:
    x, y, z, w = [float(value) for value in quaternion]
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not norm:
        raise ValueError("Box quaternion must be non-zero.")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return [
        [
            1 - 2 * (y * y + z * z),
            2 * (x * y - z * w),
            2 * (x * z + y * w),
        ],
        [
            2 * (x * y + z * w),
            1 - 2 * (x * x + z * z),
            2 * (y * z - x * w),
        ],
        [
            2 * (x * z - y * w),
            2 * (y * z + x * w),
            1 - 2 * (x * x + y * y),
        ],
    ]


def _golden_box_envelope(box: dict) -> dict[str, list[float]]:
    center = [float(value) for value in box["center"]]
    half = [float(value) / 2 for value in box["size"]]
    rotation = _rotation_matrix(box["rotation"])
    corners = []
    for dx in (-half[0], half[0]):
        for dy in (-half[1], half[1]):
            for dz in (-half[2], half[2]):
                local = [dx, dy, dz]
                rotated = [
                    sum(rotation[row][column] * local[column] for column in range(3))
                    for row in range(3)
                ]
                corners.append(
                    [center[axis] + rotated[axis] for axis in range(3)]
                )
    return {
        "min": [min(corner[axis] for corner in corners) for axis in range(3)],
        "max": [max(corner[axis] for corner in corners) for axis in range(3)],
    }


def _aabb_iou(
    first: dict[str, list[float]], second: dict[str, list[float]]
) -> float:
    intersection_size = [
        max(
            0.0,
            min(float(first["max"][axis]), float(second["max"][axis]))
            - max(float(first["min"][axis]), float(second["min"][axis])),
        )
        for axis in range(3)
    ]
    intersection = math.prod(intersection_size)
    first_volume = math.prod(
        float(first["max"][axis]) - float(first["min"][axis])
        for axis in range(3)
    )
    second_volume = math.prod(
        float(second["max"][axis]) - float(second["min"][axis])
        for axis in range(3)
    )
    return _safe_divide(
        intersection, first_volume + second_volume - intersection
    )


def build_bbox_metrics(
    golden_boxes: list[dict],
    predicted_objects: list[dict],
    instance_matches: dict,
    *,
    iou_threshold: float = 0.5,
    runner: Callable[[dict, dict], float] | None = None,
) -> dict:
    """计算匹配实例的包围盒指标并如实记录实际引擎。"""

    if not 0 <= iou_threshold <= 1:
        raise ValueError("Box IoU threshold must be between 0 and 1.")
    golden_by_id = {str(item["instance_id"]): item for item in golden_boxes}
    predicted_by_id = {str(item["object_id"]): item for item in predicted_objects}
    requested_engine = "oriented_3d"
    executed_engine = "oriented_3d" if runner else "axis_aligned_envelope"
    fallback_reason = None if runner else "oriented_runner_unavailable"
    rows = []
    for match in instance_matches.get("matches", []):
        golden_id = str(match["golden_instance_id"])
        predicted_id = str(match["predicted_instance_id"])
        golden_box = golden_by_id.get(golden_id)
        predicted_object = predicted_by_id.get(predicted_id)
        if golden_box is None or predicted_object is None:
            continue
        if runner:
            iou = float(runner(golden_box, predicted_object))
        else:
            iou = _aabb_iou(
                _golden_box_envelope(golden_box), predicted_object["bounds"]
            )
        if not math.isfinite(iou) or not 0 <= iou <= 1:
            raise ValueError("Box IoU engine must return a finite value between 0 and 1.")
        rows.append(
            {
                "golden_instance_id": golden_id,
                "predicted_instance_id": predicted_id,
                "iou": iou,
            }
        )

    true_positive_count = sum(
        row["iou"] >= iou_threshold for row in rows
    )
    false_positive_count = max(0, len(predicted_objects) - true_positive_count)
    false_negative_count = max(0, len(golden_boxes) - true_positive_count)
    precision = _safe_divide(
        true_positive_count, true_positive_count + false_positive_count
    )
    recall = _safe_divide(
        true_positive_count, true_positive_count + false_negative_count
    )
    return {
        "schema_version": "1.0",
        "iou_threshold": iou_threshold,
        "requested_engine": requested_engine,
        "executed_engine": executed_engine,
        "fallback_reason": fallback_reason,
        "per_instance_box_iou": rows,
        "mean_box_iou": (
            sum(row["iou"] for row in rows) / len(rows) if rows else 0.0
        ),
        "true_positive_count": true_positive_count,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "box_precision": precision,
        "box_recall": recall,
        "box_f1": _f1(precision, recall),
    }
