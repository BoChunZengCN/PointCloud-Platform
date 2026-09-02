"""从已验证配准与不可变决定生成清单，不持久化可变队列。"""

import base64
import json
import math
from datetime import datetime
from pathlib import Path

from pc_system.model_binding import project_binding_chain
from pc_system.model_match_decision import (
    _decision_projection, _digest, _identifier, _registered_reports,
    compute_case_id, compute_case_revision, compute_evidence_fingerprint,
    list_decision_bundles, load_decision_context, resolve_decision_case_identity,
)
from pc_system.model_matching_audit import read_verified_operation_snapshot
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role


_IDENTITY = ("asset_id", "source_id", "instance_id", "retrieval_run_id", "object_fingerprint")


def _invalid(message):
    return ModelMatchingError("decision_not_allowed", message)


def _time(value):
    if type(value) is not str:
        raise _invalid("Time filter must be an ISO 8601 string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise _invalid("Time filter must include a valid timezone.") from exc


def _project(root: Path, identity: dict, reports: list[dict], principal: Principal, *, detail=False) -> dict:
    context = load_decision_context(root, **{key: identity[key] for key in _IDENTITY if key != "object_fingerprint"})
    case_id = compute_case_id(**identity)
    bundles = list_decision_bundles(root, **{key: identity[key] for key in ("asset_id", "source_id", "instance_id")})
    head = _decision_projection(bundles, case_id)
    evidence = compute_evidence_fingerprint(reports)
    bindings = project_binding_chain([b["binding"] for b in bundles if b["binding"] is not None],
                                      current_object_fingerprint=context["object_fingerprint"])
    status = "pending"
    if identity["object_fingerprint"] != context["object_fingerprint"] or bindings["current_status"] == "stale":
        status = "stale"
    elif head is not None and head["decision"] in {"confirmed", "no_match"} and head["evidence_fingerprint"] == evidence:
        status = "processed"
    rejected = {b["decision"]["registration_id"] for b in bundles
                if b["decision"]["case_id"] == case_id and b["decision"]["decision"] == "rejected"}
    expert = "expert" in principal.roles
    can_write = bool(principal.roles.intersection({"operator", "expert"}))
    current = bindings["current_binding"]
    candidates = []
    for report in sorted(reports, key=lambda item: (item["candidate_rank"], item["registration_id"])):
        eligible = report["registration_id"] not in rejected and report["gate_status"] in {"passed", "review_required"}
        actions = []
        if can_write and status == "pending" and eligible:
            if (expert or report["gate_status"] == "passed") and current is None:
                actions.append("confirm")
            actions.append("reject")
        candidates.append({
            "registration_id": report["registration_id"], "candidate_rank": report["candidate_rank"],
            "model_id": report["candidate_model_id"], "model_version_id": report["candidate_version_id"],
            "gate_status": report["gate_status"], "human_rejected": report["registration_id"] in rejected,
            "generated_at": report["generated_at"], "available_actions": actions,
        })
    actions = []
    if can_write and status == "pending":
        for action in ("confirm", "reject"):
            if any(action in item["available_actions"] for item in candidates):
                actions.append(action)
        actions.append("no_match")
    if expert and identity["object_fingerprint"] == context["object_fingerprint"]:
        actions.append("rerun")
        if current is not None:
            if any(not c["human_rejected"] and c["gate_status"] in {"passed", "review_required"} for c in candidates):
                actions.append("supersede")
            if any(item["binding_id"] != current["binding_id"] and item["object_fingerprint"] == context["object_fingerprint"] for item in bindings["history"]):
                actions.append("restore")
    timestamps = [report["generated_at"] for report in reports]
    if head is not None:
        timestamps.append(head["decided_at"])
    updated_at = max(timestamps, key=_time)
    result = {
        "viewer_role": "expert" if expert else "operator" if "operator" in principal.roles else "auditor",
        "case_id": case_id, "status": status,
        "case_revision": compute_case_revision(context["object_fingerprint"], evidence,
                                                None if head is None else _digest(head), bindings["binding_head_fingerprint"]),
        "object": {**context["object"], **identity}, "retrieval_run_id": identity["retrieval_run_id"],
        "candidate_summary": candidates, "available_actions": actions, "updated_at": updated_at,
        "decision_summary": None if head is None else {key: head[key] for key in (
            "decision_id", "decision", "decided_by", "decided_at", "decision_reason", "verification_scope"
        )},
        "binding_summary": None if current is None else {
            **{key: current[key] for key in ("binding_id", "model_id", "model_version_id", "verification_scope")},
            "status": bindings["current_status"],
        },
    }
    if detail and principal.roles.intersection({"expert", "auditor"}):
        decisions = [b["decision"] for b in bundles if b["decision"]["case_id"] == case_id]
        operation_ids = sorted({r["operation_id"] for r in reports} | {d["operation_id"] for d in decisions})
        audit = []
        for operation_id in operation_ids:
            snapshot = read_verified_operation_snapshot(root, operation_id)
            audit.append({"operation_id": operation_id, "status": snapshot["operation"]["status"],
                          "events": [{key: event[key] for key in ("event_type", "timestamp", "event_hash")} for event in snapshot["events"]]})
        result["technical"] = {"registrations": reports, "decisions": decisions,
                                "binding_history": bindings["history"], "audit": audit}
    return result


def list_model_decision_items(project_root: Path, *, principal: Principal, status: str = "all",
                              asset_id=None, class_id=None, gate_status=None, decided_by=None,
                              started_at=None, ended_at=None, limit: int = 50, cursor=None) -> dict:
    require_any_role(principal, {"operator", "expert", "auditor"})
    if type(status) is not str or status not in {"all", "pending", "processed", "stale"}:
        raise _invalid("Decision status filter is invalid.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _invalid("Decision page limit must be an integer from 1 to 100.")
    for key, value in (("asset_id", asset_id), ("class_id", class_id), ("decided_by", decided_by)):
        if value is not None:
            _identifier(value, key)
    if gate_status is not None and (type(gate_status) is not str or gate_status not in {"passed", "review_required", "rejected"}):
        raise _invalid("Registration gate filter is invalid.")
    start, end = None if started_at is None else _time(started_at), None if ended_at is None else _time(ended_at)
    if start is not None and end is not None and start > end:
        raise _invalid("Decision time interval is reversed.")
    filters = dict(status=status, asset_id=asset_id, class_id=class_id, gate_status=gate_status,
                   decided_by=decided_by, started_at=started_at, ended_at=ended_at)
    digest, after = _digest(filters), None
    if cursor is not None:
        try:
            if type(cursor) is not str or len(cursor) > 2048:
                raise ValueError("cursor length")
            decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if set(decoded) != {"filters", "after"} or decoded["filters"] != digest:
                raise ValueError("cursor filters")
            after = tuple(decoded["after"])
            if len(after) != 2 or type(after[0]) not in {int, float} or not math.isfinite(after[0]) or type(after[1]) is not str:
                raise ValueError("cursor sort key")
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError) as exc:
            raise _invalid("Decision cursor is invalid or belongs to different filters.") from exc
    root, groups = Path(project_root), {}
    for report in _registered_reports(root, (asset_id, None, None, None)):
        identity = {key: report[key] for key in _IDENTITY}
        groups.setdefault(compute_case_id(**identity), (identity, []))[1].append(report)
    items, counts = [], dict(pending=0, processed=0, stale=0, all=0)
    for identity, reports in groups.values():
        item = _project(root, identity, reports, principal)
        time = _time(item["updated_at"])
        if ((class_id is not None and item["object"]["class_id"] != class_id)
                or (gate_status is not None and not any(c["gate_status"] == gate_status for c in item["candidate_summary"]))
                or (decided_by is not None and (item["decision_summary"] or {}).get("decided_by") != decided_by)
                or (start is not None and time < start) or (end is not None and time > end)):
            continue
        counts[item["status"]] += 1
        counts["all"] += 1
        if status == "all" or item["status"] == status:
            items.append(item)
    key = lambda item: (-_time(item["updated_at"]), item["case_id"])
    items.sort(key=key)
    if after is not None:
        items = [item for item in items if key(item) > after]
    page, next_cursor = items[:limit], None
    if len(items) > limit:
        next_cursor = base64.urlsafe_b64encode(json.dumps({"filters": digest, "after": key(page[-1])}).encode()).decode()
    return {"items": page, "next_cursor": next_cursor, "counts": counts}


def load_model_decision_item(project_root: Path, *, case_id: str, principal: Principal) -> dict:
    require_any_role(principal, {"operator", "expert", "auditor"})
    identity = resolve_decision_case_identity(project_root, case_id)
    reports = [report for report in _registered_reports(Path(project_root), tuple(identity[k] for k in ("asset_id", "source_id", "instance_id")) + (None,))
               if all(report[key] == identity[key] for key in _IDENTITY)]
    return _project(Path(project_root), identity, reports, principal, detail=True)


def project_current_decision_state(project_root: Path, *, asset_id: str, source_id: str,
                                   instance_id: str, retrieval_run_id: str, principal: Principal) -> dict:
    context = load_decision_context(project_root, asset_id=asset_id, source_id=source_id,
                                    instance_id=instance_id, retrieval_run_id=retrieval_run_id)
    return load_model_decision_item(project_root, case_id=context["case_id"], principal=principal)


def crop_model_binding(binding: dict | None, *, principal: Principal) -> dict | None:
    require_any_role(principal, {"operator", "expert", "auditor"})
    if binding is None or principal.roles.intersection({"expert", "auditor"}):
        return binding
    fields = {"binding_id", "model_id", "model_version_id", "asset_id", "source_id", "instance_id",
              "verification_scope", "created_at", "created_by", "supersedes_binding_id", "restores_binding_id", "status"}
    return {key: value for key, value in binding.items() if key in fields}


def load_model_bindings(project_root: Path, *, asset_id: str, source_id: str, instance_id: str,
                        principal: Principal, include_history: bool = False) -> dict:
    require_any_role(principal, {"operator", "expert", "auditor"})
    identity = dict(asset_id=asset_id, source_id=source_id, instance_id=instance_id)
    for key, value in identity.items():
        _identifier(value, key)
    bindings = [bundle["binding"] for bundle in list_decision_bundles(project_root, **identity)
                if bundle["binding"] is not None]
    fingerprint = "0" * 64
    if bindings:
        context = load_decision_context(project_root, **identity, retrieval_run_id=bindings[0]["retrieval_run_id"])
        fingerprint = context["object_fingerprint"]
    projection = project_binding_chain(bindings, current_object_fingerprint=fingerprint)
    result = {"current_binding": crop_model_binding(projection["current_binding"], principal=principal),
              "current_status": projection["current_status"],
              "binding_head_fingerprint": projection["binding_head_fingerprint"]}
    if include_history:
        result["history"] = [crop_model_binding(binding, principal=principal) for binding in projection["history"]]
    return result
