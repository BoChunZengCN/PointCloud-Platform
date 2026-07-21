from pc_system.identifiers import validate_identifier


SEVERITY_WEIGHT = {
    "critical": 400,
    "high": 300,
    "medium": 200,
    "low": 100,
}
SOURCE_WEIGHT = {
    "golden_evaluation": 30,
    "operational_quality": 20,
    "heuristic": 10,
}


def _suggested_action(reason_code: str) -> str:
    normalized = reason_code.lower()
    if "under" in normalized or "merged" in normalized:
        return "split"
    if "over" in normalized or "fragment" in normalized or "small" in normalized:
        return "merge"
    if "class" in normalized or "label" in normalized:
        return "relabel"
    if "noise" in normalized:
        return "restore_from_noise"
    return "confirm"


def _queue_item(
    *,
    sequence: int,
    instance_id: str,
    source: str,
    reason_code: str,
    severity: str,
    evidence: dict,
    confirmed: set[str],
) -> dict:
    severity = severity if severity in SEVERITY_WEIGHT else "medium"
    return {
        "item_id": f"queue-{sequence:05d}",
        "instance_id": validate_identifier(instance_id, "instance_id"),
        "source": source,
        "reason_code": reason_code,
        "severity": severity,
        "priority": SEVERITY_WEIGHT[severity] + SOURCE_WEIGHT[source],
        "suggested_action": _suggested_action(reason_code),
        "confirmed": instance_id in confirmed,
        "evidence": evidence,
    }


def build_review_queue(
    *,
    session: dict,
    baseline: dict,
    draft: dict,
    quality: dict | None = None,
    evaluation: dict | None = None,
) -> dict:
    """Build a deterministic advisory queue without mutating assignments."""

    confirmed = set(draft.get("confirmed_instance_ids", []))
    candidates = []
    for error in (evaluation or {}).get("instance_errors", []):
        instance_id = error.get("instance_id")
        if isinstance(instance_id, str):
            candidates.append(
                {
                    "instance_id": instance_id,
                    "source": "golden_evaluation",
                    "reason_code": str(error.get("kind", "evaluation_error")),
                    "severity": str(error.get("severity", "high")),
                    "evidence": dict(error),
                }
            )
    for flag in (quality or {}).get("flags", []):
        instance_id = flag.get("object_id", flag.get("instance_id"))
        if isinstance(instance_id, str):
            candidates.append(
                {
                    "instance_id": instance_id,
                    "source": "operational_quality",
                    "reason_code": str(flag.get("code", "quality_flag")),
                    "severity": str(flag.get("severity", "medium")),
                    "evidence": dict(flag),
                }
            )
    if not candidates:
        active = sorted(
            {
                item["instance_id"]
                for item in draft.get("assignments", [])
                if not item.get("is_noise", False)
            }
        )
        candidates.extend(
            {
                "instance_id": instance_id,
                "source": "heuristic",
                "reason_code": "needs_confirmation",
                "severity": "low",
                "evidence": {"message": "Awaiting human confirmation."},
            }
            for instance_id in active
        )
    items = [
        _queue_item(sequence=index, confirmed=confirmed, **candidate)
        for index, candidate in enumerate(candidates, start=1)
    ]
    items.sort(
        key=lambda item: (
            -item["priority"],
            item["instance_id"],
            item["reason_code"],
            item["item_id"],
        )
    )
    for index, item in enumerate(items, start=1):
        item["item_id"] = f"queue-{index:05d}"
    return {
        "schema_version": "1.0",
        "session_id": session.get("session_id"),
        "item_count": len(items),
        "pending_count": sum(not item["confirmed"] for item in items),
        "confirmed_count": sum(item["confirmed"] for item in items),
        "items": items,
    }


def build_correction_diff(baseline: dict, draft: dict) -> dict:
    """Summarize assignment changes without exposing the full point payload."""

    baseline_by_index = {
        int(item["source_point_index"]): item
        for item in baseline.get("assignments", [])
    }
    draft_by_index = {
        int(item["source_point_index"]): item
        for item in draft.get("assignments", [])
    }
    all_indices = sorted(set(baseline_by_index) | set(draft_by_index))
    changed = []
    noise_added = []
    noise_restored = []
    for index in all_indices:
        before = baseline_by_index.get(index)
        after = draft_by_index.get(index)
        if before is None or after is None:
            changed.append(index)
            continue
        identity_before = (
            before["instance_id"],
            before["class_id"],
            before["is_noise"],
        )
        identity_after = (
            after["instance_id"],
            after["class_id"],
            after["is_noise"],
        )
        if identity_before != identity_after:
            changed.append(index)
        if not before["is_noise"] and after["is_noise"]:
            noise_added.append(index)
        if before["is_noise"] and not after["is_noise"]:
            noise_restored.append(index)
    baseline_instances = {
        item["instance_id"]
        for item in baseline_by_index.values()
        if not item["is_noise"]
    }
    draft_instances = {
        item["instance_id"]
        for item in draft_by_index.values()
        if not item["is_noise"]
    }
    baseline_classes = {
        instance_id: {
            item["class_id"]
            for item in baseline_by_index.values()
            if not item["is_noise"] and item["instance_id"] == instance_id
        }
        for instance_id in baseline_instances
    }
    draft_classes = {
        instance_id: {
            item["class_id"]
            for item in draft_by_index.values()
            if not item["is_noise"] and item["instance_id"] == instance_id
        }
        for instance_id in draft_instances
    }
    class_changed_instances = sorted(
        instance_id
        for instance_id in baseline_instances & draft_instances
        if baseline_classes[instance_id] != draft_classes[instance_id]
    )
    created = sorted(draft_instances - baseline_instances)
    removed = sorted(baseline_instances - draft_instances)
    return {
        "schema_version": "1.0",
        "changed_point_count": len(changed),
        "noise_added_point_count": len(noise_added),
        "noise_restored_point_count": len(noise_restored),
        "class_change_count": len(class_changed_instances),
        "created_instance_count": len(created),
        "removed_instance_count": len(removed),
        "confirmed_instance_count": len(draft.get("confirmed_instance_ids", [])),
        "affected_source_point_indices": changed[:1000],
        "created_instance_ids": created[:100],
        "removed_instance_ids": removed[:100],
        "class_changed_instance_ids": class_changed_instances[:100],
    }
