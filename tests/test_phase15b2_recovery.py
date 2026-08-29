import json
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import pc_system.model_feature_index as feature_index_module
import pc_system.model_feature_store as feature_store_module
import pc_system.model_index_release as index_release_module
import pc_system.model_retrieval as retrieval_module
from pc_system.model_feature_index import (
    build_model_feature_index,
    load_model_feature_index,
    read_index_entries,
)
from pc_system.model_feature_store import load_feature, publish_object_feature
from pc_system.model_index_release import (
    load_current_model_feature_index_release,
    release_model_feature_index,
)
from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_retrieval import (
    load_model_retrieval,
    retrieve_model_candidates,
)
from pc_system.model_retrieval_config import publish_retrieval_config
from pc_system.segmentation_service import run_segmentation
from phase15b2_support import (
    EXPERT,
    FEATURE_V1,
    MAPPING_V1,
    SCORING_V1,
    _mesh_reader,
    prepare_released_models,
)


class SimulatedInterruption(BaseException):
    pass


def _points():
    return [
        {"x": x, "y": y, "z": z}
        for x in (0.0, 0.6, 1.2, 1.8)
        for y in (0.0, 1.0)
        for z in (0.0, 0.5)
    ]


@pytest.fixture
def recovery_project(tmp_path):
    publish_retrieval_config(
        tmp_path,
        config_id="retrieval-v1",
        feature=FEATURE_V1,
        scoring=SCORING_V1,
        category_mapping=MAPPING_V1,
        principal=EXPERT,
        operation_id="op-config-recovery",
        request_id="req-config-recovery",
        idempotency_key="idem-config-recovery",
    )
    prepared = prepare_released_models(tmp_path)
    source = tmp_path / "scan-recovery.points.json"
    source.write_text(json.dumps(_points()), encoding="utf-8")
    run_segmentation(
        tmp_path,
        asset_id="scan-recovery",
        asset_version="v1",
        source_uri=str(source),
        points=_points(),
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 3.0,
            "min_points": 1,
        },
        run_id="run-recovery-001",
    )
    index = _build_index(
        tmp_path,
        index_id="index-production-001",
        operation_id="op-index-production-001",
    )
    release = _release_index(
        tmp_path,
        index_id=index["index_id"],
        release_id="index-release-production-001",
        operation_id="op-index-release-production-001",
        expected_current_release_id=None,
    )
    return {
        "root": tmp_path,
        "prepared": prepared,
        "index": index,
        "release": release,
    }


def _build_index(root, *, index_id, operation_id):
    return build_model_feature_index(
        root,
        index_id=index_id,
        index_mode="production",
        config_id="retrieval-v1",
        historical_releases=None,
        principal=EXPERT,
        operation_id=operation_id,
        request_id=f"req-{operation_id}",
        idempotency_key=f"idem-{operation_id}",
        mesh_reader=_mesh_reader,
    )


def _release_index(
    root,
    *,
    index_id,
    release_id,
    operation_id,
    expected_current_release_id,
):
    return release_model_feature_index(
        root,
        index_id=index_id,
        release_id=release_id,
        action="activate",
        expected_current_release_id=expected_current_release_id,
        rollback_of_release_id=None,
        reason="Recovery gate",
        principal=EXPERT,
        operation_id=operation_id,
        request_id=f"req-{operation_id}",
        idempotency_key=f"idem-{operation_id}",
    )


def _retrieve(root, *, run_id="retrieval-recovery-001", operation_id="op-retrieval-recovery-001"):
    return retrieve_model_candidates(
        root,
        retrieval_run_id=run_id,
        source_kind="segmentation_run",
        asset_id="scan-recovery",
        source_id="run-recovery-001",
        instance_id="obj-001",
        index_release_id=None,
        index_id=None,
        top_k=10,
        keywords=[],
        tags=[],
        manufacturer=None,
        model_number=None,
        hint_source=None,
        principal=EXPERT,
        operation_id=operation_id,
        request_id=f"req-{operation_id}",
        idempotency_key=f"idem-{operation_id}",
    )


def _object_feature_request(root):
    return {
        "project_root": root,
        "source_kind": "segmentation_run",
        "asset_id": "scan-recovery",
        "source_id": "run-recovery-001",
        "instance_id": "obj-001",
        "config_id": "retrieval-v1",
        "principal": EXPERT,
        "operation_id": "op-object-feature-recovery",
        "request_id": "req-object-feature-recovery",
        "idempotency_key": "idem-object-feature-recovery",
    }


