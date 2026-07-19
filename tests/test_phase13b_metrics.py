import math

import pytest

from pc_system.segmentation_metrics import (
    associate_instances,
    build_bbox_metrics,
    build_instance_metrics,
    build_point_metrics,
)


def test_perfect_point_and_instance_metrics_equal_one():
    golden_instances = {0: "gold-a", 1: "gold-a", 2: "gold-b"}
    predicted_instances = {0: "pred-a", 1: "pred-a", 2: "pred-b"}
    matches = associate_instances(golden_instances, predicted_instances, 0.5)
    golden = {
        0: {"instance_id": "gold-a", "class_id": "pipe", "is_noise": False},
        1: {"instance_id": "gold-a", "class_id": "pipe", "is_noise": False},
        2: {"instance_id": "gold-b", "class_id": "valve", "is_noise": False},
    }
    predicted = {
        0: {"instance_id": "pred-a", "class_id": "pipe", "is_noise": False},
        1: {"instance_id": "pred-a", "class_id": "pipe", "is_noise": False},
        2: {"instance_id": "pred-b", "class_id": "valve", "is_noise": False},
    }

    point_metrics = build_point_metrics(golden, predicted, matches)
    instance_metrics = build_instance_metrics(
        golden_instances, predicted_instances, iou_threshold=0.5
    )

    assert point_metrics["point_miou"] == 1.0
    assert point_metrics["class_miou"] == 1.0
    assert point_metrics["labeled_point_accuracy"] == 1.0
    assert instance_metrics["instance_precision"] == 1.0
    assert instance_metrics["instance_recall"] == 1.0
    assert instance_metrics["instance_f1"] == 1.0


def test_instance_metrics_count_false_positive_and_false_negative():
    metrics = build_instance_metrics(
        {0: "gold-a", 1: "gold-a", 2: "gold-b"},
        {0: "pred-a", 1: "pred-a", 3: "pred-extra"},
        iou_threshold=0.5,
    )

    assert metrics["true_positive_count"] == 1
    assert metrics["false_positive_count"] == 1
    assert metrics["false_negative_count"] == 1
    assert metrics["instance_precision"] == 0.5
    assert metrics["instance_recall"] == 0.5
    assert metrics["instance_f1"] == 0.5


def test_instance_metrics_detect_over_segmentation():
    metrics = build_instance_metrics(
        {0: "gold-a", 1: "gold-a", 2: "gold-a", 3: "gold-a"},
        {0: "pred-a", 1: "pred-a", 2: "pred-b", 3: "pred-b"},
        iou_threshold=0.5,
    )

    assert metrics["over_segmentation_count"] == 1
    assert metrics["under_segmentation_count"] == 0


def test_instance_metrics_detect_under_segmentation():
    metrics = build_instance_metrics(
        {0: "gold-a", 1: "gold-a", 2: "gold-b", 3: "gold-b"},
        {0: "pred-a", 1: "pred-a", 2: "pred-a", 3: "pred-a"},
        iou_threshold=0.5,
    )

    assert metrics["under_segmentation_count"] == 1
    assert metrics["over_segmentation_count"] == 0


def test_point_metrics_report_noise_precision_recall_and_f1():
    golden = {
        0: {"instance_id": "gold-a", "class_id": "pipe", "is_noise": False},
        1: {"instance_id": "noise", "class_id": "noise", "is_noise": True},
        2: {"instance_id": "noise", "class_id": "noise", "is_noise": True},
    }
    predicted = {
        0: {"instance_id": "pred-a", "class_id": "pipe", "is_noise": False},
        1: {"instance_id": "noise", "class_id": "noise", "is_noise": True},
        2: {"instance_id": "pred-extra", "class_id": "pipe", "is_noise": False},
    }
    matches = associate_instances({0: "gold-a"}, {0: "pred-a", 2: "pred-extra"}, 0.5)

    metrics = build_point_metrics(golden, predicted, matches)

    assert metrics["noise_precision"] == 1.0
    assert metrics["noise_recall"] == 0.5
    assert metrics["noise_f1"] == pytest.approx(2 / 3)


