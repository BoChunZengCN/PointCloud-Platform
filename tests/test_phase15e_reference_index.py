import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from phase15b2_support import (
    EXPERT,
    FEATURE_V1,
    MAPPING_V1,
    SCORING_V1,
    prepare_released_models,
)
from phase15e_support import prepared_scan, release_request, review_request
from pc_system.model_feature_index import build_model_feature_index, read_index_entries
from pc_system.model_index_release import release_model_feature_index
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_audit import load_operation
from pc_system.model_release import load_current_model_release, release_model_version
from pc_system.model_retrieval_config import publish_retrieval_config
from pc_system.reference_review import review_reference_version


FEATURE_V11 = {
    **FEATURE_V1,
    "schema_version": "1.1",
    "config_id": "retrieval-v11",
    "scanned_sampling": {
        "algorithm": "sha256_point_subset_v1",
        "point_count": 16,
        "random_seed": 20260903,
    },
}
SCORING_V11 = {**SCORING_V1, "config_id": "retrieval-v11"}
MAPPING_V11 = {**MAPPING_V1, "config_id": "retrieval-v11"}


def _publish_config(root, feature=FEATURE_V11):
    return publish_retrieval_config(
        root,
        config_id=feature["config_id"],
        feature=feature,
        scoring={**SCORING_V11, "config_id": feature["config_id"]},
        category_mapping={**MAPPING_V11, "config_id": feature["config_id"]},
        principal=EXPERT,
        operation_id=f"op-config-{feature['config_id']}",
        request_id=f"req-config-{feature['config_id']}",
        idempotency_key=f"idem-config-{feature['config_id']}",
    )


def _released_scan(root, *, approved=True):
    prepared_scan(root)
    if approved:
        review_reference_version(root, **review_request())
    return release_model_version(root, **release_request())


def _build(root, *, index_id="index-scans", mode="production", historical=None, config_id="retrieval-v11"):
    return build_model_feature_index(
        root,
        index_id=index_id,
        index_mode=mode,
        config_id=config_id,
        historical_releases=historical,
        principal=EXPERT,
        operation_id=f"op-{index_id}",
        request_id=f"req-{index_id}",
        idempotency_key=f"idem-{index_id}",
    )


def _build_reference(root, sequence="a"):
    from pc_system.reference_representation import build_reference_representation

    return build_reference_representation(
        root, model_id="scan-pump", version_id="v1", config=FEATURE_V11,
        principal=EXPERT, operation_id=f"op-reference-{sequence}",
        request_id=f"req-reference-{sequence}",
        idempotency_key=f"idem-reference-{sequence}",
    )


def test_verified_scanned_and_cad_releases_share_a_v11_production_index(tmp_path):
    """Removing source-family dispatch or type binding must fail this mixed index."""
    _publish_config(tmp_path)
    prepare_released_models(tmp_path)
    _released_scan(tmp_path)

    index = _build(tmp_path)
    entries = list(read_index_entries(tmp_path, index["index_id"]))

    assert {item["representation_type"] for item in entries} == {
        "cad_sampled", "scanned_reference"
    }
    assert {item["model_id"] for item in entries} == {"pump-a", "valve-a", "scan-pump"}
    scan = next(item for item in entries if item["model_id"] == "scan-pump")
    assert scan["version_id"] == "v1"
    assert scan["source_review_id"] == "review-1"
    assert scan["source_quality_status"] == "passed"
    assert index["schema_version"] == "1.1"


def test_unreviewed_scanned_release_is_not_silently_indexed(tmp_path):
    """A missing approval must become coverage evidence, never a directory-based inclusion."""
    _publish_config(tmp_path)
    prepared_scan(tmp_path)

    with pytest.raises(ModelMatchingError) as caught:
        release_model_version(tmp_path, **release_request())
    assert caught.value.code == "reference_review_required"
    assert not (tmp_path / "models" / "feature_indexes").exists()