@pytest.mark.parametrize(
    "interrupt_at",
    ["owner", "content", "published_event", "audit_complete"],
)
def test_feature_same_operation_recovers_at_each_publication_boundary(
    recovery_project, monkeypatch, interrupt_at
):
    root = recovery_project["root"]
    request = _object_feature_request(root)
    original_publish = feature_store_module._publish_exact_json
    original_event = feature_store_module.ensure_operation_event
    original_complete = feature_store_module.complete_operation

    if interrupt_at in {"owner", "content"}:
        target = "operation_owner.json" if interrupt_at == "owner" else "feature.json"

        def interrupt_publish(path, value, **kwargs):
            result = original_publish(path, value, **kwargs)
            if path.name == target:
                raise SimulatedInterruption()
            return result

        monkeypatch.setattr(
            feature_store_module, "_publish_exact_json", interrupt_publish
        )
    elif interrupt_at == "published_event":

        def interrupt_event(root_path, operation_id, event_type, details):
            result = original_event(root_path, operation_id, event_type, details)
            if event_type == "object_feature.published":
                raise SimulatedInterruption()
            return result

        monkeypatch.setattr(
            feature_store_module, "ensure_operation_event", interrupt_event
        )
    else:

        def interrupt_complete(root_path, operation_id, result):
            value = original_complete(root_path, operation_id, result)
            raise SimulatedInterruption()

        monkeypatch.setattr(
            feature_store_module, "complete_operation", interrupt_complete
        )

    with pytest.raises(SimulatedInterruption):
        publish_object_feature(**request)

    monkeypatch.setattr(
        feature_store_module, "_publish_exact_json", original_publish
    )
    monkeypatch.setattr(
        feature_store_module, "ensure_operation_event", original_event
    )
    monkeypatch.setattr(
        feature_store_module, "complete_operation", original_complete
    )
    recovered = publish_object_feature(**request)

    assert recovered["status"] == "ready"
    assert load_operation(root, request["operation_id"])["status"] == "completed"
    assert not list(root.rglob("*quarantine*"))


@pytest.mark.parametrize("interrupt_at", ["manifest", "published_event"])
def test_index_same_operation_recovers_after_visible_manifest(
    recovery_project, monkeypatch, interrupt_at
):
    root = recovery_project["root"]
    original_publish = feature_index_module._publish_exact_json
    original_event = feature_index_module.ensure_operation_event

    if interrupt_at == "manifest":

        def interrupt_publish(path, value, **kwargs):
            result = original_publish(path, value, **kwargs)
            if path.name == "index_manifest.json":
                raise SimulatedInterruption()
            return result

        monkeypatch.setattr(
            feature_index_module, "_publish_exact_json", interrupt_publish
        )
    else:

        def interrupt_event(root_path, operation_id, event_type, details):
            result = original_event(root_path, operation_id, event_type, details)
            if event_type == "model_feature_index.published":
                raise SimulatedInterruption()
            return result

        monkeypatch.setattr(
            feature_index_module, "ensure_operation_event", interrupt_event
        )

    with pytest.raises(SimulatedInterruption):
        _build_index(
            root,
            index_id="index-recovery-002",
            operation_id="op-index-recovery-002",
        )

    monkeypatch.setattr(
        feature_index_module, "_publish_exact_json", original_publish
    )
    monkeypatch.setattr(
        feature_index_module, "ensure_operation_event", original_event
    )
    recovered = _build_index(
        root,
        index_id="index-recovery-002",
        operation_id="op-index-recovery-002",
    )

    assert recovered["status"] == "ready"
    assert load_operation(root, "op-index-recovery-002")["status"] == "completed"
    assert not list(root.rglob("*quarantine*"))


@pytest.mark.parametrize("interrupt_at", ["release", "published_event"])
def test_index_release_same_operation_recovers_after_visible_release(
    recovery_project, monkeypatch, interrupt_at
):
    root = recovery_project["root"]
    current = recovery_project["release"]["release_id"]
    original_publish = index_release_module._publish_exact_json
    original_event = index_release_module.ensure_operation_event

    if interrupt_at == "release":

        def interrupt_publish(path, value, **kwargs):
            result = original_publish(path, value, **kwargs)
            if path.name == "release.json":
                raise SimulatedInterruption()
            return result

        monkeypatch.setattr(
            index_release_module, "_publish_exact_json", interrupt_publish
        )
    else:

        def interrupt_event(root_path, operation_id, event_type, details):
            result = original_event(root_path, operation_id, event_type, details)
            if event_type == "model_feature_index_release.published":
                raise SimulatedInterruption()
            return result

        monkeypatch.setattr(
            index_release_module, "ensure_operation_event", interrupt_event
        )

    with pytest.raises(SimulatedInterruption):
        _release_index(
            root,
            index_id=recovery_project["index"]["index_id"],
            release_id="index-release-recovery-002",
            operation_id="op-index-release-recovery-002",
            expected_current_release_id=current,
        )

    monkeypatch.setattr(
        index_release_module, "_publish_exact_json", original_publish
    )
    monkeypatch.setattr(
        index_release_module, "ensure_operation_event", original_event
    )
    recovered = _release_index(
        root,
        index_id=recovery_project["index"]["index_id"],
        release_id="index-release-recovery-002",
        operation_id="op-index-release-recovery-002",
        expected_current_release_id=current,
    )

    assert recovered["status"] == "published"
    assert load_current_model_feature_index_release(root) == recovered
    assert not list(root.rglob("*quarantine*"))


