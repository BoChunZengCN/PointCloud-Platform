"""扫描版本、审计门禁与源冻结恢复的行为回归。"""

import importlib
import json
from pathlib import Path

import pytest

from phase15e_support import EXPERT, import_request, scan_asset, scan_source
from pc_system.model_library import load_model_asset
from pc_system.model_import import import_model_version, load_model_version
from pc_system.model_matching_audit import load_operation, read_verified_operation_events
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal
from pc_system.model_resource_lock import model_resource_lock


def importer():
    assert importlib.util.find_spec("pc_system.reference_import") is not None, "缺少扫描导入服务"
    return importlib.import_module("pc_system.reference_import")


def test_scan_asset_uses_new_schema_and_default_cad_asset_keeps_old_bytes(tmp_path):
    scan = scan_asset(tmp_path)
    cad = scan_asset(tmp_path, "cad-pump", source_family="cad_mesh")
    assert scan["schema_version"] == "1.1" and scan["source_family"] == "scanned_reference"
    assert cad["schema_version"] == "1.0" and "source_family" not in cad
    path = tmp_path / "models/cad-pump/model_asset.json"
    before = path.read_bytes()
    assert scan_asset(tmp_path, "cad-pump", source_family="cad_mesh") == cad
    assert path.read_bytes() == before
    assert load_model_asset(tmp_path, "scan-pump") == scan
    assert scan_asset(tmp_path) == scan


