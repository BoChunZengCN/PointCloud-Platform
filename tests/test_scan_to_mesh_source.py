import hashlib
import json

import pytest

from phase15e_support import prepared_scan, review_request
from pc_system.reference_review import review_reference_version
from pc_system.scan_to_mesh_errors import ScanToMeshError
from pc_system.scan_to_mesh_source import freeze_mesh_source
from pc_system.segmentation_review_evidence import build_object_review_evidence


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _phase14_release(root):
    assignments = [
        {"source_point_index": 0, "x": 0.0, "y": 0.0, "z": 0.0, "instance_id": "pipe-1", "class_id": "pipe", "is_noise": False, "origin": "human_correction"},
        {"source_point_index": 1, "x": 2.0, "y": 0.0, "z": 0.0, "instance_id": "pipe-1", "class_id": "pipe", "is_noise": False, "origin": "human_correction"},
        {"source_point_index": 2, "x": 0.0, "y": 2.0, "z": 0.0, "instance_id": "pipe-1", "class_id": "pipe", "is_noise": False, "origin": "human_correction"},
        {"source_point_index": 3, "x": 8.0, "y": 8.0, "z": 8.0, "instance_id": "valve-1", "class_id": "valve", "is_noise": False, "origin": "automatic_segmentation"},
        {"source_point_index": 4, "x": 99.0, "y": 99.0, "z": 99.0, "instance_id": "noise-1", "class_id": "noise", "is_noise": True, "origin": "human_correction"},
    ]
    objects = {
        "objects": [
            {"instance_id": "pipe-1", "class_id": "pipe"},
            {"instance_id": "valve-1", "class_id": "valve"},
        ]
    }
    source_fingerprint = "a" * 64
    evidence = build_object_review_evidence(
        asset_id="plant-scan",
        release_id="release-1",
        source_fingerprint=source_fingerprint,
        draft={
            "assignments": assignments,
            "confirmed_instance_ids": ["pipe-1"],
        },
        objects=objects,
        active_events=[
            {
                "operation": {"type": "confirm", "instance_ids": ["pipe-1"]},
                "resulting_revision": 4,
                "client_request_id": "confirm-pipe-1",
            }
        ],
    )
    directory = (
        root
        / "reports"
        / "segmentation_correction_releases"
        / "plant-scan"
        / "release-1"
    )
    directory.mkdir(parents=True)
    release = {
        "schema_version": "1.0",
        "release_id": "release-1",
        "asset_id": "plant-scan",
        "status": "published",
        "source_fingerprint": source_fingerprint,
        "artifacts": {
            "labels": "labels.json",
            "provenance": "provenance.json",
            "object_review_evidence": "object_review_evidence.json",
        },
    }
    labels = {
        "schema_version": "1.0",
        "point_labels": [
            {
                "point_index": item["source_point_index"],
                **{key: item[key] for key in ("x", "y", "z", "instance_id", "class_id", "is_noise", "origin")},
            }
            for item in assignments
        ],
        "boxes": [],
    }
    provenance = {
        "schema_version": "1.0",
        "asset_id": "plant-scan",
        "source_fingerprint": source_fingerprint,
        "reviewer": "expert-a",
        "published_at": "2026-10-16T00:00:00Z",
    }
    for name, value in (
        ("correction_release.json", release),
        ("labels.json", labels),
        ("provenance.json", provenance),
        ("object_review_evidence.json", evidence),
    ):
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
    return directory