def test_same_scanned_source_and_config_produce_the_same_representation(tmp_path):
    """Changing selection order or using random sampling must fail deterministic identity."""
    from pc_system.reference_representation import build_reference_representation

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    first = build_reference_representation(
        tmp_path, model_id="scan-pump", version_id="v1", config=FEATURE_V11,
        principal=EXPERT, operation_id="op-reference-a", request_id="req-reference-a",
        idempotency_key="idem-reference-a",
    )
    second = build_reference_representation(
        tmp_path, model_id="scan-pump", version_id="v1", config=FEATURE_V11,
        principal=EXPERT, operation_id="op-reference-b", request_id="req-reference-b",
        idempotency_key="idem-reference-b",
    )

    assert second == first
    assert first["representation_type"] == "scanned_reference"
    assert first["point_count"] == 16


def test_v10_config_explicitly_rejects_scanned_release(tmp_path):
    """Defaulting old CAD sampling for scans would make this test incorrectly pass."""
    _publish_config(tmp_path, FEATURE_V1)
    _released_scan(tmp_path)

    index = _build(tmp_path, index_id="index-v10", config_id="retrieval-v1")

    assert index["coverage"]["eligible_count"] == 1
    assert index["coverage"]["indexed_count"] == 0
    assert index["exclusions"] == [{
        "model_id": "scan-pump", "version_id": "v1", "code": "feature_config_invalid",
    }]


@pytest.mark.parametrize("replacement", ["cad_sampled", None])
def test_tampered_or_missing_representation_type_is_rejected_when_reading_index(tmp_path, replacement):
    """Dropping type from a feature/index identity must expose this substitution."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)
    index = _build(tmp_path)
    entry = next(item for item in read_index_entries(tmp_path, "index-scans") if item["model_id"] == "scan-pump")
    path = (tmp_path / "models" / "scan-pump" / "representations" / "v1"
            / "scanned_reference" / entry["representation_id"] / "representation.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if replacement is None:
        del value["representation_type"]
    else:
        value["representation_type"] = replacement
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as caught:
        list(read_index_entries(tmp_path, index["index_id"]))
    assert caught.value.code == "model_index_integrity_error"


def test_v11_index_release_keeps_the_verified_index_schema(tmp_path):
    """Downgrading a mixed-source release record must fail this schema binding."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)
    _build(tmp_path)

    release = release_model_feature_index(
        tmp_path, index_id="index-scans", release_id="index-release-1", action="activate",
        expected_current_release_id=None, rollback_of_release_id=None, reason="发布已核验混合索引",
        principal=EXPERT, operation_id="op-index-release-1", request_id="req-index-release-1",
        idempotency_key="idem-index-release-1",
    )

    assert release["schema_version"] == "1.1"


def test_scanned_child_operation_is_real_and_retries_after_completion_response_loss(tmp_path, monkeypatch):
    """A completed operation whose response is lost must replay the verified expression."""
    import pc_system.reference_representation as representations

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    actual = representations.complete_operation

    def complete_then_lose_response(*args):
        actual(*args)
        raise OSError("lost response after durable completion")

    monkeypatch.setattr(representations, "complete_operation", complete_then_lose_response)
    with pytest.raises(ModelMatchingError) as caught:
        _build_reference(tmp_path)
    assert caught.value.code == "publication_recovery_required"
    child_id = "op-reference-a"
    assert load_operation(tmp_path, child_id)["status"] == "completed"
    monkeypatch.setattr(representations, "complete_operation", actual)

    recovered = _build_reference(tmp_path)
    assert recovered["operation_id"] == child_id
    index = _build(tmp_path)
    scan = next(item for item in read_index_entries(tmp_path, index["index_id"]) if item["model_id"] == "scan-pump")
    child = load_operation(tmp_path, scan["sampling_operation_id"])
    assert child["operation_type"] == "reference_representation.build"
    assert child["status"] == "completed"


