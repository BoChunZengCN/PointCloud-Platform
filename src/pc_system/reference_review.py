"""扫描模板的一次性专家核验及可追溯发布门禁。"""

import json
from pathlib import Path

from .identifiers import validate_identifier
from .model_import import _copy_json_value
from .model_matching_audit import start_operation, ensure_operation_event, complete_operation, fail_operation, read_verified_operation_snapshot
from .model_matching_errors import ModelMatchingError
from .model_matching_identity import require_any_role
from .model_resource_lock import model_resource_lock
from .reference_store import audit_digest, digest, load_bundle, plain, publish_json, read_json


BASE_ACKNOWLEDGEMENTS = frozenset({"single_object", "metadata_and_rights", "coverage_limitations"})
_OWNER_FIELDS = {"schema_version", "operation_id", "request_id", "request_fingerprint", "idempotency_key_hash",
                 "actor_id", "created_at", "request", "version_manifest_fingerprint", "quality_fingerprint"}


def _error(code, message):
    return ModelMatchingError(code, message)


def _integrity():
    return _error("reference_review_integrity_error", "核验的版本、所有者、最终决定或审计证据不一致。")


def _capture_request(values):
    try:
        captured = _copy_json_value(values, depth=0, budget=[0])
        if len(json.dumps(captured, ensure_ascii=False).encode("utf-8")) > 16384:
            raise ValueError("request too large")
        return {"schema_id": "reference_version.review", "schema_version": "1.0", "request": captured}
    except (TypeError, ValueError, UnicodeError):
        return {"schema_id": "reference_version.review", "schema_version": "1.0", "capture_error": "invalid_request"}


def _normalize(request):
    try:
        if type(request) is not dict or set(request) != {"model_id", "version_id", "decision", "reason", "acknowledgements"}:
            raise ValueError("invalid request")
        model_id = validate_identifier(request["model_id"], "model_id")
        version_id = validate_identifier(request["version_id"], "version_id")
        decision, reason, acknowledgements = request["decision"], request["reason"], request["acknowledgements"]
        if type(decision) is not str or decision not in {"approved", "rejected"}:
            raise ValueError("invalid decision")
        if type(reason) is not str or not reason.strip() or len(reason) > 1000 or "\0" in reason:
            raise ValueError("invalid reason")
        reason.encode("utf-8")
        if type(acknowledgements) is not list or len(acknowledgements) > 32 or any(type(code) is not str or not code or len(code) > 128 for code in acknowledgements):
            raise ValueError("invalid acknowledgements")
        return {"model_id": model_id, "version_id": version_id, "decision": decision, "reason": reason.strip(),
                "acknowledgements": sorted(set(acknowledgements))}
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _error("reference_review_invalid", "核验身份、决定、原因或确认码无效。") from exc


def _directory(root, model_id, version_id):
    model_id = validate_identifier(model_id, "model_id")
    version_id = validate_identifier(version_id, "version_id")
    return Path(root) / "models" / model_id / "reviews" / version_id


def _check_decision(request, bundle):
    quality = bundle["quality"]
    warnings = set(quality["warning_codes"])
    confirmed = set(request["acknowledgements"])
    if confirmed - (BASE_ACKNOWLEDGEMENTS | warnings):
        raise _error("reference_review_invalid", "确认码不属于本版本的核验声明或风险。")
    if request["decision"] == "approved":
        if quality["status"] == "rejected" or quality["rejection_codes"]:
            raise _error("reference_quality_rejected", "自动检查不通过的扫描版本不能批准。")
        if not (BASE_ACKNOWLEDGEMENTS | warnings) <= confirmed:
            raise _error("reference_acknowledgements_required", "通过核验前须确认三项基础声明及全部自动风险。")


