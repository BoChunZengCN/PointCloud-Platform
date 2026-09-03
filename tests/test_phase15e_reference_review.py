"""专家核验权限、一次最终决定和不可变证据恢复。"""

import hashlib
import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from phase15e_support import prepared_scan, review_request
from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal


def reviewer():
    assert importlib.util.find_spec("pc_system.reference_review") is not None, "缺少专家核验服务"
    return importlib.import_module("pc_system.reference_review")


def test_approved_review_pins_version_and_quality_without_mutating_import(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    assert module.load_reference_review(tmp_path, "scan-pump", "v1") is None
    directory = tmp_path / "models/scan-pump/versions/v1"
    before = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    request = review_request()
    review = module.review_reference_version(tmp_path, **request)
    assert review["decision"] == "approved" and review["review_id"] == "review-1"
    assert review["version_manifest_fingerprint"] == hashlib.sha256(before["model_manifest.json"]).hexdigest()
    assert review["quality_fingerprint"] == hashlib.sha256(before["quality_report.json"]).hexdigest()
    assert review["reviewed_by"] == "alice"
    assert module.review_reference_version(tmp_path, **request) == review
    assert module.load_reference_review(tmp_path, "scan-pump", "v1") == review
    assert before == {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("changes,code", [
    ({"acknowledgements": []}, "reference_acknowledgements_required"),
    ({"acknowledgements": ["unknown"]}, "reference_review_invalid"),
    ({"reason": " "}, "reference_review_invalid"),
    ({"reason": "x" * 1001}, "reference_review_invalid"),
    ({"decision": "maybe"}, "reference_review_invalid"),
    ({"principal": Principal("bob", frozenset({"operator"}), "configured_token")}, "permission_denied"),
])
def test_invalid_or_unauthorized_review_is_audited_without_a_final_decision(tmp_path, changes, code):
    module = reviewer()
    prepared_scan(tmp_path)
    with pytest.raises(ModelMatchingError) as caught:
        module.review_reference_version(tmp_path, **review_request(**changes))
    assert caught.value.code == code
    assert load_operation(tmp_path, "review-1")["status"] == "failed"
    assert module.load_reference_review(tmp_path, "scan-pump", "v1") is None


def test_hard_failed_quality_cannot_be_approved_but_can_be_rejected(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path, kind="small")
    with pytest.raises(ModelMatchingError) as caught:
        module.review_reference_version(tmp_path, **review_request())
    assert caught.value.code == "reference_quality_rejected"
    review = module.review_reference_version(tmp_path, **review_request(sequence="2", decision="rejected", acknowledgements=[], reason="点数不足，重新扫描"))
    assert review["decision"] == "rejected"


def test_quality_warnings_must_be_acknowledged_explicitly(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path, kind="plane")
    with pytest.raises(ModelMatchingError) as caught:
        module.review_reference_version(tmp_path, **review_request())
    assert caught.value.code == "reference_acknowledgements_required"
    request = review_request(sequence="2")
    request["acknowledgements"].append("geometry_planar")
    assert module.review_reference_version(tmp_path, **request)["decision"] == "approved"


def test_final_decision_cannot_be_revised_by_a_new_operation(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    first = module.review_reference_version(tmp_path, **review_request(decision="rejected", acknowledgements=[], reason="型号不符"))
    with pytest.raises(ModelMatchingError) as caught:
        module.review_reference_version(tmp_path, **review_request(sequence="2"))
    assert caught.value.code == "reference_review_exists"
    assert module.load_reference_review(tmp_path, "scan-pump", "v1") == first


def test_concurrent_reviews_publish_only_one_final_decision(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    def submit(sequence):
        try:
            return module.review_reference_version(tmp_path, **review_request(sequence=sequence))
        except ModelMatchingError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["1", "2"]))
    assert sum(type(value) is dict for value in results) == 1
    assert "reference_review_exists" in results


def test_unfinished_review_is_hidden_and_only_original_operation_can_recover(tmp_path, monkeypatch):
    module = reviewer()
    prepared_scan(tmp_path)
    original = module.complete_operation
    monkeypatch.setattr(module, "complete_operation", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    with pytest.raises(ModelMatchingError):
        module.load_reference_review(tmp_path, "scan-pump", "v1")
    with pytest.raises(ModelMatchingError) as caught:
        module.review_reference_version(tmp_path, **review_request(sequence="2"))
    assert caught.value.code == "reference_review_owned"
    monkeypatch.setattr(module, "complete_operation", original)
    assert module.review_reference_version(tmp_path, **review_request())["decision"] == "approved"


@pytest.mark.parametrize("name", ["owner.json", "review.json", "commit.json"])
def test_corrupted_review_evidence_is_not_publicly_accepted(tmp_path, name):
    module = reviewer()
    prepared_scan(tmp_path)
    module.review_reference_version(tmp_path, **review_request())
    (tmp_path / "models/scan-pump/reviews/v1" / name).write_text("{}", encoding="utf-8")
    with pytest.raises(ModelMatchingError):
        module.load_reference_review(tmp_path, "scan-pump", "v1")


def test_corrupt_pending_owner_preserves_recoverable_operation(tmp_path, monkeypatch):
    module = reviewer()
    prepared_scan(tmp_path)
    real = module.complete_operation
    monkeypatch.setattr(module, "complete_operation", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    path = tmp_path / "models/scan-pump/reviews/v1/owner.json"
    original = path.read_bytes()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    assert load_operation(tmp_path, "review-1")["status"] == "running"
    path.write_bytes(original)
    monkeypatch.setattr(module, "complete_operation", real)
    assert module.review_reference_version(tmp_path, **review_request())["decision"] == "approved"


def test_damaged_version_during_review_recovery_keeps_original_operation_running(tmp_path, monkeypatch):
    module = reviewer()
    prepared_scan(tmp_path)
    real = module.complete_operation
    monkeypatch.setattr(module, "complete_operation", lambda *a, **k: (_ for _ in ()).throw(OSError("中断")))
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    path = tmp_path / "models/scan-pump/versions/v1/quality_report.json"
    original = path.read_bytes()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    assert load_operation(tmp_path, "review-1")["status"] == "running"
    path.write_bytes(original)
    monkeypatch.setattr(module, "complete_operation", real)
    assert module.review_reference_version(tmp_path, **review_request())["decision"] == "approved"


def test_owner_publication_response_loss_recovers_without_a_second_decision(tmp_path, monkeypatch):
    module = reviewer()
    prepared_scan(tmp_path)
    publish = module.publish_json
    def lost_response(path, value):
        publish(path, value)
        if path.name == "owner.json":
            raise OSError("所有者已落盘，返回前中断")
    monkeypatch.setattr(module, "publish_json", lost_response)
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    assert load_operation(tmp_path, "review-1")["status"] == "running"
    monkeypatch.setattr(module, "publish_json", publish)
    assert module.review_reference_version(tmp_path, **review_request())["review_id"] == "review-1"


def test_empty_directory_after_owner_write_failure_allows_original_retry(tmp_path, monkeypatch):
    module = reviewer()
    prepared_scan(tmp_path)
    publish = module.publish_json
    def interrupted(path, value):
        if path.name == "owner.json":
            raise OSError("创建目录后、写所有者前中断")
        publish(path, value)
    monkeypatch.setattr(module, "publish_json", interrupted)
    with pytest.raises(ModelMatchingError):
        module.review_reference_version(tmp_path, **review_request())
    directory = tmp_path / "models/scan-pump/reviews/v1"
    assert list(directory.iterdir()) == []
    assert load_operation(tmp_path, "review-1")["status"] == "running"
    monkeypatch.setattr(module, "publish_json", publish)
    assert module.review_reference_version(tmp_path, **review_request())["decision"] == "approved"


def test_same_idempotency_key_cannot_alias_a_new_review_operation(tmp_path):
    module = reviewer()
    prepared_scan(tmp_path)
    request = review_request()
    first = module.review_reference_version(tmp_path, **request)
    request["operation_id"] = "alias"
    with pytest.raises(ModelMatchingError) as caught:
        module.review_reference_version(tmp_path, **request)
    assert caught.value.code == "reference_operation_identity_conflict"
    assert module.load_reference_review(tmp_path, "scan-pump", "v1") == first