def test_reference_point_snapshot_reads_with_a_limit_before_parsing(tmp_path, monkeypatch):
    """Replacing bounded handle reads with Path.read_bytes must fail before parsing."""
    import pc_system.reference_representation as module

    path = tmp_path / "sampled_points.json"
    path.write_text("{}", encoding="utf-8")
    open_path = Path.open
    read_sizes = []

    class GuardedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, size=-1):
            read_sizes.append(size)
            if size < 0:
                raise AssertionError("unbounded read")
            return self.handle.read(size)

    def guarded_open(candidate, *args, **kwargs):
        handle = open_path(candidate, *args, **kwargs)
        return GuardedReader(handle) if candidate == path else handle

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(ValueError, match="sampled points invalid"):
        module._verified_points(path)
    assert read_sizes == [module._MAX_POINTS_BYTES + 1]


def test_maximum_scanned_sampling_payload_fits_the_verified_reader(tmp_path):
    """The documented 500,000-point configuration must not publish an unreadable artifact."""
    import pc_system.reference_representation as module

    point = [-1.7976931348623157e308, 1.7976931348623157e308, -1.234567890123e300]
    expected_count = 500_000
    value = {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "point_count": expected_count,
        "points": [point] * expected_count,
    }
    payload = module._canonical_json_bytes(value)
    assert len(payload) > 16 * 1024 * 1024
    path = tmp_path / "maximum_sampled_points.json"
    path.write_bytes(payload)

    loaded, _fingerprint = module._verified_points(path)
    assert loaded["point_count"] == expected_count
    assert loaded["points"][-1] == point


@pytest.mark.parametrize(
    ("model_id", "version_id", "representation_id"),
    [
        ("scan-pump", "../v1", "scanned-reference-x"),
        ("scan-pump", "v1", "../scanned-reference-x"),
        (None, "v1", "scanned-reference-x"),
    ],
)
def test_joint_reference_loader_rejects_untrusted_path_identifiers(
    tmp_path, model_id, version_id, representation_id
):
    """Unified point loading must reject path traversal and non-text identities at its boundary."""
    from pc_system.reference_representation import load_reference_representation_with_points

    with pytest.raises(ModelMatchingError) as caught:
        load_reference_representation_with_points(
            tmp_path, model_id, version_id, representation_id
        )
    assert caught.value.code == "model_representation_not_found"


def test_unified_representation_loader_rejects_tampered_path_identity(tmp_path):
    """A representation dictionary must not leak a lower-level invalid-path result."""
    from pc_system.model_representation import load_representation_points

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    representation = _build_reference(tmp_path)
    tampered = {**representation, "source_version_id": "../v1"}

    with pytest.raises(ModelMatchingError) as caught:
        load_representation_points(tmp_path, tampered)
    assert caught.value.code == "model_representation_integrity_error"


def test_source_evidence_failure_is_linked_to_an_existing_child_operation(tmp_path, monkeypatch):
    """Coverage evidence must never cite a calculated child ID that was not audited."""
    import pc_system.reference_representation as module

    _publish_config(tmp_path)
    _released_scan(tmp_path)

    def reject_source(*args, **kwargs):
        raise ModelMatchingError("reference_integrity_error", "simulated damaged source evidence")

    monkeypatch.setattr(module, "load_bundle", reject_source)

    index = _build(tmp_path, index_id="index-source-evidence-failed")
    exclusion = next(item for item in index["exclusions"] if item["model_id"] == "scan-pump")
    child = load_operation(tmp_path, exclusion["child_operation_id"])
    assert child["operation_type"] == "reference_representation.build"
    assert child["status"] == "failed"
    assert child["error"]["code"] == exclusion["code"] == "reference_integrity_error"