def _context(root, directory, owner):
    try:
        if type(owner) is not dict or set(owner) != _OWNER_FIELDS or owner["schema_version"] != "1.0":
            raise _integrity()
        request = _normalize(owner["request"])
        if directory != _directory(root, request["model_id"], request["version_id"]):
            raise _integrity()
        snapshot = read_verified_operation_snapshot(root, owner["operation_id"])
        op, events = snapshot["operation"], snapshot["events"]
        if (op["operation_type"] != "reference_version.review"
                or any(owner[key] != op[key] for key in ("operation_id", "request_id", "request_fingerprint", "idempotency_key_hash", "actor_id"))
                or audit_digest(_capture_request(owner["request"])) != op["request_fingerprint"]
                or owner["created_at"] != events[0]["timestamp"]):
            raise _integrity()
        bundle = load_bundle(root, request["model_id"], request["version_id"])
        if (owner["version_manifest_fingerprint"] != digest(bundle["manifest"])
                or owner["quality_fingerprint"] != digest(bundle["quality"])):
            raise _integrity()
        _check_decision(request, bundle)
        return request, snapshot
    except (TypeError, ValueError, KeyError) as exc:
        raise _integrity() from exc


def _review_value(owner, request):
    return {"schema_version": "1.0", **request, "review_id": owner["operation_id"], "operation_id": owner["operation_id"],
            "reviewed_by": owner["actor_id"], "reviewed_at": owner["created_at"],
            "version_manifest_fingerprint": owner["version_manifest_fingerprint"], "quality_fingerprint": owner["quality_fingerprint"]}


def _commit_value(owner, review):
    return {"schema_version": "1.0", "owner_fingerprint": digest(owner), "review_fingerprint": digest(review)}


def _events(owner, review):
    return [("reference.review_owned", {"owner_fingerprint": digest(owner)}),
            ("reference.version_reviewed", {"review_id": review["review_id"], "review_fingerprint": digest(review), "decision": review["decision"]})]


def load_reference_review(root, model_id, version_id):
    root = Path(root)
    # 即使尚无核验，也先确认被查询的是完整扫描版本。
    load_bundle(root, model_id, version_id)
    directory = _directory(root, model_id, version_id)
    try:
        if not directory.parent.exists():
            return None
        plain(directory.parent, directory=True)
        if not directory.exists():
            return None
        plain(directory, directory=True)
        if not any(directory.iterdir()):
            return None
        owner = read_json(directory / "owner.json")
        request, snapshot = _context(root, directory, owner)
        expected = _review_value(owner, request)
        review, commit = read_json(directory / "review.json"), read_json(directory / "commit.json")
        if review != expected or commit != _commit_value(owner, expected):
            raise _integrity()
        for event_type, details in _events(owner, review):
            matching = [event for event in snapshot["events"] if event["event_type"] == event_type]
            if len(matching) != 1 or matching[0]["details"] != details:
                raise _integrity()
        op = snapshot["operation"]
        if op["status"] != "completed" or op["result"] != {"review_fingerprint": digest(review)}:
            raise _error("publication_recovery_required", "专家核验尚未完成审计提交。")
        return review
    except FileNotFoundError as exc:
        raise _error("publication_recovery_required", "核验包尚未完整提交，请由原操作恢复。") from exc
    except (TypeError, ValueError, KeyError, OSError) as exc:
        raise _integrity() from exc