def test_import_commits_real_scan_and_replays_without_external_source(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    path = scan_source(tmp_path / "source.ply")
    request = import_request(path)
    value = module.import_reference_version(tmp_path, **request)
    assert value["schema_version"] == "2.0" and value["source_kind"] == "scanned_reference"
    assert value["coordinate_unit"] == "m" and value["point_count"] == 64
    assert value["index_status"] == "not_indexed"
    directory = tmp_path / "models/scan-pump/versions/v1"
    before = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert not (directory / "source_geometry.json").exists()
    path.unlink()
    assert module.import_reference_version(tmp_path, **request) == value
    assert load_model_version(tmp_path, "scan-pump", "v1") == value
    assert before == {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    operation = load_operation(tmp_path, request["operation_id"])
    assert operation["status"] == "completed"
    event_types = [e["event_type"] for e in read_verified_operation_events(tmp_path, request["operation_id"])]
    assert "reference.source_frozen" in event_types and "reference.quality_checked" in event_types


@pytest.mark.parametrize("family,entry", [("cad_mesh", "scan"), ("scanned_reference", "cad")])
def test_import_entrypoints_reject_the_other_asset_family(tmp_path, family, entry):
    module = importer()
    scan_asset(tmp_path, source_family=family)
    path = scan_source(tmp_path / "source.ply")
    function = module.import_reference_version if entry == "scan" else import_model_version
    with pytest.raises(ModelMatchingError) as caught:
        function(tmp_path, **import_request(path))
    assert caught.value.code == "model_source_family_conflict"
    assert load_operation(tmp_path, "import-scan-pump-v1")["status"] == "failed"


def test_nonexpert_import_is_audited_and_creates_no_version(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **import_request(scan_source(tmp_path / "source.ply"), principal=Principal("reader", frozenset({"operator"}), "configured_token")))
    assert load_operation(tmp_path, "import-scan-pump-v1")["status"] == "failed"
    assert not (tmp_path / "models/scan-pump/versions/v1").exists()


def test_recovery_uses_frozen_copy_after_external_source_changed(tmp_path, monkeypatch):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    actual = module.decode_reference_file
    monkeypatch.setattr(module, "decode_reference_file", lambda *a, **k: (_ for _ in ()).throw(OSError("进程启动暂时失败")))
    with pytest.raises(ModelMatchingError) as caught:
        module.import_reference_version(tmp_path, **request)
    assert caught.value.code == "publication_recovery_required"
    assert load_operation(tmp_path, request["operation_id"])["status"] == "running"
    request["source_path"].write_bytes(b"changed external file")
    monkeypatch.setattr(module, "decode_reference_file", actual)
    value = module.import_reference_version(tmp_path, **request)
    assert value["point_count"] == 64


def test_incomplete_copy_without_freeze_marker_is_not_reused(tmp_path, monkeypatch):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    real = module._capture_source
    def interrupt(source, directory, **kwargs):
        (directory / "partial.ply").write_bytes(b"partial")
        raise OSError("模拟复制中断")
    monkeypatch.setattr(module, "_capture_source", interrupt)
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    monkeypatch.setattr(module, "_capture_source", real)
    result = module.import_reference_version(tmp_path, **request)
    assert result["point_count"] == 64
    assert (tmp_path / "models/scan-pump/versions/v1/source/partial.ply").read_bytes() == b"partial"


def test_commit_without_completed_audit_is_hidden_then_recovered(tmp_path, monkeypatch):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    real = module.complete_operation
    monkeypatch.setattr(module, "complete_operation", lambda *a, **k: (_ for _ in ()).throw(OSError("模拟审计失败")))
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    with pytest.raises(ModelMatchingError):
        load_model_version(tmp_path, "scan-pump", "v1")
    monkeypatch.setattr(module, "complete_operation", real)
    request["source_path"].unlink()
    assert module.import_reference_version(tmp_path, **request)["point_count"] == 64


@pytest.mark.parametrize("name", ["owner.json", "source_frozen.json", "reference_points.json", "quality_report.json", "model_manifest.json", "commit.json"])
def test_corrupt_committed_artifact_is_never_publicly_loaded(tmp_path, name):
    module = importer()
    scan_asset(tmp_path)
    module.import_reference_version(tmp_path, **import_request(scan_source(tmp_path / "source.ply")))
    target = tmp_path / "models/scan-pump/versions/v1" / name
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ModelMatchingError):
        load_model_version(tmp_path, "scan-pump", "v1")


def test_foreign_operation_cannot_take_over_unfinished_version(tmp_path, monkeypatch):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    monkeypatch.setattr(module, "decode_reference_file", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    request.update(operation_id="foreign", request_id="foreign", idempotency_key="foreign")
    with pytest.raises(ModelMatchingError) as caught:
        module.import_reference_version(tmp_path, **request)
    assert caught.value.code == "reference_version_owned"


def test_low_quality_scan_has_complete_report_but_is_not_approved(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    value = module.import_reference_version(tmp_path, **import_request(scan_source(tmp_path / "small.ply", count=16)))
    assert value["quality_status"] == "rejected"
    assert "approved" not in value.values()


def test_decode_slots_reject_third_worker_without_losing_frozen_source(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    with model_resource_lock(tmp_path, "reference-decode", "0"), model_resource_lock(tmp_path, "reference-decode", "1"):
        with pytest.raises(ModelMatchingError) as caught:
            module.import_reference_version(tmp_path, **request)
    assert caught.value.code == "operation_busy"
    assert module.import_reference_version(tmp_path, **request)["point_count"] == 64


def test_staged_sources_count_toward_retained_quota(tmp_path, monkeypatch):
    module = importer()
    scan_asset(tmp_path)
    staging = tmp_path / "imports/models"
    staging.mkdir(parents=True)
    path = scan_source(staging / "source.ply")
    monkeypatch.setattr(module, "MAX_RETAINED_BYTES", module.MAX_IMPORT_BYTES)
    with pytest.raises(ModelMatchingError) as caught:
        module.import_reference_version(tmp_path, **import_request(path))
    assert caught.value.code == "reference_storage_limit"


def test_new_version_preserves_old_and_validates_predecessor(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    source = scan_source(tmp_path / "source.ply")
    first = module.import_reference_version(tmp_path, **import_request(source))
    second = module.import_reference_version(tmp_path, **import_request(source, version_id="v2", supersedes_version_id="v1"))
    assert second["supersedes_version_id"] == "v1"
    assert load_model_version(tmp_path, "scan-pump", "v1") == first


def test_invalid_format_has_a_terminal_audit_and_retains_rejected_source(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    path = tmp_path / "bad.ply"
    path.write_bytes(b"not a point file")
    request = import_request(path)
    with pytest.raises(ModelMatchingError) as caught:
        module.import_reference_version(tmp_path, **request)
    assert caught.value.code == "reference_format_invalid"
    assert load_operation(tmp_path, request["operation_id"])["status"] == "failed"
    with pytest.raises(ModelMatchingError) as repeated:
        module.import_reference_version(tmp_path, **request)
    assert repeated.value.code == caught.value.code
    directory = tmp_path / "models/scan-pump/versions/v1"
    assert (directory / "rejection.json").exists()
    assert any((directory / "source").iterdir())
    assert not (directory / "commit.json").exists()


def test_changed_request_cannot_reuse_original_operation(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    module.import_reference_version(tmp_path, **request)
    request["declared_unit"] = "mm"
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    assert load_model_version(tmp_path, "scan-pump", "v1")["declared_unit"] == "m"


@pytest.mark.parametrize("field", ["operation_id", "request_id", "principal"])
def test_same_idempotency_key_cannot_alias_another_operation_or_actor(tmp_path, monkeypatch, field):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    real = module.decode_reference_file
    monkeypatch.setattr(module, "decode_reference_file", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    monkeypatch.setattr(module, "decode_reference_file", real)
    request[field] = Principal("bob", frozenset({"expert"}), "configured_token") if field == "principal" else "alias"
    with pytest.raises(ModelMatchingError) as caught:
        module.import_reference_version(tmp_path, **request)
    assert caught.value.code in {"reference_operation_identity_conflict", "audit_integrity_error"}
    assert load_operation(tmp_path, "import-scan-pump-v1")["status"] == "running"


def test_parallel_quota_reservations_allow_only_one_pending_version(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    module = importer()
    scan_asset(tmp_path)
    source = scan_source(tmp_path / "source.ply")
    waiting, release = threading.Event(), threading.Event()
    decode = module.decode_reference_file
    def held(*args, **kwargs):
        waiting.set()
        assert release.wait(5)
        return decode(*args, **kwargs)
    monkeypatch.setattr(module, "MAX_RETAINED_BYTES", module.MAX_IMPORT_BYTES)
    monkeypatch.setattr(module, "decode_reference_file", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(module.import_reference_version, tmp_path, **import_request(source))
        assert waiting.wait(5)
        try:
            with pytest.raises(ModelMatchingError) as caught:
                module.import_reference_version(tmp_path, **import_request(source, version_id="v2"))
            assert caught.value.code == "reference_storage_limit"
        finally:
            release.set()
        assert future.result()["version_id"] == "v1"


def test_existing_retrieval_directories_are_not_mistaken_for_assets(tmp_path):
    module = importer()
    scan_asset(tmp_path)
    (tmp_path / "models/retrieval_configs").mkdir()
    (tmp_path / "models/feature_indexes").mkdir()
    assert module.import_reference_version(tmp_path, **import_request(scan_source(tmp_path / "source.ply")))["point_count"] == 64


def test_corrupt_unfinished_owner_does_not_terminalize_original_operation(tmp_path, monkeypatch):
    module = importer()
    scan_asset(tmp_path)
    request = import_request(scan_source(tmp_path / "source.ply"))
    real = module.decode_reference_file
    monkeypatch.setattr(module, "decode_reference_file", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    path = tmp_path / "models/scan-pump/versions/v1/owner.json"
    original = path.read_bytes()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ModelMatchingError):
        module.import_reference_version(tmp_path, **request)
    assert load_operation(tmp_path, request["operation_id"])["status"] == "running"
    path.write_bytes(original)
    monkeypatch.setattr(module, "decode_reference_file", real)
    assert module.import_reference_version(tmp_path, **request)["point_count"] == 64


def test_quota_scan_cannot_release_reservation_using_precommit_byte_count(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    module = importer()
    scan_asset(tmp_path)
    source = scan_source(tmp_path / "source.ply")
    waiting, release = threading.Event(), threading.Event()
    decode, measure = module.decode_reference_file, module._tree_bytes
    def held(*args, **kwargs):
        waiting.set()
        assert release.wait(5)
        return decode(*args, **kwargs)
    monkeypatch.setattr(module, "decode_reference_file", held)
    directory = tmp_path / "models/scan-pump/versions/v1"
    main_thread = threading.get_ident()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(module.import_reference_version, tmp_path, **import_request(source))
        assert waiting.wait(5)
        def race(path):
            actual = measure(path)
            if path == directory and threading.get_ident() == main_thread:
                release.set()
                assert future.result(timeout=5)["version_id"] == "v1"
            return actual
        monkeypatch.setattr(module, "_tree_bytes", race)
        try:
            with model_resource_lock(tmp_path, "reference-quota"):
                usage = module._retained_usage(tmp_path)
            assert usage >= module.MAX_IMPORT_BYTES
        finally:
            release.set()