def test_approved_historical_scan_is_only_in_explicit_challenger(tmp_path):
    """Replacing current heads with history or skipping review evidence must fail this selection."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)
    prepared_scan(tmp_path, version_id="v2")
    review_reference_version(tmp_path, **review_request(version_id="v2", sequence="2"))
    current = release_model_version(
        tmp_path,
        **release_request(
            sequence="2", version_id="v2",
            expected_current_release_id="release-1",
        ),
    )
    historical = [{"model_id": "scan-pump", "release_id": "release-1"}]
    challenger = _build(tmp_path, index_id="index-history", mode="challenger", historical=historical)

    assert challenger["current_heads"] == []
    row = next(read_index_entries(tmp_path, challenger["index_id"]))
    assert row["source_mode"] == "challenger"
    assert row["version_id"] == "v1"
    assert row["source_review_id"] == "review-1"
    assert load_current_model_release(tmp_path, "scan-pump") == current
    assert not (tmp_path / "models" / "feature_index_releases" / "current.json").exists()


@pytest.mark.parametrize("artifact", ["operation_owner.json", "sampled_points.json", "representation.json"])
@pytest.mark.parametrize("visible", [False, True])
def test_each_reference_artifact_publication_boundary_recovers_original_operation(
    tmp_path, monkeypatch, artifact, visible
):
    """A crash immediately before or after one artifact becomes visible must be recoverable."""
    import pc_system.reference_representation as module

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    publish = module._publish_exact_json
    interrupted = False

    def fail_once(path, value, **kwargs):
        nonlocal interrupted
        if path.name == artifact and not interrupted:
            interrupted = True
            if visible:
                publish(path, value, **kwargs)
            raise OSError("simulated publication interruption")
        return publish(path, value, **kwargs)

    monkeypatch.setattr(module, "_publish_exact_json", fail_once)
    with pytest.raises(ModelMatchingError) as caught:
        _build_reference(tmp_path)
    assert caught.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-reference-a")["status"] == "running"

    monkeypatch.setattr(module, "_publish_exact_json", publish)
    result = _build_reference(tmp_path)
    assert result["representation_type"] == "scanned_reference"
    assert load_operation(tmp_path, "op-reference-a")["status"] == "completed"


def test_reference_publication_event_response_loss_recovers_original_operation(tmp_path, monkeypatch):
    """A persisted business event followed by response loss must not strand the producer."""
    import pc_system.reference_representation as module

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    ensure = module.ensure_operation_event
    interrupted = False

    def lose_response(root, operation_id, event_type, details):
        nonlocal interrupted
        result = ensure(root, operation_id, event_type, details)
        if event_type == "reference_representation.published" and not interrupted:
            interrupted = True
            raise OSError("simulated event response loss")
        return result

    monkeypatch.setattr(module, "ensure_operation_event", lose_response)
    with pytest.raises(ModelMatchingError) as caught:
        _build_reference(tmp_path)
    assert caught.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, "op-reference-a")["status"] == "running"

    monkeypatch.setattr(module, "ensure_operation_event", ensure)
    assert _build_reference(tmp_path)["representation_type"] == "scanned_reference"


def test_incomplete_reference_owner_cannot_be_taken_over(tmp_path, monkeypatch):
    """A second operation must not overwrite or terminalize another running producer."""
    import pc_system.reference_representation as module

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    publish = module._publish_exact_json

    def stop_after_owner(path, value, **kwargs):
        if path.name == "sampled_points.json":
            raise OSError("pause after owner")
        return publish(path, value, **kwargs)

    monkeypatch.setattr(module, "_publish_exact_json", stop_after_owner)
    with pytest.raises(ModelMatchingError):
        _build_reference(tmp_path, "owner")
    monkeypatch.setattr(module, "_publish_exact_json", publish)

    with pytest.raises(ModelMatchingError) as caught:
        _build_reference(tmp_path, "foreign")
    assert caught.value.code == "operation_busy"
    assert load_operation(tmp_path, "op-reference-owner")["status"] == "running"


def test_same_reference_config_concurrency_has_one_producer_and_one_verified_reuse(tmp_path):
    """Concurrent equal requests must converge without replacing immutable artifacts."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_build_reference, tmp_path, sequence) for sequence in ("a", "b")]
    results = [future.result() for future in futures]

    assert results[0] == results[1]
    assert {load_operation(tmp_path, f"op-reference-{sequence}")["status"] for sequence in ("a", "b")} == {"completed"}


