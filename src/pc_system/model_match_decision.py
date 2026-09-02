"""人工决策的纯契约；持久化与服务入口随后在此接入。"""

import hashlib
import copy
import json
import os
import re
import stat
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_sampling import _canonical_json_bytes
from pc_system.model_sampling import _publish_exact_json
from pc_system.model_matching_audit import read_verified_operation_snapshot, ensure_operation_event
from pc_system.model_binding import build_model_binding, project_binding_chain
from pc_system.model_registration import load_model_registration
from pc_system.model_registration_input import _load_model_evidence, _required_candidate
from pc_system.model_retrieval import load_model_retrieval
from pc_system.model_retrieval_input import _reload_retrieval_object


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DECISIONS = frozenset({"confirmed", "rejected", "no_match"})
_SCOPES = frozenset({"identity", "operational_pose", "expert_pose"})


def _error(code: str, message: str) -> ModelMatchingError:
    return ModelMatchingError(code, message)


def _identifier(value: object, label: str) -> str:
    try:
        if type(value) is not str:
            raise ValueError(label)
        return validate_identifier(value, label)
    except ValueError as exc:
        raise _error("decision_not_allowed", f"Invalid {label}.") from exc


def _fingerprint(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise _error("artifact_integrity_failed", "Invalid decision fingerprint.")
    return value


def _digest(value: dict) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def compute_case_id(asset_id: str, source_id: str, instance_id: str,
                    object_fingerprint: str, retrieval_run_id: str) -> str:
    return _digest({
        "asset_id": _identifier(asset_id, "asset_id"),
        "source_id": _identifier(source_id, "source_id"),
        "instance_id": _identifier(instance_id, "instance_id"),
        "object_fingerprint": _fingerprint(object_fingerprint),
        "retrieval_run_id": _identifier(retrieval_run_id, "retrieval_run_id"),
    })


def compute_evidence_fingerprint(registrations: list[dict]) -> str:
    if type(registrations) is not list:
        raise _error("artifact_integrity_failed", "Invalid registration evidence collection.")
    evidence, identifiers = [], set()
    for report in registrations:
        if type(report) is not dict:
            raise _error("artifact_integrity_failed", "Invalid registration evidence.")
        registration_id = _identifier(report.get("registration_id"), "registration_id")
        rank = report.get("candidate_rank")
        if type(rank) is not int or rank < 1 or registration_id in identifiers:
            raise _error("artifact_integrity_failed", "Duplicate or invalid registration evidence.")
        identifiers.add(registration_id)
        evidence.append({
            "registration_id": registration_id, "candidate_rank": rank,
            "report_fingerprint": _fingerprint(report.get("report_fingerprint")),
        })
    evidence.sort(key=lambda item: (item["candidate_rank"], item["registration_id"], item["report_fingerprint"]))
    return _digest({"registrations": evidence})


def compute_case_revision(object_fingerprint: str, evidence_fingerprint: str,
                          decision_head_fingerprint: str | None,
                          binding_head_fingerprint: str | None) -> str:
    return _digest({
        "object_fingerprint": _fingerprint(object_fingerprint),
        "evidence_fingerprint": _fingerprint(evidence_fingerprint),
        "decision_head_fingerprint": _fingerprint(decision_head_fingerprint, nullable=True),
        "binding_head_fingerprint": _fingerprint(binding_head_fingerprint, nullable=True),
    })


def normalize_decision_request(*, decision_id: str, case_id: str, decision: str,
                               decision_reason: str, verification_scope: str,
                               registration_id: str | None, candidate_rank: int | None,
                               expected_case_revision: str, binding_id: str | None) -> dict:
    _identifier(decision_id, "decision_id")
    _fingerprint(case_id)
    _fingerprint(expected_case_revision)
    if type(decision) is not str or decision not in _DECISIONS:
        raise _error("decision_not_allowed", "Unknown decision action.")
    if type(verification_scope) is not str or verification_scope not in _SCOPES:
        raise _error("decision_not_allowed", "Unknown verification scope.")
    if type(decision_reason) is not str:
        raise _error("decision_reason_invalid", "Decision reason must be text.")
    reason = decision_reason.strip()
    try:
        reason.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error("decision_reason_invalid", "Decision reason is not valid Unicode.") from exc
    if not reason or len(decision_reason) > 1000 or "\x00" in decision_reason:
        raise _error("decision_reason_invalid", "Decision reason must contain 1-1000 characters without NUL.")
    if decision == "no_match":
        if registration_id is not None or candidate_rank is not None:
            raise _error("decision_not_allowed", "No-match must not reference a registration.")
    else:
        _identifier(registration_id, "registration_id")
        if type(candidate_rank) is not int or candidate_rank < 1:
            raise _error("decision_not_allowed", "Candidate rank must be a positive integer.")
    if decision == "confirmed":
        _identifier(binding_id, "binding_id")
    elif binding_id is not None:
        raise _error("decision_not_allowed", "Only confirmed decisions create a binding.")
    return {
        "decision_id": decision_id, "case_id": case_id, "decision": decision,
        "decision_reason": reason, "verification_scope": verification_scope,
        "registration_id": registration_id, "candidate_rank": candidate_rank,
        "expected_case_revision": expected_case_revision, "binding_id": binding_id,
    }


def require_decision_allowed(principal: Principal, *, decision: str,
                             gate_status: str | None, verification_scope: str) -> None:
    if type(principal) is not Principal:
        raise _error("permission_denied", "A trusted principal is required.")
    require_any_role(principal, {"operator", "expert"})
    if type(decision) is not str or decision not in _DECISIONS:
        raise _error("decision_not_allowed", "Unknown decision action.")
    if type(verification_scope) is not str or verification_scope not in _SCOPES:
        raise _error("decision_not_allowed", "Unknown verification scope.")
    if verification_scope == "expert_pose" and "expert" not in principal.roles:
        raise _error("decision_not_allowed", "Expert pose requires an expert.")
    if decision == "confirmed" and not (
        gate_status == "passed" or (gate_status == "review_required" and "expert" in principal.roles)
    ):
        raise _error("decision_not_allowed", "Registration cannot be confirmed by this principal.")


def build_match_decision(*, request: dict, context: dict, operation: dict,
                         principal: Principal, previous_decision_id: str | None) -> dict:
    request = normalize_decision_request(**request)
    identity = {key: context[key] for key in (
        "asset_id", "source_id", "instance_id", "object_fingerprint", "retrieval_run_id"
    )}
    if (compute_case_id(**identity) != request["case_id"]
            or context["case_id"] != request["case_id"]
            or compute_case_revision(context["object_fingerprint"], context["evidence_fingerprint"],
                                     context["decision_head_fingerprint"], context["binding_head_fingerprint"])
            != request["expected_case_revision"]
            or previous_decision_id != context["decision_head_id"]):
        raise _error("decision_conflict", "Decision does not match its authoritative context.")
    if previous_decision_id is not None:
        _identifier(previous_decision_id, "previous_decision_id")
    if (previous_decision_id is None) != (context["decision_head_fingerprint"] is None):
        raise _error("artifact_integrity_failed", "Decision predecessor is incomplete.")
    registration = context["registrations_by_id"].get(request["registration_id"])
    if request["decision"] != "no_match" and (
        registration is None or registration["candidate_rank"] != request["candidate_rank"]
    ):
        raise _error("registration_not_eligible", "Registration does not match the chosen candidate.")
    require_decision_allowed(principal, decision=request["decision"],
                             gate_status=None if registration is None else registration["gate_status"],
                             verification_scope=request["verification_scope"])
    if (operation["actor_id"] != principal.actor_id or operation["roles"] != sorted(principal.roles)
            or operation["principal_source"] != principal.source):
        raise _error("permission_denied", "Decision principal differs from the audit owner.")
    _identifier(operation["operation_id"], "operation_id")
    return {
        "schema_version": "1.0", **identity,
        **{key: request[key] for key in (
            "decision_id", "case_id", "decision", "decision_reason", "verification_scope",
            "registration_id", "candidate_rank", "expected_case_revision",
        )},
        "evidence_fingerprint": context["evidence_fingerprint"],
        "decided_by": principal.actor_id, "decider_roles": sorted(principal.roles),
        "decided_at": operation["started_event_at"],
        "previous_decision_id": previous_decision_id,
        "previous_decision_head_fingerprint": context["decision_head_fingerprint"],
        "expected_decision_head_fingerprint": context["decision_head_fingerprint"],
        "expected_binding_head_fingerprint": context["binding_head_fingerprint"],
        "operation_id": operation["operation_id"],
    }


_OBJECT_KEYS = ("asset_id", "source_id", "instance_id")
_IDENTITY_KEYS = (*_OBJECT_KEYS, "retrieval_run_id", "object_fingerprint")
_OWNER_FIELDS = frozenset({
    "schema_version", "operation_id", "request_id", "request_fingerprint",
    "idempotency_key_hash", "request", "identity", "frozen", "transition",
})
_COMMIT_FIELDS = frozenset({
    "schema_version", "decision_id", "decision_sha256", "owner_sha256", "binding_id",
    "binding_sha256", "case_id", "object_fingerprint", "evidence_fingerprint",
    "operation_id", "audit_event_hashes", "result_fingerprint",
})
_MAX_JSON_BYTES = 16 * 1024 * 1024


def _integrity(message: str) -> ModelMatchingError:
    return _error("artifact_integrity_failed", message)


def _plain_info(path: Path, *, directory: bool):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise _integrity("Decision storage contains a non-plain filesystem object.")
    return info


def _plain_path(root: Path, parts: tuple, *, create: bool = False) -> Path:
    path = Path(root)
    _plain_info(path, directory=True)
    for part in parts:
        path = path / part
        if create:
            path.mkdir(exist_ok=True)
        _plain_info(path, directory=True)
    return path


def _strict_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _read_decision_json(path: Path) -> dict:
    """对已验证目录中的文件检查类型、描述符身份和规范原始字节。"""
    descriptor = None
    try:
        before = _plain_info(path, directory=False)
        if before.st_size > _MAX_JSON_BYTES:
            raise ValueError("oversized decision artifact")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("decision file identity changed")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(_MAX_JSON_BYTES + 1)
        after = _plain_info(path, directory=False)
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise ValueError("decision file changed while reading")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs)
        if type(value) is not dict or len(raw) > _MAX_JSON_BYTES or raw != _canonical_json_bytes(value):
            raise ValueError("decision artifact is not canonical")
        return value
    except FileNotFoundError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise _integrity("Decision artifact cannot be verified.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _scan_artifact_directories(root: Path, kind: str, filters: tuple = (None,) * 4) -> list[Path]:
    try:
        parent = _plain_path(root, ("reports", kind))
    except FileNotFoundError:
        return []
    paths = [parent]
    for selected in filters:
        following = []
        if selected is not None:
            _identifier(selected, "artifact_identity")
        for path in paths:
            for child in sorted(path.iterdir()):
                _plain_info(child, directory=True)
                _identifier(child.name, "artifact_identity")
                if selected is None or child.name == selected:
                    following.append(child)
        paths = following
    return paths


def _bundle_directory(root: Path, identity: dict, decision_id: str, *, create=False) -> Path:
    parts = tuple(_identifier(identity[key], key) for key in _OBJECT_KEYS)
    return _plain_path(root, ("reports", "model_match_decisions", *parts, _identifier(decision_id, "decision_id")), create=create)


def _audit_request_hash(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, ensure_ascii=False, allow_nan=False,
                                     sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _registration_evidence(root: Path, identity: dict, registration_id: str) -> dict:
    report = load_model_registration(root, **{key: identity[key] for key in _OBJECT_KEYS}, registration_id=registration_id)
    if report["status"] != "completed":
        raise _error("registration_not_eligible", "Failed registration is not decision evidence.")
    retrieval = load_model_retrieval(root, **{key: report[key] for key in (*_OBJECT_KEYS, "retrieval_run_id")})
    candidate = _required_candidate(retrieval, report["candidate_rank"])
    mapping = {"model_id": "candidate_model_id", "version_id": "candidate_version_id",
               "representation_id": "candidate_representation_id", "release_id": "candidate_release_id",
               "representation_fingerprint": "candidate_representation_fingerprint"}
    if any(candidate[key] != report[target] for key, target in mapping.items()) or retrieval["object_fingerprint"] != report["object_fingerprint"]:
        raise _integrity("Registration and retrieval evidence differ.")
    _load_model_evidence(root, candidate)
    return report


def _registered_reports(root: Path, filters=(None,) * 4) -> list[dict]:
    reports = []
    for path in _scan_artifact_directories(root, "model_registrations", filters):
        asset, source, instance, registration_id = path.parts[-4:]
        try:
            _plain_info(path / "registration_report.json", directory=False)
        except FileNotFoundError:
            continue
        report = load_model_registration(root, asset_id=asset, source_id=source, instance_id=instance, registration_id=registration_id)
        if report["status"] == "completed":
            reports.append(report)
    return reports


def _decision_projection(bundles: list[dict], case_id: str) -> dict | None:
    decisions = [bundle["decision"] for bundle in bundles if bundle["decision"]["case_id"] == case_id]
    if not decisions:
        return None
    by_id, children = {}, {}
    roots = []
    for item in decisions:
        key, parent = item["decision_id"], item["previous_decision_id"]
        if key in by_id or (parent is not None and parent in children):
            raise _integrity("Decision history forks or repeats an identity.")
        by_id[key] = item
        if parent is None:
            roots.append(key)
        else:
            children[parent] = key
    if len(roots) != 1 or any(key not in by_id for key in children):
        raise _integrity("Decision history is not a single complete chain.")
    seen, key, head = set(), roots[0], None
    while key is not None:
        if key in seen:
            raise _integrity("Decision history contains a cycle.")
        item = by_id[key]
        expected = None if head is None else _digest(head)
        if item["previous_decision_head_fingerprint"] != expected or item["expected_decision_head_fingerprint"] != expected:
            raise _integrity("Decision predecessor fingerprint differs.")
        seen.add(key)
        head = item
        key = children.get(key)
    if len(seen) != len(decisions):
        raise _integrity("Decision history has unreachable records.")
    return head


def load_decision_context(project_root: Path, *, asset_id: str, source_id: str,
                          instance_id: str, retrieval_run_id: str) -> dict:
    root = Path(project_root)
    identity = dict(asset_id=asset_id, source_id=source_id, instance_id=instance_id, retrieval_run_id=retrieval_run_id)
    for key, value in identity.items():
        _identifier(value, key)
    retrieval = load_model_retrieval(root, **identity)
    observed = _reload_retrieval_object(root, **{key: identity[key] for key in _OBJECT_KEYS}, source_kind=retrieval["source_kind"])
    identity["object_fingerprint"] = observed["object_fingerprint"]
    case_id = compute_case_id(**identity)
    reports = [_registration_evidence(root, identity, report["registration_id"])
               for report in _registered_reports(root, (asset_id, source_id, instance_id, None))
               if report["retrieval_run_id"] == retrieval_run_id and report["object_fingerprint"] == observed["object_fingerprint"]]
    bundles = list_decision_bundles(root, asset_id=asset_id, source_id=source_id, instance_id=instance_id)
    head = _decision_projection(bundles, case_id)
    binding = project_binding_chain([b["binding"] for b in bundles if b["binding"] is not None], current_object_fingerprint=observed["object_fingerprint"])
    result = {
        **identity, "case_id": case_id, "evidence_fingerprint": compute_evidence_fingerprint(reports),
        "decision_head_id": None if head is None else head["decision_id"],
        "decision_head_fingerprint": None if head is None else _digest(head),
        "decision_head": head, "binding_head_fingerprint": binding["binding_head_fingerprint"],
        "current_binding": binding["current_binding"], "binding_status": binding["current_status"],
        "registrations_by_id": {item["registration_id"]: item for item in reports},
        "object": {key: value for key, value in observed.items() if key != "points"},
    }
    result["case_revision"] = compute_case_revision(result["object_fingerprint"], result["evidence_fingerprint"],
                                                     result["decision_head_fingerprint"], result["binding_head_fingerprint"])
    return result


def resolve_decision_case_identity(project_root: Path, case_id: str) -> dict:
    _fingerprint(case_id)
    matches = []
    for report in _registered_reports(Path(project_root)):
        identity = {key: report[key] for key in _IDENTITY_KEYS}
        if compute_case_id(**identity) == case_id:
            matches.append(identity)
    if not matches:
        for bundle in list_decision_bundles(project_root):
            identity = {key: bundle["decision"][key] for key in _IDENTITY_KEYS}
            if compute_case_id(**identity) == case_id:
                matches.append(identity)
    if not matches:
        raise _error("decision_item_not_found", "Decision case does not exist.")
    if any(item != matches[0] for item in matches):
        raise _integrity("Decision case identity is ambiguous.")
    return matches[0]


def _operation_context(root: Path, operation_id: str) -> dict:
    snapshot = read_verified_operation_snapshot(root, operation_id)
    return {**snapshot["operation"], "started_event_at": snapshot["events"][0]["timestamp"]}


def _frozen_context(root: Path, owner: dict, operation: dict) -> dict:
    try:
        if (set(owner) != _OWNER_FIELDS or owner["schema_version"] != "1.0"
                or any(owner[key] != operation[key] for key in ("operation_id", "request_id", "request_fingerprint", "idempotency_key_hash"))
                or _audit_request_hash(owner["request"]) != operation["request_fingerprint"]):
            raise _integrity("Decision owner differs from its audit envelope.")
        request = normalize_decision_request(**owner["request"])
        if request != owner["request"] or compute_case_id(**owner["identity"]) != request["case_id"]:
            raise _integrity("Decision owner identity is invalid.")
        frozen = owner["frozen"]
        expected_fields = {"registrations", "decision_head_id", "decision_head_fingerprint", "binding_head_fingerprint", "current_binding", "restores_binding"}
        if type(frozen) is not dict or set(frozen) != expected_fields:
            raise _integrity("Frozen decision context is invalid.")
        registrations = []
        for evidence in frozen["registrations"]:
            report = _registration_evidence(root, owner["identity"], evidence["registration_id"])
            if any(report[key] != value for key, value in evidence.items()) or any(report[key] != owner["identity"][key] for key in _IDENTITY_KEYS):
                raise _integrity("Frozen registration differs from its source.")
            registrations.append(report)
        current = frozen["current_binding"]
        if (None if current is None else _digest(current)) != frozen["binding_head_fingerprint"]:
            raise _integrity("Frozen binding head fingerprint differs.")
        result = {
            **owner["identity"], **frozen, "case_id": request["case_id"], "request": request,
            "evidence_fingerprint": compute_evidence_fingerprint(registrations),
            "registrations_by_id": {item["registration_id"]: item for item in registrations},
        }
        result["case_revision"] = compute_case_revision(result["object_fingerprint"], result["evidence_fingerprint"],
                                                        result["decision_head_fingerprint"], result["binding_head_fingerprint"])
        if result["case_revision"] != request["expected_case_revision"]:
            raise _integrity("Frozen evidence does not reproduce the submitted revision.")
        if operation["operation_type"] != "model_match.decision" or owner["transition"] != ("create" if request["decision"] == "confirmed" else None):
            raise _integrity("Decision transition differs from its audit action.")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise _integrity("Frozen decision owner cannot be validated.") from exc


def _prepare_decision_owner_locked(project_root: Path, *, request: dict, context: dict,
                                   operation: dict, principal: Principal, transition: str | None,
                                   restores_binding: dict | None) -> dict:
    root = Path(project_root)
    build_match_decision(request=request, context=context, operation=operation, principal=principal,
                         previous_decision_id=context["decision_head_id"])
    if transition == "create" and context["current_binding"] is not None:
        raise _error("binding_exists", "Create cannot replace an existing binding.")
    owner = {
        "schema_version": "1.0", **{key: operation[key] for key in ("operation_id", "request_id", "request_fingerprint", "idempotency_key_hash")},
        "request": copy.deepcopy(request), "identity": {key: context[key] for key in _IDENTITY_KEYS},
        "transition": transition,
        "frozen": {
            "registrations": [{key: item[key] for key in ("registration_id", "candidate_rank", "report_fingerprint")}
                              for item in sorted(context["registrations_by_id"].values(), key=lambda item: item["registration_id"])],
            **{key: copy.deepcopy(context[key]) for key in ("decision_head_id", "decision_head_fingerprint", "binding_head_fingerprint", "current_binding")},
            "restores_binding": copy.deepcopy(restores_binding),
        },
    }
    _frozen_context(root, owner, operation)
    directory = _bundle_directory(root, context, request["decision_id"], create=True)
    _publish_exact_json(directory / "owner.json", owner, conflict_code="operation_busy", conflict_message="Decision owner conflicts.")
    return owner


def _business_events(root: Path, owner: dict, decision: dict, binding: dict | None, *, publish: bool) -> list[dict]:
    details = {"owner_sha256": _digest(owner), "decision_sha256": _digest(decision),
               "binding_sha256": None if binding is None else _digest(binding),
               "decision_id": decision["decision_id"], "case_id": decision["case_id"]}
    kinds = ["match.decision_" + decision["decision"]]
    if binding is not None:
        kinds.append("model_binding.created")
    if publish:
        return [ensure_operation_event(root, owner["operation_id"], kind, details) for kind in kinds]
    snapshot = read_verified_operation_snapshot(root, owner["operation_id"])
    selected = []
    for kind in kinds:
        events = [event for event in snapshot["events"] if event["event_type"] == kind]
        if len(events) != 1 or events[0]["details"] != details:
            raise _integrity("Decision business audit event differs.")
        selected.append(events[0])
    return selected


def _publish_decision_bundle_locked(project_root: Path, *, owner: dict, operation: dict,
                                    decision: dict, binding: dict | None) -> dict:
    root = Path(project_root)
    context = _frozen_context(root, owner, operation)
    principal = Principal(operation["actor_id"], frozenset(operation["roles"]), operation["principal_source"])
    expected = build_match_decision(request=context["request"], context=context, operation=operation,
                                    principal=principal, previous_decision_id=context["decision_head_id"])
    expected_binding = None
    if expected["decision"] == "confirmed":
        expected_binding = build_model_binding(binding_id=context["request"]["binding_id"], decision=expected,
            registration=context["registrations_by_id"][expected["registration_id"]], transition=owner["transition"],
            current_binding=context["current_binding"], restores_binding=context["restores_binding"])
    if decision != expected or binding != expected_binding:
        raise _integrity("Decision publication differs from the frozen request.")
    directory = _bundle_directory(root, owner["identity"], decision["decision_id"])
    if _read_decision_json(directory / "owner.json") != owner:
        raise _integrity("Persisted owner differs.")
    for name, value in (("decision.json", decision), ("binding.json", binding)):
        if value is not None:
            _publish_exact_json(directory / name, value, conflict_code="artifact_integrity_failed", conflict_message="Decision artifact conflicts.")
        elif (directory / name).exists():
            raise _integrity("Non-confirmed decision must not have a binding.")
    events = _business_events(root, owner, decision, binding, publish=True)
    commit = {"schema_version": "1.0", "decision_id": decision["decision_id"],
              "owner_sha256": _digest(owner), "decision_sha256": _digest(decision),
              "binding_id": None if binding is None else binding["binding_id"],
              "binding_sha256": None if binding is None else _digest(binding),
              **{key: decision[key] for key in ("case_id", "object_fingerprint", "evidence_fingerprint", "operation_id")},
              "audit_event_hashes": [event["event_hash"] for event in events]}
    commit["result_fingerprint"] = _digest(commit)
    _publish_exact_json(directory / "commit.json", commit, conflict_code="artifact_integrity_failed", conflict_message="Decision commit conflicts.")
    return commit


def _load_bundle(root: Path, directory: Path) -> dict | None:
    try:
        commit = _read_decision_json(directory / "commit.json")
    except FileNotFoundError:
        return None
    try:
        owner = _read_decision_json(directory / "owner.json")
        decision = _read_decision_json(directory / "decision.json")
        if set(commit) != _COMMIT_FIELDS or commit["schema_version"] != "1.0":
            raise _integrity("Decision commit schema differs.")
        binding = None if commit["binding_id"] is None else _read_decision_json(directory / "binding.json")
        if binding is None and (directory / "binding.json").exists():
            raise _integrity("Unexpected binding artifact.")
        if (commit["owner_sha256"] != _digest(owner) or commit["decision_sha256"] != _digest(decision)
                or commit["binding_sha256"] != (None if binding is None else _digest(binding))
                or commit["result_fingerprint"] != _digest({k: v for k, v in commit.items() if k != "result_fingerprint"})
                or any(commit[key] != decision[key] for key in ("case_id", "decision_id", "object_fingerprint", "evidence_fingerprint", "operation_id"))
                or tuple(decision[key] for key in (*_OBJECT_KEYS, "decision_id")) != directory.parts[-4:]):
            raise _integrity("Decision commit fingerprints or path identity differ.")
        operation = _operation_context(root, owner["operation_id"])
        context = _frozen_context(root, owner, operation)
        principal = Principal(operation["actor_id"], frozenset(operation["roles"]), operation["principal_source"])
        expected = build_match_decision(request=context["request"], context=context, operation=operation,
                                        principal=principal, previous_decision_id=context["decision_head_id"])
        if expected != decision:
            raise _integrity("Decision differs from its frozen audit owner.")
        expected_binding = None
        if decision["decision"] == "confirmed":
            expected_binding = build_model_binding(binding_id=context["request"]["binding_id"], decision=decision,
                registration=context["registrations_by_id"][decision["registration_id"]], transition=owner["transition"],
                current_binding=context["current_binding"], restores_binding=context["restores_binding"])
        if expected_binding != binding or (None if binding is None else binding["binding_id"]) != commit["binding_id"]:
            raise _integrity("Binding differs from its decision source.")
        events = _business_events(root, owner, decision, binding, publish=False)
        if [event["event_hash"] for event in events] != commit["audit_event_hashes"]:
            raise _integrity("Decision commit audit references differ.")
        if operation["status"] == "running":
            return None
        if operation["status"] != "completed" or operation["result"] != {"result_fingerprint": commit["result_fingerprint"]}:
            raise _integrity("Decision commit audit result differs.")
        return {"decision": decision, "binding": binding, "commit": commit}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise _integrity("Decision bundle is incomplete or invalid.") from exc


def list_decision_bundles(project_root: Path, *, asset_id=None, source_id=None, instance_id=None) -> list[dict]:
    root = Path(project_root)
    result = []
    for directory in _scan_artifact_directories(root, "model_match_decisions", (asset_id, source_id, instance_id, None)):
        bundle = _load_bundle(root, directory)
        if bundle is not None:
            result.append(bundle)
    return result


def load_decision_bundle(project_root: Path, *, asset_id: str, source_id: str,
                         instance_id: str, decision_id: str) -> dict:
    root = Path(project_root)
    identity = dict(asset_id=asset_id, source_id=source_id, instance_id=instance_id)
    try:
        result = _load_bundle(root, _bundle_directory(root, identity, decision_id))
    except FileNotFoundError as exc:
        raise _error("decision_not_found", "Decision bundle does not exist.") from exc
    if result is None:
        raise _error("publication_recovery_required", "Decision bundle is not publicly committed.")
    return result


def load_operation_decision_result(project_root: Path, operation: dict) -> dict:
    bundles = [bundle for bundle in list_decision_bundles(project_root)
               if bundle["decision"]["operation_id"] == operation["operation_id"]]
    if len(bundles) != 1:
        raise _integrity("Completed decision operation does not have exactly one bundle.")
    return bundles[0]


def _inspect_object_decision_writes_locked(project_root: Path, *, identity: dict, operation: dict) -> dict | None:
    root, pending = Path(project_root), []
    filters = tuple(identity[key] for key in _OBJECT_KEYS) + (None,)
    for directory in _scan_artifact_directories(root, "model_match_decisions", filters):
        if _load_bundle(root, directory) is not None:
            continue
        try:
            owner = _read_decision_json(directory / "owner.json")
        except FileNotFoundError:
            if list(directory.iterdir()):
                raise _integrity("Decision artifacts have no owner.")
            continue
        observed_operation = _operation_context(root, owner["operation_id"])
        _frozen_context(root, owner, observed_operation)
        if tuple(owner["identity"][key] for key in _OBJECT_KEYS) + (owner["request"]["decision_id"],) != directory.parts[-4:]:
            raise _integrity("Unfinished decision owner differs from its directory identity.")
        if observed_operation["status"] != "running":
            raise _integrity("Terminal decision operation has an incomplete bundle.")
        pending.append(owner)
    if len(pending) > 1:
        raise _integrity("Object has multiple unfinished decision owners.")
    if pending and pending[0]["operation_id"] != operation["operation_id"]:
        raise _error("publication_recovery_required", "Another decision operation requires recovery.")
    return pending[0] if pending else None


def _load_frozen_decision_context_locked(project_root: Path, *, owner: dict, operation: dict) -> dict:
    root = Path(project_root)
    context = _frozen_context(root, owner, operation)
    bundles = list_decision_bundles(root, **{key: owner["identity"][key] for key in _OBJECT_KEYS})
    head = _decision_projection(bundles, context["case_id"])
    binding = project_binding_chain([b["binding"] for b in bundles if b["binding"] is not None], current_object_fingerprint=context["object_fingerprint"])
    if ((None if head is None else _digest(head)) != context["decision_head_fingerprint"]
            or binding["binding_head_fingerprint"] != context["binding_head_fingerprint"]):
        raise _integrity("Recovery would cross a committed decision or binding successor.")
    return context