def test_retrieval_same_operation_recovers_after_visible_report(
    recovery_project, monkeypatch
):
    root = recovery_project["root"]
    original = retrieval_module._publish_exact_json

    def interrupt_publish(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if path.name == "retrieval_report.json":
            raise SimulatedInterruption()
        return result

    monkeypatch.setattr(
        retrieval_module, "_publish_exact_json", interrupt_publish
    )
    with pytest.raises(SimulatedInterruption):
        _retrieve(root)

    monkeypatch.setattr(retrieval_module, "_publish_exact_json", original)
    recovered = _retrieve(root)

    assert recovered["status"] == "completed"
    assert load_operation(root, "op-retrieval-recovery-001")["status"] == "completed"
    assert not list(root.rglob("*quarantine*"))


@pytest.mark.parametrize("projection_visible", [False, True])
def test_completed_index_release_recovers_current_projection(
    recovery_project, monkeypatch, projection_visible
):
    root = recovery_project["root"]
    current = recovery_project["release"]["release_id"]
    original = index_release_module.write_json

    def interrupt_after_projection(value, path):
        if projection_visible:
            original(value, path)
        raise SimulatedInterruption()

    monkeypatch.setattr(index_release_module, "write_json", interrupt_after_projection)
    with pytest.raises(SimulatedInterruption):
        _release_index(
            root,
            index_id=recovery_project["index"]["index_id"],
            release_id="index-release-projection-002",
            operation_id="op-index-release-projection-002",
            expected_current_release_id=current,
        )

    monkeypatch.setattr(index_release_module, "write_json", original)
    recovered = _release_index(
        root,
        index_id=recovery_project["index"]["index_id"],
        release_id="index-release-projection-002",
        operation_id="op-index-release-projection-002",
        expected_current_release_id=current,
    )

    assert load_operation(root, "op-index-release-projection-002")["status"] == "completed"
    assert load_current_model_feature_index_release(root) == recovered


def test_index_entries_parent_fsync_uncertainty_stays_running_and_recovers(
    recovery_project, monkeypatch
):
    root = recovery_project["root"]
    original = getattr(feature_index_module, "_fsync_directory", None)

    def fail_parent_fsync(_path):
        raise OSError("simulated uncertain parent fsync")

    monkeypatch.setattr(
        feature_index_module, "_fsync_directory", fail_parent_fsync, raising=False
    )
    with pytest.raises(ModelMatchingError) as error:
        _build_index(
            root,
            index_id="index-fsync-recovery",
            operation_id="op-index-fsync-recovery",
        )

    assert error.value.code == "publication_recovery_required"
    assert load_operation(root, "op-index-fsync-recovery")["status"] == "running"
    assert original is not None
    monkeypatch.setattr(feature_index_module, "_fsync_directory", original)
    recovered = _build_index(
        root,
        index_id="index-fsync-recovery",
        operation_id="op-index-fsync-recovery",
    )

    assert recovered["status"] == "ready"
    assert not list(root.rglob("*quarantine*"))


def test_temp_cleanup_error_does_not_hide_visible_index_recovery_state(
    recovery_project, monkeypatch
):
    root = recovery_project["root"]
    original_unlink = Path.unlink

    def fail_parent_fsync(_path):
        raise OSError("simulated uncertain parent fsync")

    def fail_temp_cleanup(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError("simulated temporary cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(feature_index_module, "_fsync_directory", fail_parent_fsync)
    monkeypatch.setattr(Path, "unlink", fail_temp_cleanup)

    with pytest.raises(ModelMatchingError) as error:
        _build_index(
            root,
            index_id="index-fsync-cleanup-recovery",
            operation_id="op-index-fsync-cleanup-recovery",
        )

    assert error.value.code == "publication_recovery_required"
    assert (
        load_operation(root, "op-index-fsync-cleanup-recovery")["status"]
        == "running"
    )
    assert not list(root.rglob("*quarantine*"))


def _add_duplicate_field(path: Path, field: str) -> str:
    original = path.read_text(encoding="utf-8")
    value = json.loads(original)[field]
    closing = original.rfind("}")
    prefix = original[:closing].rstrip()
    duplicate = json.dumps(value, ensure_ascii=False, allow_nan=False)
    path.write_text(
        f'{prefix},\n  "{field}": {duplicate}\n}}', encoding="utf-8"
    )
    return original


def _json_fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_retrieval_recovery_rejects_invalid_visible_duration(
    recovery_project, monkeypatch
):
    root = recovery_project["root"]
    original = retrieval_module._publish_exact_json

    def interrupt_publish(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if path.name == "retrieval_report.json":
            raise SimulatedInterruption()
        return result

    monkeypatch.setattr(
        retrieval_module, "_publish_exact_json", interrupt_publish
    )
    with pytest.raises(SimulatedInterruption):
        _retrieve(
            root,
            run_id="retrieval-invalid-duration",
            operation_id="op-retrieval-invalid-duration",
        )
    monkeypatch.setattr(retrieval_module, "_publish_exact_json", original)

    report_path = (
        root
        / "reports"
        / "model_retrieval"
        / "scan-recovery"
        / "run-recovery-001"
        / "obj-001"
        / "retrieval-invalid-duration"
        / "retrieval_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["scan_duration_microseconds"] = -1
    report["report_fingerprint"] = _json_fingerprint(
        {
            key: value
            for key, value in report.items()
            if key != "report_fingerprint"
        }
    )
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as error:
        _retrieve(
            root,
            run_id="retrieval-invalid-duration",
            operation_id="op-retrieval-invalid-duration",
        )

    assert error.value.code == "feature_integrity_error"
    assert not list(root.rglob("*quarantine*"))


def test_duplicate_keys_are_rejected_for_all_published_artifacts(
    recovery_project
):
    root = recovery_project["root"]
    report = _retrieve(root)
    feature_path = next(
        (root / "models" / "pump-a" / "features").rglob("feature.json")
    )
    feature = json.loads(feature_path.read_text(encoding="utf-8"))
    feature_identity = {
        "model_id": feature["source"]["model_id"],
        "version_id": feature["source"]["version_id"],
        "representation_id": feature["source"]["representation_id"],
        "feature_id": feature["feature_id"],
    }
    report_path = (
        root
        / "reports"
        / "model_retrieval"
        / "scan-recovery"
        / "run-recovery-001"
        / "obj-001"
        / report["retrieval_run_id"]
        / "retrieval_report.json"
    )
    cases = [
        (
            feature_path,
            lambda: load_feature(
                root, feature_type="model", identity=feature_identity
            ),
            "feature_integrity_error",
        ),
        (
            root
            / "models"
            / "feature_indexes"
            / recovery_project["index"]["index_id"]
            / "index_manifest.json",
            lambda: load_model_feature_index(
                root,
                recovery_project["index"]["index_id"],
                require_current_heads=False,
            ),
            "model_index_integrity_error",
        ),
        (
            root
            / "models"
            / "feature_index_releases"
            / recovery_project["release"]["release_id"]
            / "release.json",
            lambda: load_current_model_feature_index_release(root),
            "model_index_integrity_error",
        ),
        (
            report_path,
            lambda: load_model_retrieval(
                root,
                asset_id="scan-recovery",
                source_id="run-recovery-001",
                instance_id="obj-001",
                retrieval_run_id=report["retrieval_run_id"],
            ),
            "feature_integrity_error",
        ),
    ]

    for path, loader, expected_code in cases:
        original = _add_duplicate_field(path, "status")
        try:
            with pytest.raises(ModelMatchingError) as error:
                loader()
            assert error.value.code == expected_code
        finally:
            path.write_text(original, encoding="utf-8")


def test_non_plain_and_reparse_index_artifacts_are_rejected(
    recovery_project, monkeypatch
):
    root = recovery_project["root"]
    index_id = recovery_project["index"]["index_id"]
    coverage = root / "models" / "feature_indexes" / index_id / "coverage.json"
    original_lstat = Path.lstat

    def reparse_lstat(path):
        info = original_lstat(path)
        if path == coverage:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=0x400,
            )
        return info

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(ModelMatchingError) as reparse_error:
        load_model_feature_index(root, index_id, require_current_heads=False)
    assert reparse_error.value.code == "model_index_integrity_error"

    monkeypatch.setattr(Path, "lstat", original_lstat)
    original = coverage.read_bytes()
    coverage.unlink()
    coverage.mkdir()
    try:
        with pytest.raises(ModelMatchingError) as directory_error:
            load_model_feature_index(root, index_id, require_current_heads=False)
        assert directory_error.value.code == "model_index_integrity_error"
    finally:
        coverage.rmdir()
        coverage.write_bytes(original)


def test_symlinked_index_artifact_is_rejected(recovery_project):
    root = recovery_project["root"]
    index_id = recovery_project["index"]["index_id"]
    coverage = root / "models" / "feature_indexes" / index_id / "coverage.json"
    outside = root / "outside-coverage.json"
    outside.write_bytes(coverage.read_bytes())
    coverage.unlink()
    try:
        os.symlink(outside, coverage)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ModelMatchingError) as error:
        load_model_feature_index(root, index_id, require_current_heads=False)

    assert error.value.code == "model_index_integrity_error"


def test_oversized_jsonl_row_is_rejected(recovery_project):
    root = recovery_project["root"]
    index_id = recovery_project["index"]["index_id"]
    entries = root / "models" / "feature_indexes" / index_id / "entries.jsonl"
    entries.write_bytes(
        b'{"padding":"' + b"x" * (64 * 1024) + b'"}\n'
    )

    with pytest.raises(ModelMatchingError) as error:
        list(read_index_entries(root, index_id))

    assert error.value.code == "model_index_integrity_error"


def test_foreign_index_owner_is_not_taken_over(recovery_project):
    root = recovery_project["root"]
    candidate = root / "models" / "feature_indexes" / "index-foreign-owner"
    candidate.mkdir(parents=True)
    owner_path = candidate / "operation_owner.json"
    owner = {
        "schema_version": "1.0",
        "index_id": "index-foreign-owner",
        "operation_id": "op-foreign",
        "request_id": "req-foreign",
        "request_fingerprint": "f" * 64,
    }
    owner_path.write_text(json.dumps(owner, sort_keys=True), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as error:
        _build_index(
            root,
            index_id="index-foreign-owner",
            operation_id="op-index-foreign-owner",
        )

    assert error.value.code == "operation_busy"
    assert json.loads(owner_path.read_text(encoding="utf-8")) == owner
    assert load_operation(root, "op-index-foreign-owner")["status"] == "running"
    assert not list(root.rglob("*quarantine*"))


def test_published_index_and_retrieval_reject_resource_identity_owner_tampering(
    recovery_project
):
    root = recovery_project["root"]
    report = _retrieve(root)
    index_owner_path = (
        root
        / "models"
        / "feature_indexes"
        / recovery_project["index"]["index_id"]
        / "operation_owner.json"
    )
    retrieval_owner_path = (
        root
        / "reports"
        / "model_retrieval"
        / "scan-recovery"
        / "run-recovery-001"
        / "obj-001"
        / report["retrieval_run_id"]
        / "operation_owner.json"
    )
    cases = [
        (
            index_owner_path,
            "index_id",
            "foreign-index",
            lambda: load_model_feature_index(
                root,
                recovery_project["index"]["index_id"],
                require_current_heads=False,
            ),
            "model_index_integrity_error",
        ),
        (
            retrieval_owner_path,
            "retrieval_run_id",
            "foreign-retrieval",
            lambda: load_model_retrieval(
                root,
                asset_id="scan-recovery",
                source_id="run-recovery-001",
                instance_id="obj-001",
                retrieval_run_id=report["retrieval_run_id"],
            ),
            "feature_integrity_error",
        ),
    ]

    for path, field, foreign_value, loader, expected_code in cases:
        original = path.read_text(encoding="utf-8")
        owner = json.loads(original)
        owner[field] = foreign_value
        path.write_text(json.dumps(owner), encoding="utf-8")
        try:
            with pytest.raises(ModelMatchingError) as error:
                loader()
            assert error.value.code == expected_code
        finally:
            path.write_text(original, encoding="utf-8")


def test_concurrent_index_requests_publish_once_and_complete_both_operations(
    recovery_project
):
    root = recovery_project["root"]

    def build(sequence):
        return _build_index(
            root,
            index_id="index-concurrent",
            operation_id=f"op-index-concurrent-{sequence}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(build, (1, 2)))

    assert results[0] == results[1]
    assert {
        load_operation(root, "op-index-concurrent-1")["status"],
        load_operation(root, "op-index-concurrent-2")["status"],
    } == {"completed"}
    manifests = list(
        (root / "models" / "feature_indexes" / "index-concurrent").glob(
            "index_manifest.json"
        )
    )
    assert len(manifests) == 1