def test_fatal_reference_child_error_aborts_index_and_preserves_parent_for_recovery(tmp_path, monkeypatch):
    """Audit/recovery failures must not be converted into an ordinary coverage exclusion."""
    import pc_system.model_feature_index as module

    _publish_config(tmp_path)
    _released_scan(tmp_path)

    def fail(*args, **kwargs):
        raise ModelMatchingError("audit_integrity_error", "simulated fatal child evidence")

    monkeypatch.setattr(module, "build_reference_representation", fail)
    with pytest.raises(ModelMatchingError) as caught:
        _build(tmp_path, index_id="index-fatal")
    assert caught.value.code == "audit_integrity_error"
    assert load_operation(tmp_path, "op-index-fatal")["status"] == "running"
    assert not (tmp_path / "models" / "feature_indexes" / "index-fatal" / "index_manifest.json").exists()


def test_deterministic_reference_generation_failure_is_auditable_coverage_evidence(tmp_path, monkeypatch):
    """A deterministic child failure must name a real failed operation in coverage evidence."""
    import pc_system.reference_representation as module

    _publish_config(tmp_path)
    _released_scan(tmp_path)

    def fail(*args, **kwargs):
        raise ModelMatchingError("reference_geometry_invalid", "simulated deterministic selection failure")

    monkeypatch.setattr(module, "select_reference_points", fail)
    index = _build(tmp_path, index_id="index-generation-failed")
    exclusion = index["exclusions"][0]
    child = load_operation(tmp_path, exclusion["child_operation_id"])
    assert exclusion["code"] == "reference_geometry_invalid"
    assert child["operation_type"] == "reference_representation.build"
    assert child["status"] == "failed"
    assert child["error"]["code"] == exclusion["code"]


def test_corrupt_existing_reference_is_auditable_coverage_evidence(tmp_path):
    """A corrupt prior expression must be excluded through a real attempted child operation."""
    _publish_config(tmp_path)
    _released_scan(tmp_path)
    representation = _build_reference(tmp_path, "producer")
    path = (tmp_path / "models" / "scan-pump" / "representations" / "v1"
            / "scanned_reference" / representation["representation_id"] / "representation.json")
    path.write_text("{}", encoding="utf-8")

    index = _build(tmp_path, index_id="index-corrupt-reference")
    exclusion = index["exclusions"][0]
    child = load_operation(tmp_path, exclusion["child_operation_id"])
    assert exclusion["code"] == "model_representation_integrity_error"
    assert child["operation_type"] == "reference_representation.build"
    assert child["status"] == "failed"


def test_reference_point_consumer_uses_the_verified_byte_snapshot(tmp_path, monkeypatch):
    """The point list returned to features must come from the same bytes whose hash was checked."""
    from io import BytesIO

    from pc_system.model_representation import load_representation_points

    _publish_config(tmp_path)
    _released_scan(tmp_path)
    representation = _build_reference(tmp_path)
    points_path = (tmp_path / "models" / "scan-pump" / "representations" / "v1"
                   / "scanned_reference" / representation["representation_id"] / "sampled_points.json")
    open_path = Path.open
    reads = 0

    def changing_open(path, *args, **kwargs):
        nonlocal reads
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == points_path and mode == "rb":
            reads += 1
            if reads > 1:
                return BytesIO(b"{}")
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", changing_open)
    points = load_representation_points(tmp_path, representation)
    assert len(points) == representation["point_count"]
    assert reads == 1
