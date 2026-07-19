import math

from pc_system.segmentation_provenance import fingerprint_points


class CorrespondenceError(ValueError):
    """带稳定错误码和可选诊断报告的点对应错误。"""

    def __init__(self, code: str, message: str, report: dict | None = None):
        super().__init__(message)
        self.code = code
        self.report = report


def _base_report(
    mode: str,
    label_count: int,
    matched_count: int,
    unmatched_count: int,
    ambiguous_count: int,
    tolerance: float | None,
) -> dict:
    return {
        "schema_version": "1.0",
        "mode": mode,
        "label_count": label_count,
        "matched_count": matched_count,
        "matched_ratio": round(
            matched_count / label_count if label_count else 1.0, 6
        ),
        "unmatched_count": unmatched_count,
        "ambiguous_count": ambiguous_count,
        "tolerance": tolerance,
    }


def _strict_match(
    labels: list[dict],
    source_points: list[dict],
    expected_fingerprint: str,
) -> tuple[list[dict], dict]:
    actual_fingerprint = fingerprint_points(source_points)
    if actual_fingerprint != expected_fingerprint:
        raise CorrespondenceError(
            "source_fingerprint_mismatch",
            "Source point fingerprint does not match the golden benchmark sample.",
        )
    matched = []
    for label in labels:
        try:
            point_index = int(label["point_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CorrespondenceError(
                "invalid_point_index", "Golden point label requires an integer point_index."
            ) from exc
        if point_index < 0 or point_index >= len(source_points):
            raise CorrespondenceError(
                "point_index_out_of_range",
                f"Golden point_index is outside the source point range: {point_index}",
            )
        matched.append({**label, "source_point_index": point_index})
    return matched, _base_report(
        "strict_index", len(labels), len(matched), 0, 0, None
    )


def _coordinate_match(
    labels: list[dict],
    source_points: list[dict],
    tolerance: float,
    min_coverage: float,
) -> tuple[list[dict], dict]:
    source_xyz = []
    for point in source_points:
        try:
            xyz = tuple(float(point[axis]) for axis in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError) as exc:
            raise CorrespondenceError(
                "invalid_source_coordinates", "Source points require finite x, y, and z."
            ) from exc
        if not all(math.isfinite(value) for value in xyz):
            raise CorrespondenceError(
                "invalid_source_coordinates", "Source points require finite x, y, and z."
            )
        source_xyz.append(xyz)

    tolerance_squared = tolerance * tolerance
    matched = []
    unmatched_count = 0
    ambiguous_count = 0
    for label in labels:
        try:
            target = tuple(float(label[axis]) for axis in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError) as exc:
            raise CorrespondenceError(
                "invalid_label_coordinates",
                "Coordinate correspondence requires finite label x, y, and z.",
            ) from exc
        if not all(math.isfinite(value) for value in target):
            raise CorrespondenceError(
                "invalid_label_coordinates",
                "Coordinate correspondence requires finite label x, y, and z.",
            )
        candidates = []
        for index, point in enumerate(source_xyz):
            distance_squared = sum(
                (target[axis] - point[axis]) ** 2 for axis in range(3)
            )
            if distance_squared <= tolerance_squared:
                candidates.append(index)
        if not candidates:
            unmatched_count += 1
        elif len(candidates) > 1:
            ambiguous_count += 1
        else:
            matched.append({**label, "source_point_index": candidates[0]})

    report = _base_report(
        "coordinate_tolerance",
        len(labels),
        len(matched),
        unmatched_count,
        ambiguous_count,
        tolerance,
    )
    if report["matched_ratio"] < min_coverage:
        raise CorrespondenceError(
            "insufficient_match_coverage",
            (
                "Coordinate match coverage is below the configured minimum: "
                f"{report['matched_ratio']} < {min_coverage}"
            ),
            report,
        )
    return matched, report


def match_point_labels(
    labels: list[dict],
    source_points: list[dict],
    *,
    expected_fingerprint: str,
    mode: str = "strict_index",
    tolerance: float | None = None,
    min_coverage: float = 1.0,
) -> tuple[list[dict], dict]:
    """把黄金点标签稳定对应到评估源点。"""

    min_coverage = float(min_coverage)
    if not math.isfinite(min_coverage) or not 0 <= min_coverage <= 1:
        raise CorrespondenceError(
            "invalid_min_coverage", "min_coverage must be between 0 and 1."
        )
    if mode == "strict_index":
        return _strict_match(labels, source_points, expected_fingerprint)
    if mode != "coordinate_tolerance":
        raise CorrespondenceError(
            "unsupported_correspondence_mode",
            f"Unsupported correspondence mode: {mode}",
        )
    if tolerance is None:
        raise CorrespondenceError(
            "invalid_tolerance",
            "Coordinate correspondence requires a positive finite tolerance.",
        )
    tolerance = float(tolerance)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise CorrespondenceError(
            "invalid_tolerance",
            "Coordinate correspondence requires a positive finite tolerance.",
        )
    return _coordinate_match(labels, source_points, tolerance, min_coverage)
