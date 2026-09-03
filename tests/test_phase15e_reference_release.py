"""扫描发布及回滚必须固定已完成的专家核验证据。"""

import importlib

import pytest

from phase15e_support import prepared_scan, review_request, release_request
from pc_system.model_release import release_model_version, load_current_model_release, list_model_releases
from pc_system.model_matching_errors import ModelMatchingError


def reviewer():
    assert importlib.util.find_spec("pc_system.reference_review") is not None, "缺少专家核验服务"
    return importlib.import_module("pc_system.reference_review")


def test_scan_cannot_be_released_without_expert_review(tmp_path):
    prepared_scan(tmp_path)
    with pytest.raises(ModelMatchingError) as caught:
        release_model_version(tmp_path, **release_request())
    assert caught.value.code == "reference_review_required"
    assert load_current_model_release(tmp_path, "scan-pump") is None


def test_rejected_review_is_not_publishable(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    module.review_reference_version(tmp_path, **review_request(decision="rejected", acknowledgements=[], reason="型号标注不符"))
    with pytest.raises(ModelMatchingError) as caught:
        release_model_version(tmp_path, **release_request())
    assert caught.value.code == "reference_review_required"


def test_scanned_activate_upgrade_and_rollback_keep_version_and_review_history(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    prepared_scan(tmp_path, version_id="v2")
    review = module.review_reference_version(tmp_path, **review_request())
    module.review_reference_version(tmp_path, **review_request(version_id="v2", sequence="2"))
    model = tmp_path / "models/scan-pump"
    before = {p.relative_to(model).as_posix(): p.read_bytes() for base in (model / "versions", model / "reviews") for p in base.rglob("*") if p.is_file()}
    first = release_model_version(tmp_path, **release_request())
    assert first["schema_version"] == "1.1" and first["review_id"] == review["review_id"]
    assert len(first["review_fingerprint"]) == 64
    release_model_version(tmp_path, **release_request("2", version_id="v2", expected_current_release_id="release-1"))
    rollback = release_model_version(tmp_path, **release_request("3", action="rollback", expected_current_release_id="release-2", rollback_of_release_id="release-1"))
    assert rollback["version_id"] == "v1" and rollback["review_fingerprint"] == first["review_fingerprint"]
    assert load_current_model_release(tmp_path, "scan-pump") == rollback
    assert len(list_model_releases(tmp_path, "scan-pump")) == 3
    assert all((model / name).read_bytes() == value for name, value in before.items())
    assert not (tmp_path / "models/feature_indexes").exists()


def test_release_recovery_cannot_bypass_corrupted_review(tmp_path, monkeypatch):
    import pc_system.model_release as releases
    module = reviewer()
    prepared_scan(tmp_path)
    module.review_reference_version(tmp_path, **review_request())
    real = releases.complete_operation
    monkeypatch.setattr(releases, "complete_operation", lambda *a, **k: (_ for _ in ()).throw(OSError("模拟发布响应中断")))
    with pytest.raises(ModelMatchingError):
        releases.release_model_version(tmp_path, **release_request())
    path = tmp_path / "models/scan-pump/reviews/v1/review.json"
    original = path.read_bytes()
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(releases, "complete_operation", real)
    with pytest.raises(ModelMatchingError):
        releases.release_model_version(tmp_path, **release_request())
    with pytest.raises(ModelMatchingError):
        load_current_model_release(tmp_path, "scan-pump")
    path.write_bytes(original)
    assert releases.release_model_version(tmp_path, **release_request())["schema_version"] == "1.1"


def test_stale_scan_release_head_is_not_overwritten(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    module.review_reference_version(tmp_path, **review_request())
    first = release_model_version(tmp_path, **release_request())
    with pytest.raises(ModelMatchingError) as caught:
        release_model_version(tmp_path, **release_request("2"))
    assert caught.value.code == "stale_model_release"
    assert load_current_model_release(tmp_path, "scan-pump") == first


def test_completed_release_read_fails_if_expert_review_evidence_changes(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    module.review_reference_version(tmp_path, **review_request())
    release_model_version(tmp_path, **release_request())
    (tmp_path / "models/scan-pump/reviews/v1/review.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ModelMatchingError):
        load_current_model_release(tmp_path, "scan-pump")


def test_unfinished_expert_review_cannot_enable_release(tmp_path, monkeypatch):
    module = reviewer()
    prepared_scan(tmp_path)
    monkeypatch.setattr(module, "complete_operation", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    with pytest.raises(ModelMatchingError):
        release_model_version(tmp_path, **release_request())
    assert load_current_model_release(tmp_path, "scan-pump") is None
