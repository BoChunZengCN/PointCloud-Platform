import hashlib
import json
import math
import re
import time
import unicodedata
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_feature_index import load_model_feature_index, read_index_entries
from pc_system.model_feature_store import load_feature, publish_object_feature
from pc_system.model_index_release import (
    load_current_model_feature_index_release,
)
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_release import _load_json, _record_failure, _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_retrieval_config import load_retrieval_config
from pc_system.model_retrieval_input import load_retrieval_object
from pc_system.model_sampling import _publish_exact_json


_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def _round(value: float) -> float:
    result = round(float(value), 12)
    return 0.0 if result == 0 else result


def _normalized(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ModelMatchingError("invalid_retrieval_input", "Retrieval hint is invalid.")
    result = unicodedata.normalize("NFKC", value).casefold().strip()
    return result or None


def _tokens(values: object) -> list[str]:
    if type(values) is not list or any(type(value) is not str for value in values):
        raise ModelMatchingError("invalid_retrieval_input", "Retrieval terms are invalid.")
    result: set[str] = set()
    for raw in values:
        normalized = _normalized(raw)
        if normalized:
            result.add(normalized)
            result.update(part for part in _SPLIT.split(normalized) if part)
    return sorted(result)


def _term_weights(value: dict) -> dict[str, float]:
    result = {term: 1.0 for term in value.get("keyword_terms", [])}
    for term in value.get("tag_terms", []):
        result[term] = 2.0
    return result


def _vector_similarity(first: object, second: object) -> float | None:
    if (
        type(first) is not list
        or type(second) is not list
        or len(first) != len(second)
        or not first
        or any(type(value) not in {int, float} for value in [*first, *second])
    ):
        return None
    return max(0.0, min(1.0, 1.0 - 0.5 * math.fsum(abs(float(a) - float(b)) for a, b in zip(first, second))))


def score_candidate(query: dict, candidate: dict, config: dict) -> dict:
    scoring = config.get("scoring_config", config)
    weights = scoring["weights"]
    components: dict[str, dict] = {}
    risks: set[str] = set()
    if query.get("category_id") is not None:
        components["category"] = {"score": 1.0 if query["category_id"] == candidate.get("category_id") else 0.0}
    query_terms = _term_weights(query)
    candidate_terms = _term_weights(candidate)
    if query_terms and candidate_terms:
        union = set(query_terms) | set(candidate_terms)
        intersection = math.fsum(min(query_terms.get(term, 0.0), candidate_terms.get(term, 0.0)) for term in union)
        denominator = math.fsum(max(query_terms.get(term, 0.0), candidate_terms.get(term, 0.0)) for term in union)
        components["terms"] = {"score": _round(intersection / denominator)}
    comparisons = []
    for field in ("manufacturer", "model_number"):
        requested = _normalized(query.get(field))
        available = _normalized(candidate.get(field))
        if requested is not None and available is not None:
            comparisons.append(1.0 if requested == available else 0.0)
    if comparisons:
        components["manufacturer_model"] = {"score": _round(math.fsum(comparisons) / len(comparisons))}
    query_features = query.get("features") or {}
    candidate_features = candidate.get("features") or {}
    query_quality = (query_features.get("quality") or {}).get("status")
    candidate_quality = (candidate_features.get("quality") or {}).get("status")
    reasons = set((query_features.get("quality") or {}).get("reasons", [])) | set((candidate_features.get("quality") or {}).get("reasons", []))
    risks.update(reasons)
    if "metadata_only" in {query_quality, candidate_quality}:
        risks.add("metadata_only")
    if query_quality == candidate_quality == "usable":
        object_spans = query_features.get("observed_spans_m")
        model_spans = candidate_features.get("observed_spans_m")
        if (
            type(object_spans) is list
            and type(model_spans) is list
            and len(object_spans) == len(model_spans) == 3
            and all(float(value) > 0 for value in [*object_spans, *model_spans])
        ):
            penalties = scoring["dimension_penalties"]
            errors = []
            for model, observed in zip(model_spans, object_spans):
                multiplier = penalties["model_smaller_multiplier"] if model < observed else penalties["model_larger_multiplier"]
                errors.append(abs(math.log(float(model) / float(observed))) * multiplier)
            components["dimensions"] = {"score": _round(math.exp(-math.fsum(errors) / 3.0))}
    if "metadata_only" not in {query_quality, candidate_quality}:
        shape_scores = []
        for field in ("principal_value_ratios", "radial_histogram"):
            similarity = _vector_similarity(query_features.get(field), candidate_features.get(field))
            if similarity is not None:
                shape_scores.append(similarity)
        if shape_scores:
            components["shape"] = {"score": _round(math.fsum(shape_scores) / len(shape_scores))}
    if query_quality == candidate_quality == "usable":
        first = query_features.get("voxel_occupancy")
        second = candidate_features.get("voxel_occupancy")
        if type(first) in {int, float} and type(second) in {int, float}:
            components["occupancy"] = {"score": _round(max(0.0, min(1.0, 1.0 - abs(float(first) - float(second)))))}
    if not components:
        raise ModelMatchingError("no_candidate_models", "Candidate has no valid scoring components.")
    total_weight = math.fsum(weights[name] for name in components)
    effective = {name: _round(weights[name] / total_weight) for name in components}
    last = next(reversed(effective))
    effective[last] = _round(1.0 - math.fsum(value for name, value in effective.items() if name != last))
    total = math.fsum(effective[name] * components[name]["score"] for name in components)
    return {
        "model_id": candidate["model_id"],
        "version_id": candidate["version_id"],
        "score": _round(total),
        "components": components,
        "effective_weights": effective,
        "risks": sorted(risks),
    }


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _child(prefix: str, operation_id: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(f"{prefix}\0{operation_id}".encode()).hexdigest()[:32]
    return f"op-{prefix}-{digest}", f"req-{prefix}-{digest}", f"idem-{prefix}-{digest}"


def _run_root(root: Path, asset_id: str, source_id: str, instance_id: str, run_id: str) -> Path:
    return root / "reports" / "model_retrieval" / asset_id / source_id / instance_id / run_id


def _read(path: Path) -> dict:
    try:
        _require_plain(path, directory=False)
        value = _load_json(path)
    except (OSError, ModelMatchingError) as exc:
        raise ModelMatchingError("feature_integrity_error", "Retrieval artifact is invalid.") from exc
    if type(value) is not dict:
        raise ModelMatchingError("feature_integrity_error", "Retrieval artifact is invalid.")
    return value


def load_model_retrieval(project_root: Path, *, asset_id: str, source_id: str, instance_id: str, retrieval_run_id: str) -> dict:
    try:
        values = [validate_identifier(value, "retrieval_identity") for value in (asset_id, source_id, instance_id, retrieval_run_id)]
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("retrieval_object_not_found", "Retrieval run identity is invalid.") from exc
    root = Path(project_root)
    directory = _run_root(root, *values)
    try:
        _require_plain(directory, directory=True)
    except FileNotFoundError as exc:
        raise ModelMatchingError("retrieval_object_not_found", "Retrieval run does not exist.") from exc
    owner = _read(directory / "operation_owner.json")
    query_feature = _read(directory / "query_feature.json")
    candidates = _read(directory / "candidates.json")
    report = _read(directory / "retrieval_report.json")
    try:
        snapshot = read_verified_operation_snapshot(root, report["operation_id"])
        operation = snapshot["operation"]
        completed = [event for event in snapshot["events"] if event["event_type"] == "model_retrieval.completed"]
        expected_report_fingerprint = _hash(
            {key: value for key, value in report.items() if key != "report_fingerprint"}
        )
        if (
            report["status"] != "completed"
            or report["asset_id"] != values[0]
            or report["source_id"] != values[1]
            or report["instance_id"] != values[2]
            or report["retrieval_run_id"] != values[3]
            or report["query_feature_fingerprint"] != _hash(query_feature)
            or report["candidates_fingerprint"] != _hash(candidates)
            or report["candidates"] != candidates["candidates"]
            or report["report_fingerprint"] != expected_report_fingerprint
            or owner["operation_id"] != report["operation_id"]
            or operation["request_id"] != owner["request_id"]
            or operation["request_fingerprint"] != owner["request_fingerprint"]
            or operation["status"] != "completed"
            or operation.get("result") != {"retrieval_run_id": values[3], "report_fingerprint": expected_report_fingerprint}
            or len(completed) != 1
            or completed[0]["details"] != operation["result"]
        ):
            raise ValueError("retrieval evidence differs")
        load_feature(root, feature_type="object", identity={
            "asset_id": values[0], "source_id": values[1], "instance_id": values[2], "feature_id": query_feature["feature_id"]
        })
        config = load_retrieval_config(root, report["config_id"])
        if config["config_fingerprint"] != report["config_fingerprint"]:
            raise ValueError("retrieval config differs")
        if report["index_release_id"] is not None:
            release = load_current_model_feature_index_release(root)
            if (
                release is None
                or release["release_id"] != report["index_release_id"]
                or release["index_id"] != report["index_id"]
            ):
                raise ValueError("production index release differs")
        else:
            index = load_model_feature_index(
                root, report["index_id"], require_current_heads=False
            )
            if index["index_mode"] != "challenger":
                raise ValueError("experimental index differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise ModelMatchingError("feature_integrity_error", "Retrieval evidence is invalid.") from exc
    return json.loads(json.dumps(report, ensure_ascii=False))


def retrieve_model_candidates(
    project_root: Path, *, retrieval_run_id: str, source_kind: str, asset_id: str, source_id: str,
    instance_id: str, index_release_id: str | None, index_id: str | None, top_k: int,
    keywords: list[str], tags: list[str], manufacturer: str | None, model_number: str | None,
    hint_source: str | None, principal: Principal, operation_id: str, request_id: str,
    idempotency_key: str,
) -> dict:
    root = Path(project_root)
    try:
        retrieval_run_id = validate_identifier(retrieval_run_id, "retrieval_run_id")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("invalid_retrieval_input", "Retrieval run identity is invalid.") from exc
    keyword_terms, tag_terms = _tokens(keywords), _tokens(tags)
    manufacturer, model_number = _normalized(manufacturer), _normalized(model_number)
    has_hints = bool(keyword_terms or tag_terms or manufacturer or model_number)
    if type(top_k) is not int or not 1 <= top_k <= 50 or (has_hints and hint_source not in {"human", "upstream_system"}) or (not has_hints and hint_source is not None):
        raise ModelMatchingError("invalid_retrieval_input", "Retrieval parameters are invalid.")
    require_any_role(principal, {"expert"})
    request_payload = {
        "retrieval_run_id": retrieval_run_id, "source_kind": source_kind, "asset_id": asset_id,
        "source_id": source_id, "instance_id": instance_id, "index_release_id": index_release_id,
        "index_id": index_id, "top_k": top_k, "keyword_terms": keyword_terms, "tag_terms": tag_terms,
        "manufacturer": manufacturer, "model_number": model_number, "hint_source": hint_source,
    }
    operation, replayed = start_operation(root, operation_id=operation_id, operation_type="model_retrieval.run", principal=principal, request_id=request_id, idempotency_key=idempotency_key, request_payload=request_payload)
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(error.get("code", "feature_integrity_error"), error.get("message", "Retrieval failed."))
    try:
        if replayed and operation["status"] == "completed":
            return load_model_retrieval(root, asset_id=asset_id, source_id=source_id, instance_id=instance_id, retrieval_run_id=retrieval_run_id)
        query_object = load_retrieval_object(root, source_kind=source_kind, asset_id=asset_id, source_id=source_id, instance_id=instance_id, principal=principal)
        if index_id is None:
            release = load_current_model_feature_index_release(root)
            if release is None or (index_release_id is not None and index_release_id != release["release_id"]):
                raise ModelMatchingError("model_index_release_not_found", "Production index release is unavailable.")
            selected_index_id = release["index_id"]
            selected_release_id = release["release_id"]
            index = load_model_feature_index(root, selected_index_id, require_current_heads=True)
        else:
            if index_release_id is not None:
                raise ModelMatchingError("invalid_retrieval_input", "Experimental index cannot name a release.")
            selected_index_id = validate_identifier(index_id, "index_id")
            selected_release_id = None
            index = load_model_feature_index(root, selected_index_id, require_current_heads=False)
            if index["index_mode"] != "challenger":
                raise ModelMatchingError("invalid_retrieval_input", "Explicit index must be Challenger.")
        config = load_retrieval_config(root, index["config_id"])
        feature_child = _child("retrieval-feature", operation_id)
        query_feature = publish_object_feature(
            root, source_kind=source_kind, asset_id=asset_id, source_id=source_id, instance_id=instance_id,
            config_id=config["config_id"], principal=principal, operation_id=feature_child[0],
            request_id=feature_child[1], idempotency_key=feature_child[2],
        )
        mapped_category = config["category_mapping"]["mappings"].get(query_object["class_id"])
        query = {
            "category_id": mapped_category, "keyword_terms": keyword_terms, "tag_terms": tag_terms,
            "manufacturer": manufacturer, "model_number": model_number, "features": query_feature["features"],
        }
        entries = list(read_index_entries(root, selected_index_id))
        hard_eligible = (
            source_kind == "correction_release"
            and query_object["category_trust"] == "human_confirmed"
            and query_object["classification_source"] == "human_confirmed"
            and mapped_category is not None
        )
        matching = [entry for entry in entries if entry["category_id"] == mapped_category] if hard_eligible else []
        if hard_eligible and matching:
            selected = matching
            filter_info = {"applied": True, "category_id": mapped_category, "degraded": False, "reason": None}
            ensure_operation_event(root, operation_id, "model_retrieval.category_filter_applied", {"category_id": mapped_category, "candidate_count": len(selected)})
        else:
            selected = entries
            reason = "category_filter_empty" if hard_eligible else "category_filter_not_trusted"
            filter_info = {"applied": False, "category_id": mapped_category, "degraded": True, "reason": reason}
            ensure_operation_event(root, operation_id, "model_retrieval.category_filter_degraded", {"reason": reason, "candidate_count": len(selected)})
        started = time.perf_counter_ns()
        scored = []
        for entry in selected:
            try:
                scored.append(score_candidate(query, entry, config))
            except ModelMatchingError as exc:
                if exc.code != "no_candidate_models":
                    raise
        scored.sort(key=lambda item: (-item["score"], item["model_id"], item["version_id"]))
        if not scored:
            raise ModelMatchingError("no_candidate_models", "No model candidate can be scored.")
        returned = scored[:top_k]
        duration = max(0, (time.perf_counter_ns() - started) // 1000)
        ensure_operation_event(root, operation_id, "model_retrieval.input_verified", {"object_fingerprint": query_object["object_fingerprint"], "index_id": selected_index_id})
        snapshot = read_verified_operation_snapshot(root, operation_id)
        first_event = snapshot["events"][0]
        candidates_artifact = {"schema_version": "1.0", "candidates": returned}
        report = {
            "schema_version": "1.0", "retrieval_run_id": retrieval_run_id, "asset_id": asset_id,
            "source_id": source_id, "instance_id": instance_id, "source_kind": source_kind,
            "object_fingerprint": query_object["object_fingerprint"], "query_feature_id": query_feature["feature_id"],
            "query_feature_fingerprint": _hash(query_feature), "index_release_id": selected_release_id,
            "index_id": selected_index_id, "config_id": config["config_id"], "config_fingerprint": config["config_fingerprint"],
            "hints": {"keywords": keyword_terms, "tags": tag_terms, "manufacturer": manufacturer, "model_number": model_number, "source": hint_source},
            "category_filter": filter_info,
            "candidate_counts": {"before_filter": len(entries), "after_filter": len(selected), "scored": len(scored), "returned": len(returned)},
            "scan_duration_microseconds": duration, "candidates": returned,
            "candidates_fingerprint": _hash(candidates_artifact), "operation_id": operation_id,
            "generated_by": first_event["actor_id"], "generated_at": first_event["timestamp"], "status": "completed",
        }
        report["report_fingerprint"] = _hash(report)
        result = {"retrieval_run_id": retrieval_run_id, "report_fingerprint": report["report_fingerprint"]}
        owner = {"schema_version": "1.0", "retrieval_run_id": retrieval_run_id, "operation_id": operation_id, "request_id": operation["request_id"], "request_fingerprint": operation["request_fingerprint"]}
        directory = _run_root(root, asset_id, source_id, instance_id, retrieval_run_id)
        with model_resource_lock(root, "model-retrieval", asset_id, source_id, instance_id, retrieval_run_id):
            directory.mkdir(parents=True, exist_ok=True)
            _publish_exact_json(directory / "operation_owner.json", owner, conflict_code="operation_busy", conflict_message="Retrieval owner conflicts.")
            _publish_exact_json(directory / "query_feature.json", query_feature, conflict_code="feature_integrity_error", conflict_message="Query feature conflicts.")
            _publish_exact_json(directory / "candidates.json", candidates_artifact, conflict_code="feature_integrity_error", conflict_message="Candidates conflict.")
            _publish_exact_json(directory / "retrieval_report.json", report, conflict_code="feature_integrity_error", conflict_message="Retrieval report conflicts.")
            ensure_operation_event(root, operation_id, "model_retrieval.completed", result)
            complete_operation(root, operation_id, result)
            return load_model_retrieval(root, asset_id=asset_id, source_id=source_id, instance_id=instance_id, retrieval_run_id=retrieval_run_id)
    except Exception as exc:
        error = exc if isinstance(exc, ModelMatchingError) else ModelMatchingError("feature_integrity_error", "Model retrieval failed.")
        current = load_operation(root, operation_id)
        if current["status"] == "running" and error.code not in {"operation_busy", "publication_recovery_required"}:
            _record_failure(root, operation_id, error)
        if error is exc:
            raise
        raise error from exc
