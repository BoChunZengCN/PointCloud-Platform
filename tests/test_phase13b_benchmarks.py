import json
from pathlib import Path

import pytest

from pc_system.segmentation_benchmarks import (
    BenchmarkValidationError,
    import_benchmark,
    load_benchmark_sample,
)


def label_document() -> dict:
    return {
        "schema_version": "1.0",
        "point_labels": [
            {
                "point_index": 0,
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "instance_id": "pipe-1",
                "class_id": "pipe",
                "is_noise": False,
            },
            {
                "point_index": 1,
                "x": 0.1,
                "y": 0.0,
                "z": 0.0,
                "instance_id": "pipe-1",
                "class_id": "pipe",
                "is_noise": False,
            },
        ],
        "boxes": [
            {
                "instance_id": "pipe-1",
                "class_id": "pipe",
                "center": [0.05, 0.0, 0.0],
                "size": [0.1, 0.1, 0.1],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        ],
    }


def write_benchmark_source(
    root: Path,
    *,
    labels_format: str = "json",
    labels: dict | None = None,
    labels_path: str | None = None,
) -> Path:
    source = root / "source-benchmark"
    source.mkdir()
    labels = labels or label_document()
    relative_labels = labels_path or f"labels/scan.{labels_format}"
    label_path = source / relative_labels
    label_path.parent.mkdir(parents=True, exist_ok=True)
    if labels_format == "json":
        label_path.write_text(json.dumps(labels), encoding="utf-8")
    else:
        records = [
            {"record_type": "point_label", **item} for item in labels["point_labels"]
        ] + [{"record_type": "box", **item} for item in labels["boxes"]]
        label_path.write_text(
            "\n".join(json.dumps(item) for item in records) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "schema_version": "1.0",
        "benchmark_id": "bench-001",
        "benchmark_version": "v1",
        "split": "golden_regression",
        "scene_type": "pipe-rack",
        "point_density": 120.0,
        "coordinate_unit": "m",
        "label_version": "labels-v1",
        "license": "internal",
        "samples": [
            {
                "sample_id": "sample-001",
                "asset_id": "scan",
                "asset_version": "v1",
                "source_uri": "scan.points.json",
                "source_fingerprint": "abc123",
                "labels_path": relative_labels,
                "labels_format": labels_format,
            }
        ],
    }
    manifest_path = source / "benchmark.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_imports_json_benchmark_into_versioned_workspace(tmp_path):
    manifest_path = write_benchmark_source(tmp_path)

    imported = import_benchmark(tmp_path / "project", manifest_path)
    manifest, labels = load_benchmark_sample(
        tmp_path / "project", "bench-001", "sample-001"
    )

    assert imported["benchmark_id"] == "bench-001"
    assert manifest["split"] == "golden_regression"
    assert labels["point_labels"][0]["instance_id"] == "pipe-1"
    assert labels["boxes"][0]["rotation"] == [0.0, 0.0, 0.0, 1.0]
    assert (
        tmp_path
        / "project"
        / "benchmarks"
        / "bench-001"
        / "samples"
        / "sample-001"
        / "labels.json"
    ).is_file()


def test_imports_jsonl_point_and_box_records(tmp_path):
    manifest_path = write_benchmark_source(tmp_path, labels_format="jsonl")

    import_benchmark(tmp_path / "project", manifest_path)
    _, labels = load_benchmark_sample(
        tmp_path / "project", "bench-001", "sample-001"
    )

    assert len(labels["point_labels"]) == 2
    assert len(labels["boxes"]) == 1
    assert "record_type" not in labels["point_labels"][0]


def test_rejects_duplicate_point_indices(tmp_path):
    labels = label_document()
    labels["point_labels"][1]["point_index"] = 0
    manifest_path = write_benchmark_source(tmp_path, labels=labels)

    with pytest.raises(BenchmarkValidationError) as exc_info:
        import_benchmark(tmp_path / "project", manifest_path)

    assert exc_info.value.code == "duplicate_point_index"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("size", [1.0, 0.0, 1.0], "invalid_box_size"),
        ("rotation", [0.0, 0.0, 0.0, 0.0], "invalid_box_rotation"),
    ],
)
def test_rejects_invalid_box_geometry(tmp_path, field, value, code):
    labels = label_document()
    labels["boxes"][0][field] = value
    manifest_path = write_benchmark_source(tmp_path, labels=labels)

    with pytest.raises(BenchmarkValidationError) as exc_info:
        import_benchmark(tmp_path / "project", manifest_path)

    assert exc_info.value.code == code


def test_rejects_missing_box_instance_reference(tmp_path):
    labels = label_document()
    labels["point_labels"][0]["instance_id"] = "missing"
    manifest_path = write_benchmark_source(tmp_path, labels=labels)

    with pytest.raises(BenchmarkValidationError) as exc_info:
        import_benchmark(tmp_path / "project", manifest_path)

    assert exc_info.value.code == "missing_instance_box"


def test_rejects_labels_path_outside_manifest_directory(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(label_document()), encoding="utf-8")
    manifest_path = write_benchmark_source(
        tmp_path, labels_path="../outside.json"
    )

    with pytest.raises(BenchmarkValidationError) as exc_info:
        import_benchmark(tmp_path / "project", manifest_path)

    assert exc_info.value.code == "unsafe_labels_path"


def test_refuses_to_overwrite_existing_benchmark(tmp_path):
    manifest_path = write_benchmark_source(tmp_path)
    import_benchmark(tmp_path / "project", manifest_path)

    with pytest.raises(BenchmarkValidationError) as exc_info:
        import_benchmark(tmp_path / "project", manifest_path)

    assert exc_info.value.code == "benchmark_exists"

