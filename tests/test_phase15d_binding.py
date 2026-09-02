import copy

import pytest

from pc_system.model_binding import build_model_binding, project_binding_chain, binding_head_fingerprint
from pc_system.model_matching_errors import ModelMatchingError


def registration():
    return dict(
        registration_id="registration-1", candidate_rank=1, status="completed", gate_status="passed",
        asset_id="asset-1", source_id="source-1", instance_id="instance-1",
        object_fingerprint="a" * 64, retrieval_run_id="retrieval-1",
        candidate_model_id="model-1", candidate_version_id="version-1",
        candidate_representation_id="representation-1", report_fingerprint="b" * 64,
        rigid_transform_4x4=[[1., 0., 0., 1.], [0., 1., 0., 2.], [0., 0., 1., 3.], [0., 0., 0., 1.]],
    )


def decision(**changes):
    return dict(
        dict(case_id="c" * 64, decision_id="decision-1", decision="confirmed",
             asset_id="asset-1", source_id="source-1", instance_id="instance-1",
             object_fingerprint="a" * 64, retrieval_run_id="retrieval-1",
             registration_id="registration-1", candidate_rank=1,
             verification_scope="operational_pose", decided_by="operator-a",
             decider_roles=["operator"], decided_at="2026-09-02T00:00:00Z", operation_id="op-1"),
        **changes,
    )


def create(binding_id="binding-1", *, current=None, restore=None, report=None):
    transition = "restore" if restore else "supersede" if current else "create"
    return build_model_binding(
        binding_id=binding_id, decision=decision(
            decision_id="decision-" + binding_id, operation_id="op-" + binding_id,
            decider_roles=["expert"] if current else ["operator"],
        ), registration=report or registration(), transition=transition,
        current_binding=current, restores_binding=restore,
    )


def test_binding_copies_verified_registration_without_aliasing():
    report = registration()
    bound = create(report=report)
    assert bound["model_version_id"] == "version-1"
    assert bound["representation_id"] == "representation-1"
    assert bound["registration_fingerprint"] == "b" * 64
    assert bound["rigid_transform_4x4"][0][3] == 1.0
    assert bound["supersedes_binding_id"] is None
    report["rigid_transform_4x4"][0][3] = 999
    assert bound["rigid_transform_4x4"][0][3] == 1.0


@pytest.mark.parametrize("changes", [
    {"gate_status": "rejected"}, {"status": "failed"},
    {"asset_id": "other"}, {"object_fingerprint": "d" * 64},
    {"retrieval_run_id": "other"}, {"candidate_rank": 2},
    {"candidate_rank": True},
    {"rigid_transform_4x4": [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]},
    {"rigid_transform_4x4": [[True, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]},
])
def test_binding_rejects_ineligible_or_mismatched_registration(changes):
    with pytest.raises(ModelMatchingError):
        create(report={**registration(), **changes})


def test_confirmed_and_expert_transition_rules_cannot_be_bypassed():
    first = create()
    for action, current, restore, chosen in [
        ("create", first, None, decision()),
        ("supersede", None, None, decision()),
        ("restore", first, None, decision(decider_roles=["expert"])),
        ("supersede", first, None, decision()),
        ("create", None, None, decision(decision="rejected")),
    ]:
        with pytest.raises(ModelMatchingError):
            build_model_binding(binding_id="binding-2", decision=chosen, registration=registration(),
                                transition=action, current_binding=current, restores_binding=restore)


def test_chain_orders_by_links_and_restore_creates_a_new_head():
    first = create()
    second = create("binding-2", current=first)
    third = create("binding-3", current=second, restore=first)
    projection = project_binding_chain([second, first, third], current_object_fingerprint="a" * 64)
    assert projection["current_binding"]["binding_id"] == "binding-3"
    assert projection["current_status"] == "active"
    assert [item["binding_id"] for item in projection["history"]] == ["binding-3", "binding-2", "binding-1"]
    assert [item["status"] for item in projection["history"]] == ["active", "superseded", "superseded"]
    stale = project_binding_chain([first, second, third], current_object_fingerprint="d" * 64)
    assert stale["current_status"] == "stale"
    assert binding_head_fingerprint(stale) == binding_head_fingerprint(projection)
    assert "status" not in first


@pytest.mark.parametrize("corruption", ["two_roots", "fork", "cycle", "missing", "duplicate", "foreign", "restore_future", "restore_matrix"])
def test_chain_fails_closed_on_invalid_graph_or_restore(corruption):
    first = create()
    second = create("binding-2", current=first)
    third = create("binding-3", current=second, restore=first)
    values = [first, second, third]
    if corruption == "two_roots":
        values.append(create("other"))
    elif corruption == "fork":
        third["supersedes_binding_id"] = first["binding_id"]
    elif corruption == "cycle":
        first["transition"] = "supersede"
        first["supersedes_binding_id"] = third["binding_id"]
    elif corruption == "missing":
        second["supersedes_binding_id"] = "absent"
    elif corruption == "duplicate":
        values.append(copy.deepcopy(first))
    elif corruption == "foreign":
        third["asset_id"] = "other"
    elif corruption == "restore_future":
        third["restores_binding_id"] = third["binding_id"]
    elif corruption == "restore_matrix":
        third["rigid_transform_4x4"][0][3] = 99
    with pytest.raises(ModelMatchingError) as caught:
        project_binding_chain(values, current_object_fingerprint="a" * 64)
    assert caught.value.code == "binding_chain_invalid"


def test_empty_chain_and_chain_across_object_versions():
    assert project_binding_chain([], current_object_fingerprint="a" * 64)["current_binding"] is None
    first = create()
    report = {**registration(), "object_fingerprint": "d" * 64}
    second = build_model_binding(
        binding_id="binding-2", decision=decision(object_fingerprint="d" * 64, decider_roles=["expert"]),
        registration=report, transition="supersede", current_binding=first, restores_binding=None,
    )
    assert project_binding_chain([first, second], current_object_fingerprint="d" * 64)["current_status"] == "active"


@pytest.mark.parametrize("field,value", [("verification_scope", []), ("transition", {}),
                                          ("rigid_transform_4x4", [[10 ** 1000] * 4] * 4)])
def test_malformed_json_values_produce_stable_chain_errors(field, value):
    binding = create()
    binding[field] = value
    with pytest.raises(ModelMatchingError) as caught:
        project_binding_chain([binding], current_object_fingerprint="a" * 64)
    assert caught.value.code == "binding_chain_invalid"
