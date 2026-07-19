from pc_system.object_segmentation import segment_object_candidates


class SegmentationEngineUnavailable(RuntimeError):
    """请求的分割引擎没有可用执行器。"""


def execute_engine(
    asset_id: str,
    points: list[dict],
    config: dict,
    runners: dict | None = None,
    include_membership: bool = False,
) -> tuple[dict, dict]:
    """执行指定引擎，并准确记录实际执行者和回退原因。"""

    requested = str(config.get("engine", "builtin_geometric"))
    runners = runners or {}
    distance_threshold = float(config.get("distance_threshold", 1.0))
    min_points = int(config.get("min_points", 10))

    if requested == "builtin_geometric":
        report = segment_object_candidates(
            asset_id,
            points,
            distance_threshold=distance_threshold,
            min_points=min_points,
            include_membership=include_membership,
        )
        executed = "builtin_geometric"
        fallback_reason = None
    elif requested in runners:
        report = runners[requested](asset_id, points, config)
        executed = requested
        fallback_reason = None
    elif bool(config.get("allow_fallback", False)):
        report = segment_object_candidates(
            asset_id,
            points,
            distance_threshold=distance_threshold,
            min_points=min_points,
            include_membership=include_membership,
        )
        executed = "builtin_geometric"
        fallback_reason = "runner_unavailable"
    else:
        raise SegmentationEngineUnavailable(f"Segmentation engine is unavailable: {requested}")

    report["asset_id"] = asset_id
    report["method"] = executed
    for item in report.get("objects", []):
        item["method"] = executed
    return report, {
        "requested_engine": requested,
        "executed_engine": executed,
        "fallback_reason": fallback_reason,
    }
