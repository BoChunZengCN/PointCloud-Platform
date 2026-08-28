from dataclasses import dataclass
from enum import Enum

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError


class ReleaseState(Enum):
    NO_CANDIDATE = "no_candidate"
    OWNED_CANDIDATE = "owned_candidate"
    RELEASE_VISIBLE_OLD_PROJECTION = "release_visible_old_projection"
    RELEASE_PROJECTED = "release_projected"
    RELEASE_ANCESTOR = "release_ancestor"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ReleaseChain:
    ordered_release_ids: tuple[str, ...]
    head_release_id: str | None


def _integrity_error(message: str) -> ModelMatchingError:
    return ModelMatchingError("model_release_integrity_error", message)


def build_release_chain(releases: list[dict]) -> ReleaseChain:
    if type(releases) is not list:
        raise _integrity_error("Model release history must be a list.")
    if not releases:
        return ReleaseChain((), None)

    by_id: dict[str, dict] = {}
    try:
        for release in releases:
            if type(release) is not dict:
                raise ValueError("release must be an object")
            release_id = release["release_id"]
            previous_id = release["previous_release_id"]
            if type(release_id) is not str:
                raise ValueError("release_id must be a string")
            validate_identifier(release_id, "release_id")
            if previous_id is not None:
                if type(previous_id) is not str:
                    raise ValueError("previous_release_id must be a string")
                validate_identifier(previous_id, "previous_release_id")
            if release_id in by_id:
                raise ValueError("duplicate release identity")
            by_id[release_id] = release
    except (KeyError, TypeError, ValueError) as exc:
        raise _integrity_error("Model release graph contains invalid nodes.") from exc

    roots = [
        release_id
        for release_id, release in by_id.items()
        if release["previous_release_id"] is None
    ]
    if len(roots) != 1:
        raise _integrity_error("Model release graph must have one root.")

    successor: dict[str, str] = {}
    for release_id, release in by_id.items():
        previous_id = release["previous_release_id"]
        if previous_id is None:
            continue
        if previous_id not in by_id or previous_id == release_id:
            raise _integrity_error(
                "Model release graph contains an orphan or self-reference."
            )
        if previous_id in successor:
            raise _integrity_error("Model release graph contains a branch.")
        successor[previous_id] = release_id

    ordered: list[str] = []
    visited: set[str] = set()
    current: str | None = roots[0]
    while current is not None:
        if current in visited:
            raise _integrity_error("Model release graph contains a cycle.")
        visited.add(current)
        ordered.append(current)
        current = successor.get(current)
    if visited != set(by_id):
        raise _integrity_error(
            "Model release graph contains a cycle or disconnected nodes."
        )

    earlier: dict[str, dict] = {}
    for release_id in ordered:
        release = by_id[release_id]
        action = release.get("action")
        rollback_id = release.get("rollback_of_release_id")
        if action == "activate":
            if rollback_id is not None:
                raise _integrity_error(
                    "Activation release contains a rollback reference."
                )
        elif action == "rollback":
            if type(rollback_id) is not str:
                raise _integrity_error(
                    "Rollback release reference is invalid."
                )
            target = earlier.get(rollback_id)
            if (
                target is None
                or target.get("version_id") != release.get("version_id")
                or rollback_id == release.get("previous_release_id")
            ):
                raise _integrity_error(
                    "Rollback release reference is not an earlier matching version."
                )
        else:
            raise _integrity_error("Model release graph contains an invalid action.")
        earlier[release_id] = release

    return ReleaseChain(tuple(ordered), ordered[-1])


def _is_ancestor(
    chain: ReleaseChain, ancestor_release_id: str, descendant_release_id: str
) -> bool:
    try:
        ancestor_index = chain.ordered_release_ids.index(ancestor_release_id)
        descendant_index = chain.ordered_release_ids.index(descendant_release_id)
    except ValueError:
        return False
    return ancestor_index < descendant_index


def classify_release_state(
    *,
    expected_owner: dict,
    actual_owner: dict | None,
    expected_release: dict,
    actual_release: dict | None,
    projected_release_id: str | None,
    operation_status: str,
    business_event_matches: bool,
    completed_result_matches: bool,
    chain: ReleaseChain,
) -> ReleaseState:
    if (
        type(expected_owner) is not dict
        or type(expected_release) is not dict
        or actual_owner is not None
        and type(actual_owner) is not dict
        or actual_release is not None
        and type(actual_release) is not dict
        or type(business_event_matches) is not bool
        or type(completed_result_matches) is not bool
        or type(chain) is not ReleaseChain
    ):
        raise _integrity_error("Release state evidence has invalid types.")
    if actual_owner is not None and actual_owner != expected_owner:
        raise _integrity_error("Release owner differs from canonical evidence.")
    if actual_release is not None and actual_owner is None:
        raise _integrity_error("Visible release has no canonical owner.")
    if actual_release is not None and actual_release != expected_release:
        raise _integrity_error("Visible release differs from canonical evidence.")
    if actual_release is None and (
        business_event_matches or completed_result_matches
    ):
        raise _integrity_error(
            "Release audit evidence exists before the release is visible."
        )

    previous_id = expected_release.get("previous_release_id")
    release_id = expected_release.get("release_id")
    if type(release_id) is not str:
        raise _integrity_error("Canonical release identity is invalid.")

    if operation_status == "completed":
        if (
            actual_owner != expected_owner
            or actual_release != expected_release
            or release_id not in chain.ordered_release_ids
            or not business_event_matches
            or not completed_result_matches
            or projected_release_id != chain.head_release_id
            or projected_release_id != release_id
            and not _is_ancestor(chain, release_id, projected_release_id)
        ):
            raise _integrity_error(
                "Completed release operation has conflicting evidence."
            )
        return ReleaseState.COMPLETED

    if operation_status != "running" or completed_result_matches:
        raise _integrity_error("Release operation status cannot be recovered.")
    if actual_owner is None:
        if (
            actual_release is None
            and projected_release_id == previous_id
            and chain.head_release_id == previous_id
        ):
            return ReleaseState.NO_CANDIDATE
        raise _integrity_error("Release candidate evidence is inconsistent.")
    if actual_release is None:
        if (
            projected_release_id == previous_id
            and chain.head_release_id == previous_id
        ):
            return ReleaseState.OWNED_CANDIDATE
        raise _integrity_error("Owned release candidate has an invalid projection.")

    if release_id not in chain.ordered_release_ids:
        raise _integrity_error("Visible release is absent from its release graph.")
    if (
        projected_release_id == previous_id
        and chain.head_release_id == release_id
    ):
        return ReleaseState.RELEASE_VISIBLE_OLD_PROJECTION
    if (
        projected_release_id == release_id
        and chain.head_release_id == release_id
    ):
        return ReleaseState.RELEASE_PROJECTED
    if (
        projected_release_id is not None
        and projected_release_id == chain.head_release_id
        and _is_ancestor(chain, release_id, projected_release_id)
    ):
        return ReleaseState.RELEASE_ANCESTOR
    raise _integrity_error("Visible release cannot be reconciled with projection.")
