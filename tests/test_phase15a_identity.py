import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import (
    Principal,
    parse_principal_bindings,
    require_any_role,
    resolve_principal,
)


def test_production_principal_comes_from_server_binding():
    bindings = parse_principal_bindings(
        '{"token-a":{"actor_id":"alice","roles":["expert","operator"]}}'
    )
    principal = resolve_principal(
        run_mode="production",
        token="token-a",
        actor_header="mallory",
        roles_header="approver",
        bindings=bindings,
    )
    assert principal == Principal(
        actor_id="alice",
        roles=frozenset({"expert", "operator"}),
        source="configured_token",
    )


def test_development_headers_are_marked_and_validated():
    principal = resolve_principal(
        run_mode="development",
        token=None,
        actor_header="dev-user",
        roles_header="expert,operator",
        bindings={},
    )
    assert principal.source == "development_headers"
    assert principal.roles == frozenset({"expert", "operator"})


def test_missing_production_binding_is_denied():
    with pytest.raises(ModelMatchingError) as exc_info:
        resolve_principal(
            run_mode="production",
            token="unknown",
            actor_header="alice",
            roles_header="expert",
            bindings={},
        )
    assert exc_info.value.code == "permission_denied"


def test_role_check_requires_one_allowed_role():
    principal = Principal("alice", frozenset({"operator"}), "configured_token")
    with pytest.raises(ModelMatchingError) as exc_info:
        require_any_role(principal, {"expert", "approver"})
    assert exc_info.value.code == "permission_denied"


class _TextSubclass(str):
    pass


@pytest.mark.parametrize(
    ("actor_id", "roles", "source"),
    [
        ("../alice", frozenset({"expert"}), "configured_token"),
        ("alice", {"expert"}, "configured_token"),
        ("alice", frozenset({"owner"}), "configured_token"),
        (
            "alice",
            frozenset({_TextSubclass("expert")}),
            "configured_token",
        ),
        ("alice", frozenset({"expert"}), "browser_headers"),
        (
            "alice",
            frozenset({"expert"}),
            _TextSubclass("configured_token"),
        ),
        ("alice", frozenset(), "configured_token"),
    ],
)
def test_direct_principal_construction_rejects_untrusted_state(
    actor_id, roles, source
):
    with pytest.raises(ValueError):
        Principal(actor_id, roles, source)


def test_system_principal_may_have_empty_roles():
    principal = Principal("system-audit", frozenset(), "system")

    assert principal.roles == frozenset()


@pytest.mark.parametrize(
    "roles",
    [
        frozenset({"expert"}),
        frozenset({"operator", "auditor"}),
    ],
)
def test_system_principal_rejects_all_authority_roles(roles):
    with pytest.raises(ValueError):
        Principal("system-api", roles, "system")