def test_axis_aligned_box_iou_known_overlap():
    golden_boxes = [
        {
            "instance_id": "gold-a",
            "class_id": "pipe",
            "center": [1.0, 1.0, 1.0],
            "size": [2.0, 2.0, 2.0],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        }
    ]
    predicted_objects = [
        {
            "object_id": "pred-a",
            "bounds": {"min": [1.0, 0.0, 0.0], "max": [3.0, 2.0, 2.0]},
        }
    ]
    instance_matches = {
        "matches": [
            {
                "golden_instance_id": "gold-a",
                "predicted_instance_id": "pred-a",
                "iou": 1.0,
            }
        ]
    }

    metrics = build_bbox_metrics(
        golden_boxes, predicted_objects, instance_matches, iou_threshold=0.3
    )

    assert metrics["mean_box_iou"] == pytest.approx(1 / 3)
    assert metrics["box_f1"] == 1.0
    assert metrics["requested_engine"] == "oriented_3d"
    assert metrics["executed_engine"] == "axis_aligned_envelope"
    assert metrics["fallback_reason"] == "oriented_runner_unavailable"


def test_rotated_box_fallback_is_truthfully_labeled():
    half_turn = math.sqrt(0.5)
    metrics = build_bbox_metrics(
        [
            {
                "instance_id": "gold-a",
                "class_id": "pipe",
                "center": [0.0, 0.0, 0.0],
                "size": [2.0, 1.0, 1.0],
                "rotation": [0.0, 0.0, half_turn, half_turn],
            }
        ],
        [
            {
                "object_id": "pred-a",
                "bounds": {
                    "min": [-0.5, -1.0, -0.5],
                    "max": [0.5, 1.0, 0.5],
                },
            }
        ],
        {
            "matches": [
                {
                    "golden_instance_id": "gold-a",
                    "predicted_instance_id": "pred-a",
                    "iou": 1.0,
                }
            ]
        },
    )

    assert metrics["mean_box_iou"] == pytest.approx(1.0)
    assert metrics["executed_engine"] == "axis_aligned_envelope"


def test_injected_oriented_runner_is_recorded_as_executed():
    def oriented_runner(golden_box, predicted_object):
        return 0.75

    metrics = build_bbox_metrics(
        [
            {
                "instance_id": "gold-a",
                "class_id": "pipe",
                "center": [0.0, 0.0, 0.0],
                "size": [1.0, 1.0, 1.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        ],
        [
            {
                "object_id": "pred-a",
                "bounds": {
                    "min": [-0.5, -0.5, -0.5],
                    "max": [0.5, 0.5, 0.5],
                },
            }
        ],
        {
            "matches": [
                {
                    "golden_instance_id": "gold-a",
                    "predicted_instance_id": "pred-a",
                    "iou": 1.0,
                }
            ]
        },
        runner=oriented_runner,
    )

    assert metrics["mean_box_iou"] == 0.75
    assert metrics["executed_engine"] == "oriented_3d"
    assert metrics["fallback_reason"] is None


def test_box_metrics_include_missing_golden_boxes_in_mean_and_findings():
    golden_boxes = [
        {
            "instance_id": instance_id,
            "class_id": "pipe",
            "center": center,
            "size": [2.0, 2.0, 2.0],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        }
        for instance_id, center in (
            ("gold-a", [0.0, 0.0, 0.0]),
            ("gold-b", [10.0, 0.0, 0.0]),
        )
    ]
    predicted_objects = [
        {
            "object_id": "pred-a",
            "bounds": {
                "min": [-1.0, -1.0, -1.0],
                "max": [1.0, 1.0, 1.0],
            },
        }
    ]

    metrics = build_bbox_metrics(
        golden_boxes,
        predicted_objects,
        {"matches": []},
        iou_threshold=0.5,
    )

    assert metrics["mean_box_iou"] == pytest.approx(0.5)
    assert metrics["true_positive_count"] == 1
    assert metrics["false_negative_count"] == 1
    assert metrics["box_recall"] == 0.5
    assert metrics["missing_golden_instance_ids"] == ["gold-b"]
    assert metrics["extra_predicted_object_ids"] == []