def review_reference_version(root, *, model_id, version_id, decision, reason, acknowledgements,
                             principal, operation_id, request_id, idempotency_key):
    root = Path(root)
    payload = _capture_request(dict(model_id=model_id, version_id=version_id, decision=decision, reason=reason, acknowledgements=acknowledgements))
    operation, replayed = start_operation(root, operation_id=operation_id, operation_type="reference_version.review", principal=principal,
                                         request_id=request_id, idempotency_key=idempotency_key, request_payload=payload)
    opid = operation["operation_id"]
    if (opid, operation["request_id"], operation["actor_id"]) != (operation_id, request_id, principal.actor_id):
        raise _error("reference_operation_identity_conflict", "核验恢复必须沿用原操作、请求和操作人身份。")
    if operation["status"] == "failed":
        raise _error(operation["error"]["code"], operation["error"]["message"])
    try:
        require_any_role(principal, {"expert"})
        request = _normalize(payload.get("request"))
    except ModelMatchingError as exc:
        if not replayed:
            fail_operation(root, opid, exc.code, str(exc))
        raise
    directory = _directory(root, request["model_id"], request["version_id"])
    with model_resource_lock(root, "reference-review", request["model_id"], request["version_id"]):
        owner = None
        # 上游证据也可能暂时损坏；检查它之前先保留已有操作/目录的恢复资格。
        uncertain = replayed or directory.exists()
        try:
            bundle = load_bundle(root, request["model_id"], request["version_id"])
            if directory.parent.exists():
                plain(directory.parent, directory=True)
            if directory.exists():
                plain(directory, directory=True)
                has_artifacts = bool(list(directory.iterdir()))
                uncertain = uncertain or has_artifacts
                if (directory / "owner.json").exists():
                    found = read_json(directory / "owner.json")
                    _, snapshot = _context(root, directory, found)
                    if found["operation_id"] != opid:
                        # 只有充分验证过的外来 owner，才可结束当前新操作。
                        if snapshot["operation"]["status"] == "completed":
                            load_reference_review(root, request["model_id"], request["version_id"])
                        uncertain = False
                        code = "reference_review_exists" if snapshot["operation"]["status"] == "completed" else "reference_review_owned"
                        raise _error(code, "该版本已有最终核验或由其他操作持有，不能改票或接管。")
                    owner = found
                elif has_artifacts:
                    raise _integrity()
            if owner is None:
                if operation["status"] != "running":
                    raise _integrity()
                _check_decision(request, bundle)
                snapshot = read_verified_operation_snapshot(root, opid)
                proposed = {"schema_version": "1.0", **{key: operation[key] for key in ("operation_id", "request_id", "request_fingerprint", "idempotency_key_hash", "actor_id")},
                            "created_at": snapshot["events"][0]["timestamp"], "request": payload["request"],
                            "version_manifest_fingerprint": digest(bundle["manifest"]), "quality_fingerprint": digest(bundle["quality"])}
                directory.parent.mkdir(exist_ok=True)
                plain(directory.parent, directory=True)
                directory.mkdir(exist_ok=True)
                plain(directory, directory=True)
                # 发布可能在落盘后抛错，因此发布前即进入不确定分支。
                uncertain = True
                publish_json(directory / "owner.json", proposed)
                owner = proposed
            normalized, snapshot = _context(root, directory, owner)
            if snapshot["operation"]["status"] == "completed":
                return load_reference_review(root, request["model_id"], request["version_id"])
            review = _review_value(owner, normalized)
            event_type, details = _events(owner, review)[0]
            ensure_operation_event(root, opid, event_type, details)
            publish_json(directory / "review.json", review)
            event_type, details = _events(owner, review)[1]
            ensure_operation_event(root, opid, event_type, details)
            publish_json(directory / "commit.json", _commit_value(owner, review))
            complete_operation(root, opid, {"review_fingerprint": digest(review)})
            return load_reference_review(root, request["model_id"], request["version_id"])
        except Exception as exc:
            if owner is not None or uncertain:
                if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
                    raise
                raise _error("publication_recovery_required", "核验状态需恢复，请保留原身份和工件重试。") from exc
            error = exc if isinstance(exc, ModelMatchingError) else _error("reference_review_failed", "核验未能开始。")
            if operation["status"] == "running":
                fail_operation(root, opid, error.code, str(error))
            raise error from exc


def approved_review_evidence(root, model_id, version_id):
    review = load_reference_review(root, model_id, version_id)
    if review is None or review["decision"] != "approved":
        raise _error("reference_review_required", "发布或回滚扫描模板前必须完成专家通过核验。")
    return {"review_id": review["review_id"], "review_fingerprint": digest(review)}
