import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_release_state import (
    ReleaseChain,
    ReleaseState,
    build_release_chain,
    classify_release_state,
)


OWNER = {
    "schema_version": "1.0",
    "model_id": "pump-a",
    "release_id": "release-002",
    "operation_id": "op-release-002",
    "request_id": "req-release-002",
    "request_fingerprint": "a" * 64,
}
RELEASE = {
    "schema_version": "1.0",
    "model_id": "pump-a",
    "release_id": "release-002",
    "version_id": "v2",
    "action": "activate",
    "previous_release_id": "release-001",
    "rollback_of_release_id": None,
    "reason": "Upgrade to v2",
    "operation_id": "op-release-002",
    "actor_id": "alice",
    "created_at": "2026-08-27T10:00:01+00:00",
    "version_manifest_fingerprint": "b" * 64,
}


def _release(
    release_id,
    previous,
    *,
    created_at="2026-08-27T10:00:00+00:00",
    version_id="v1",
    action="activate",
    rollback_of=None,
):
    return {
        "release_id": release_id,
        "previous_release_id": previous,
        "created_at": created_at,
        "version_id": version_id,
        "action": action,
        "rollback_of_release_id": rollback_of,
    }


def _classify(**overrides):
    values = {
        "expected_owner": OWNER,
        "actual_owner": None,
        "expected_release": RELEASE,
        "actual_release": None,
        "projected_release_id": "release-001",
        "operation_status": "running",
        "business_event_matches": False,
        "completed_result_matches": False,
        "chain": ReleaseChain(("release-001",), "release-001"),
    }
    values.update(overrides)
    return classify_release_state(**values)


def test_release_chain_ignores_started_time_when_computing_head():
    releases = [
        _release(
            "release-001",
            None,
            created_at="2026-08-27T10:00:02+00:00",
        ),
        _release(
            "release-002",
            "release-001",
            created_at="2026-08-27T10:00:01+00:00",
            version_id="v2",
        ),
    ]

    chain = build_release_chain(releases)

    assert chain.ordered_release_ids == ("release-001", "release-002")
    assert chain.head_release_id == "release-002"


@pytest.mark.parametrize(
    "releases",
    [
        [
            _release("release-001", None),
            _release("release-002", "release-001"),
            _release("release-003", "release-001"),
        ],
        [
            _release("release-001", "release-002"),
            _release("release-002", "release-001"),
        ],
        [
            _release("release-001", None),
            _release("release-002", None),
        ],
        [_release("release-001", "release-999")],
        [
            _release("release-001", None),
            _release("release-001", None),
        ],
    ],
    ids=["branch", "cycle", "two-roots", "orphan", "duplicate"],
)
def test_release_chain_rejects_non_linear_graphs(releases):
    with pytest.raises(ModelMatchingError) as exc_info:
        build_release_chain(releases)

    assert exc_info.value.code == "model_release_integrity_error"


def test_release_chain_validates_rollback_against_earlier_same_version():
    valid = [
        _release("release-001", None, version_id="v1"),
        _release("release-002", "release-001", version_id="v2"),
        _release(
            "release-003",
            "release-002",
            version_id="v1",
            action="rollback",
            rollback_of="release-001",
        ),
    ]
    assert build_release_chain(valid).head_release_id == "release-003"

    invalid = [dict(item) for item in valid]
    invalid[-1]["rollback_of_release_id"] = "release-002"
    with pytest.raises(ModelMatchingError) as exc_info:
        build_release_chain(invalid)
    assert exc_info.value.code == "model_release_integrity_error"

    invalid_type = [dict(item) for item in valid]
    invalid_type[-1]["rollback_of_release_id"] = []
    with pytest.raises(ModelMatchingError) as exc_info:
        build_release_chain(invalid_type)
    assert exc_info.value.code == "model_release_integrity_error"


def test_classifier_returns_each_recoverable_state_from_complete_evidence():
    assert _classify() is ReleaseState.NO_CANDIDATE
    assert _classify(actual_owner=OWNER) is ReleaseState.OWNED_CANDIDATE
    assert _classify(
        actual_owner=OWNER,
        actual_release=RELEASE,
        chain=ReleaseChain(
            ("release-001", "release-002"), "release-002"
        ),
    ) is ReleaseState.RELEASE_VISIBLE_OLD_PROJECTION
    assert _classify(
        actual_owner=OWNER,
        actual_release=RELEASE,
        projected_release_id="release-002",
        chain=ReleaseChain(
            ("release-001", "release-002"), "release-002"
        ),
    ) is ReleaseState.RELEASE_PROJECTED
    assert _classify(
        actual_owner=OWNER,
        actual_release=RELEASE,
        projected_release_id="release-003",
        chain=ReleaseChain(
            ("release-001", "release-002", "release-003"),
            "release-003",
        ),
    ) is ReleaseState.RELEASE_ANCESTOR
    assert _classify(
        actual_owner=OWNER,
        actual_release=RELEASE,
        projected_release_id="release-002",
        operation_status="completed",
        business_event_matches=True,
        completed_result_matches=True,
        chain=ReleaseChain(
            ("release-001", "release-002"), "release-002"
        ),
    ) is ReleaseState.COMPLETED


@pytest.mark.parametrize(
    "overrides",
    [
        {"chain": ReleaseChain(("release-999",), "release-999")},
        {"actual_owner": {**OWNER, "actor_id": "mallory"}},
        {
            "actual_owner": OWNER,
            "actual_release": {**RELEASE, "created_at": "2026-08-27T10:00:09+00:00"},
        },
        {"actual_release": RELEASE},
        {"business_event_matches": True},
        {
            "actual_owner": OWNER,
            "actual_release": RELEASE,
            "projected_release_id": "release-999",
        },
        {
            "actual_owner": OWNER,
            "actual_release": RELEASE,
            "projected_release_id": "release-002",
            "operation_status": "completed",
            "business_event_matches": False,
            "completed_result_matches": True,
            "chain": ReleaseChain(
                ("release-001", "release-002"), "release-002"
            ),
        },
        {
            "actual_owner": OWNER,
            "actual_release": RELEASE,
            "projected_release_id": "release-002",
            "operation_status": "completed",
            "business_event_matches": True,
            "completed_result_matches": True,
            "chain": ReleaseChain(("release-001",), "release-001"),
        },
        {
            "actual_owner": OWNER,
            "actual_release": RELEASE,
            "chain": ReleaseChain(
                ("release-001", "release-002", "release-003"),
                "release-003",
            ),
        },
        {
            "actual_owner": OWNER,
            "actual_release": RELEASE,
            "projected_release_id": "release-002",
            "chain": ReleaseChain(
                ("release-001", "release-002", "release-003"),
                "release-003",
            ),
        },
        {
            "actual_owner": OWNER,
            "actual_release": RELEASE,
            "projected_release_id": "release-003",
            "chain": ReleaseChain(
                (
                    "release-001",
                    "release-002",
                    "release-003",
                    "release-004",
                ),
                "release-004",
            ),
        },
    ],
)
def test_classifier_rejects_conflicting_or_partial_evidence(overrides):
    with pytest.raises(ModelMatchingError) as exc_info:
        _classify(**overrides)

    assert exc_info.value.code == "model_release_integrity_error"
