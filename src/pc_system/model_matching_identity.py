import json
from dataclasses import dataclass

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError


ALLOWED_ROLES = frozenset({"operator", "expert", "approver", "auditor"})


@dataclass(frozen=True)
class Principal:
    actor_id: str
    roles: frozenset[str]
    source: str


def _principal(actor_id: str, roles: list[str] | set[str], source: str) -> Principal:
    actor_id = validate_identifier(actor_id, "actor_id")
    normalized = frozenset(str(role).strip() for role in roles if str(role).strip())
    if not normalized or not normalized <= ALLOWED_ROLES:
        raise ModelMatchingError("permission_denied", "Principal roles are invalid or empty.")
    return Principal(actor_id=actor_id, roles=normalized, source=source)


def parse_principal_bindings(raw: str | None) -> dict[str, Principal]:
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("PC_SYSTEM_PRINCIPALS_JSON must be a JSON object.")
    return {
        token: _principal(item["actor_id"], item["roles"], "configured_token")
        for token, item in payload.items()
    }


def resolve_principal(
    *,
    run_mode: str,
    token: str | None,
    actor_header: str | None,
    roles_header: str | None,
    bindings: dict[str, Principal],
) -> Principal:
    if token and token in bindings:
        return bindings[token]
    if run_mode == "development" and actor_header and roles_header:
        return _principal(actor_header, roles_header.split(","), "development_headers")
    raise ModelMatchingError("permission_denied", "A trusted Phase 15 principal is required.")


def require_any_role(principal: Principal, allowed: set[str]) -> None:
    if not principal.roles.intersection(allowed):
        raise ModelMatchingError("permission_denied", "Principal lacks a required role.")
