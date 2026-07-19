import json

from pc_system.segmentation_operational_quality import (
    build_operational_quality,
    write_operational_quality,
)


def test_quality_flags_fragmentation_and_fallback_without_claiming_accuracy():
    quality = build_operational_quality(
        report={
            "asset_id": "scan",
            "point_count": 100,
            "noise_point_count": 20,
            "objects": [{"point_count": 70}, {"point_count": 5}, {"point_count": 5}],
        },
        preprocessing={"retention_ratio": 0.75, "findings": []},
        execution={"fallback_reason": "runner_unavailable"},
        thresholds={
            "max_noise_ratio": 0.1,
            "max_largest_object_ratio": 0.6,
            "max_tiny_fragment_ratio": 0.2,
            "tiny_object_points": 10,
        },
    )

    assert quality["evaluation_kind"] == "operational_proxy"
    assert quality["status"] == "review_required"
    assert "accuracy" not in quality
    assert {item["code"] for item in quality["findings"]} >= {
        "high_noise_ratio",
        "suspected_under_segmentation",
        "engine_fallback",
    }


def test_quality_passes_when_proxy_metrics_are_within_thresholds():
    quality = build_operational_quality(
        report={
            "asset_id": "scan",
            "point_count": 100,
            "noise_point_count": 5,
            "objects": [{"point_count": 45}, {"point_count": 45}, {"point_count": 5}],
        },
        preprocessing={"retention_ratio": 0.95, "findings": []},
        execution={"fallback_reason": None},
        thresholds={
            "max_noise_ratio": 0.1,
            "max_largest_object_ratio": 0.6,
            "max_tiny_fragment_ratio": 0.1,
            "tiny_object_points": 10,
        },
    )

    assert quality["status"] == "passed"
    assert quality["findings"] == []


def test_quality_writer_creates_json_and_human_readable_markdown(tmp_path):
    quality = build_operational_quality(
        report={
            "asset_id": "scan",
            "point_count": 10,
            "noise_point_count": 0,
            "objects": [{"point_count": 10}],
        },
        preprocessing={"retention_ratio": 1.0, "findings": []},
        execution={"fallback_reason": None},
        thresholds={"max_largest_object_ratio": 1.0},
    )

    outputs = write_operational_quality(quality, tmp_path)

    assert json.loads(outputs["json"].read_text(encoding="utf-8"))["evaluation_kind"] == "operational_proxy"
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "Operational Proxy" in markdown
    assert "accuracy" not in markdown.lower()