def test_phase14_snapshot_contains_only_requested_reviewed_object(tmp_path):
    _phase14_release(tmp_path)

    snapshot = freeze_mesh_source(
        tmp_path,
        source_kind="phase14_correction_release",
        source_ref={
            "asset_id": "plant-scan",
            "release_id": "release-1",
            "instance_id": "pipe-1",
        },
    )

    assert snapshot["schema_version"] == "1.0"
    assert snapshot["coordinate_unit"] == "m"
    assert snapshot["source_identity"] == {
        "asset_id": "plant-scan",
        "release_id": "release-1",
        "instance_id": "pipe-1",
        "class_id": "pipe",
    }
    assert snapshot["points"] == [
        [-1.0, -1.0, 0.0],
        [1.0, -1.0, 0.0],
        [-1.0, 1.0, 0.0],
    ]
    assert snapshot["point_count"] == 3
    assert snapshot["bounds_m"] == {
        "min": [-1.0, -1.0, 0.0],
        "max": [1.0, 1.0, 0.0],
    }
    assert snapshot["source_to_local_4x4"] == [
        [1.0, 0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0, -1.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    assert snapshot["source_evidence"]["review_state"] == "confirmed"
    assert snapshot["fingerprint"] == _canonical_digest(
        {key: value for key, value in snapshot.items() if key != "fingerprint"}
    )


@pytest.mark.parametrize(
    ("case", "instance_id"),
    [
        ("missing", "missing-1"),
        ("noise", "noise-1"),
        ("unreviewed", "valve-1"),
        ("empty", "pipe-1"),
        ("mixed", "pipe-1"),
    ],
)
def test_phase14_snapshot_rejects_missing_empty_noise_or_mixed_object(
    tmp_path, case, instance_id
):
    directory = _phase14_release(tmp_path)
    if case in {"empty", "mixed"}:
        labels_path = directory / "labels.json"
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        selected = [
            item
            for item in labels["point_labels"]
            if item["instance_id"] == "pipe-1"
        ]
        changed = selected if case == "empty" else selected[:1]
        for item in changed:
            item["instance_id"] = "valve-1"
        labels_path.write_text(json.dumps(labels), encoding="utf-8")

    with pytest.raises(ScanToMeshError) as caught:
        freeze_mesh_source(
            tmp_path,
            source_kind="phase14_correction_release",
            source_ref={
                "asset_id": "plant-scan",
                "release_id": "release-1",
                "instance_id": instance_id,
            },
        )

    assert caught.value.code in {
        "mesh_source_object_invalid",
        "mesh_source_not_reviewed",
        "mesh_source_integrity_error",
    }


def test_phase15e_snapshot_requires_completed_approved_review(tmp_path):
    prepared_scan(tmp_path)

    with pytest.raises(ScanToMeshError) as caught:
        freeze_mesh_source(
            tmp_path,
            source_kind="phase15e_scanned_reference",
            source_ref={"model_id": "scan-pump", "version_id": "v1"},
        )

    assert caught.value.code == "mesh_source_not_reviewed"
    assert not (tmp_path / "scan_to_cad").exists()


def test_snapshot_is_deterministic_and_meter_based(tmp_path):
    prepared_scan(tmp_path)
    review_reference_version(tmp_path, **review_request())

    first = freeze_mesh_source(
        tmp_path,
        source_kind="phase15e_scanned_reference",
        source_ref={"model_id": "scan-pump", "version_id": "v1"},
    )
    second = freeze_mesh_source(
        tmp_path,
        source_kind="phase15e_scanned_reference",
        source_ref={"model_id": "scan-pump", "version_id": "v1"},
    )

    assert second == first
    assert first["coordinate_unit"] == "m"
    assert first["point_count"] == 64
    assert first["source_identity"] == {
        "model_id": "scan-pump",
        "version_id": "v1",
    }
    assert first["source_evidence"]["review_id"] == "review-1"
    assert first["fingerprint"] == _canonical_digest(
        {key: value for key, value in first.items() if key != "fingerprint"}
    )


def test_phase15e_snapshot_rejects_tampered_review_evidence(tmp_path):
    prepared_scan(tmp_path)
    review_reference_version(tmp_path, **review_request())
    review_path = tmp_path / "models" / "scan-pump" / "reviews" / "v1" / "review.json"
    review_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ScanToMeshError) as caught:
        freeze_mesh_source(
            tmp_path,
            source_kind="phase15e_scanned_reference",
            source_ref={"model_id": "scan-pump", "version_id": "v1"},
        )

    assert caught.value.code == "mesh_source_integrity_error"
