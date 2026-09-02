"""不可变绑定结构与单链投影，不执行文件读写。"""

import copy
import hashlib
import math
import re

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_registration_transform import validate_rigid_transform
from pc_system.model_sampling import _canonical_json_bytes


_IDENTITY = ("asset_id", "source_id", "instance_id")
_SOURCE = ("object_fingerprint", "model_id", "model_version_id", "representation_id",
           "retrieval_run_id", "registration_id", "registration_fingerprint", "rigid_transform_4x4")
_FIELDS = frozenset({
    "schema_version", "binding_id", "case_id", *_IDENTITY, *_SOURCE,
    "decision_id", "verification_scope", "created_by", "created_at", "transition",
    "supersedes_binding_id", "restores_binding_id", "operation_id",
})
_MATRIX_POLICY = {
    "homogeneous_tolerance": 1e-8, "orthogonality_tolerance": 1e-6,
    "determinant_tolerance": 1e-6, "singular_value_tolerance": 1e-6,
    "maximum_translation_m": float("inf"), "maximum_rotation_rad": math.pi,
}


def _invalid(message: str) -> ModelMatchingError:
    return ModelMatchingError("binding_chain_invalid", message)


def _hash(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _invalid("Binding fingerprint is invalid.")
    return value


def _id(value: object) -> str:
    if type(value) is not str:
        raise _invalid("Binding identifier is invalid.")
    try:
        return validate_identifier(value)
    except ValueError as exc:
        raise _invalid("Binding identifier is invalid.") from exc


def _matrix(value: object) -> list:
    try:
        if (type(value) is not list or len(value) != 4 or any(
            type(row) is not list or len(row) != 4 or any(
                type(number) not in {int, float} or not math.isfinite(number) for number in row
            ) for row in value
        )):
            raise _invalid("Binding transform must contain finite numbers in a 4x4 matrix.")
        validate_rigid_transform(value, _MATRIX_POLICY)
    except (ModelMatchingError, OverflowError, ValueError) as exc:
        raise _invalid("Binding transform must be rigid.") from exc
    return copy.deepcopy(value)


def _validate_binding(binding: dict) -> dict:
    if type(binding) is not dict or set(binding) != _FIELDS or binding.get("schema_version") != "1.0":
        raise _invalid("Binding schema is invalid.")
    for key in (*_IDENTITY, "binding_id", "model_id", "model_version_id", "representation_id",
                "retrieval_run_id", "registration_id", "decision_id", "created_by", "operation_id"):
        _id(binding[key])
    for key in ("case_id", "object_fingerprint", "registration_fingerprint"):
        _hash(binding[key])
    if type(binding["verification_scope"]) is not str or binding["verification_scope"] not in {"identity", "operational_pose", "expert_pose"}:
        raise _invalid("Binding verification scope is invalid.")
    if type(binding["created_at"]) is not str or not binding["created_at"]:
        raise _invalid("Binding creation time is invalid.")
    _matrix(binding["rigid_transform_4x4"])
    transition = binding["transition"]
    if type(transition) is not str:
        raise _invalid("Binding transition must be text.")
    previous, restored = binding["supersedes_binding_id"], binding["restores_binding_id"]
    if transition == "create":
        if previous is not None or restored is not None:
            raise _invalid("A first binding must not have predecessors.")
    elif transition in {"supersede", "restore"}:
        _id(previous)
        if previous == binding["binding_id"]:
            raise _invalid("Binding cannot supersede itself.")
        if transition == "restore":
            _id(restored)
        elif restored is not None:
            raise _invalid("Only restore may reference a historical target.")
    else:
        raise _invalid("Unknown binding transition.")
    return copy.deepcopy(binding)


def build_model_binding(*, binding_id: str, decision: dict, registration: dict,
                        transition: str, current_binding: dict | None,
                        restores_binding: dict | None) -> dict:
    if decision.get("decision") != "confirmed":
        raise _invalid("Only confirmed decisions can create a binding.")
    roles = decision.get("decider_roles", [])
    if not isinstance(roles, list) or not set(roles).intersection({"operator", "expert"}):
        raise _invalid("Binding decision lacks a business role.")
    if (registration.get("status") != "completed" or not (
        registration.get("gate_status") == "passed"
        or (registration.get("gate_status") == "review_required" and "expert" in roles)
    )):
        raise ModelMatchingError("registration_not_eligible", "Registration cannot form a binding.")
    if decision.get("verification_scope") == "expert_pose" and "expert" not in roles:
        raise _invalid("Expert verification requires an expert decision.")
    for key in (*_IDENTITY, "object_fingerprint", "retrieval_run_id", "registration_id", "candidate_rank"):
        if key not in decision or type(decision[key]) is not type(registration.get(key)) or decision[key] != registration.get(key):
            raise ModelMatchingError("registration_not_eligible", "Binding and registration identities differ.")
    if transition == "create":
        if current_binding is not None or restores_binding is not None:
            raise ModelMatchingError("binding_exists", "Create cannot replace an existing binding.")
    elif transition in {"supersede", "restore"}:
        if "expert" not in roles or current_binding is None:
            raise _invalid("An expert and current binding are required for a transition.")
        _validate_binding(current_binding)
        if any(current_binding[key] != decision[key] for key in _IDENTITY):
            raise _invalid("A transition cannot cross objects.")
    else:
        raise _invalid("Unknown binding transition.")
    result = {
        "schema_version": "1.0", "binding_id": binding_id,
        **{key: decision[key] for key in (*_IDENTITY, "case_id", "object_fingerprint", "decision_id",
                                        "retrieval_run_id", "registration_id", "verification_scope", "operation_id")},
        "model_id": registration["candidate_model_id"],
        "model_version_id": registration["candidate_version_id"],
        "representation_id": registration["candidate_representation_id"],
        "registration_fingerprint": registration["report_fingerprint"],
        "rigid_transform_4x4": _matrix(registration["rigid_transform_4x4"]),
        "created_by": decision["decided_by"], "created_at": decision["decided_at"],
        "transition": transition,
        "supersedes_binding_id": None if current_binding is None else current_binding["binding_id"],
        "restores_binding_id": None if restores_binding is None else restores_binding["binding_id"],
    }
    if transition == "restore":
        if restores_binding is None:
            raise _invalid("Restore requires a historical target.")
        _validate_binding(restores_binding)
        if any(restores_binding[key] != result[key] for key in (*_IDENTITY, *_SOURCE)):
            raise _invalid("Restored binding differs from its historical source.")
    return _validate_binding(result)


def project_binding_chain(bindings: list[dict], *, current_object_fingerprint: str) -> dict:
    _hash(current_object_fingerprint)
    if type(bindings) is not list:
        raise _invalid("Bindings must be a collection.")
    if not bindings:
        return dict(current_binding=None, current_status=None, binding_head_fingerprint=None, history=[])
    by_id, children, roots = {}, {}, []
    identity = None
    for raw in bindings:
        binding = _validate_binding(raw)
        object_identity = tuple(binding[key] for key in _IDENTITY)
        if identity is not None and object_identity != identity:
            raise _invalid("Binding chain crosses object identities.")
        identity = object_identity
        if binding["binding_id"] in by_id:
            raise _invalid("Duplicate binding identity.")
        by_id[binding["binding_id"]] = binding
        parent = binding["supersedes_binding_id"]
        if parent is None:
            roots.append(binding["binding_id"])
        elif parent in children:
            raise _invalid("Binding chain has a fork.")
        else:
            children[parent] = binding["binding_id"]
    if len(roots) != 1 or any(parent not in by_id for parent in children):
        raise _invalid("Binding chain has multiple roots, a cycle or a missing predecessor.")
    ordered, seen, next_id = [], set(), roots[0]
    while next_id is not None:
        if next_id in seen:
            raise _invalid("Binding chain contains a cycle.")
        binding = by_id[next_id]
        restored = binding["restores_binding_id"]
        if restored is not None and (restored not in seen or any(
            binding[key] != by_id[restored][key] for key in _SOURCE
        )):
            raise _invalid("Restore target is not an identical historical source.")
        seen.add(next_id)
        ordered.append(binding)
        next_id = children.get(next_id)
    if len(seen) != len(by_id):
        raise _invalid("Binding chain has unreachable entries.")
    head = ordered[-1]
    status = "active" if head["object_fingerprint"] == current_object_fingerprint else "stale"
    return {
        "current_binding": copy.deepcopy(head), "current_status": status,
        "binding_head_fingerprint": hashlib.sha256(_canonical_json_bytes(head)).hexdigest(),
        "history": [{**item, "status": status if item["binding_id"] == head["binding_id"] else "superseded"}
                    for item in reversed(ordered)],
    }


def binding_head_fingerprint(projection: dict) -> str | None:
    return projection["binding_head_fingerprint"]
