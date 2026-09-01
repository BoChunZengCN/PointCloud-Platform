import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_audit import (
    complete_operation,
    ensure_operation_event,
    fail_operation,
    load_operation,
    read_verified_operation_snapshot,
    start_operation,
)
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_matching_identity import Principal, require_any_role
from pc_system.model_registration_config import load_registration_config
from pc_system.model_registration_engine import EngineDescription, RegistrationEngine
from pc_system.model_registration_gate import evaluate_registration_gate
from pc_system.model_registration_input import load_registration_input
from pc_system.model_registration_metrics import compute_registration_metrics
from pc_system.model_registration_transform import (
    generate_initial_hypotheses,
    validate_rigid_transform,
)
from pc_system.model_release import _require_plain
from pc_system.model_resource_lock import model_resource_lock
from pc_system.model_sampling import _canonical_json_bytes, _publish_exact_json


_ARTIFACT_NAMES = (
    "registration_input.json",
    "initial_hypotheses.json",
    "coarse_results.json",
    "fine_results.json",
    "residual_report.json",
    "transformed_preview.json",
)
_OWNER_FIELDS = {
    "schema_version",
    "registration_id",
    "operation_id",
    "request_id",
    "request_fingerprint",
}
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


def _integrity(message: str) -> ModelMatchingError:
    return ModelMatchingError("artifact_integrity_failed", message)


def _registration_root(
    root: Path,
    asset_id: str,
    source_id: str,
    instance_id: str,
    registration_id: str,
) -> Path:
    return (
        root
        / "reports"
        / "model_registrations"
        / asset_id
        / source_id
        / instance_id
        / registration_id
    )


def _canonical_fingerprint(value: dict) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _read_canonical(path: Path) -> dict:
    try:
        _require_plain(path, directory=False)
        payload = path.read_bytes()
        if len(payload) > _MAX_ARTIFACT_BYTES:
            raise ValueError("registration artifact is too large")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        if type(value) is not dict or payload != _canonical_json_bytes(value):
            raise ValueError("registration artifact is not canonical")
        return value
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError, ModelMatchingError) as exc:
        raise _integrity("Registration artifact is invalid.") from exc


def _owner(operation: dict, registration_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "registration_id": registration_id,
        "operation_id": operation["operation_id"],
        "request_id": operation["request_id"],
        "request_fingerprint": operation["request_fingerprint"],
    }


def _result(report: dict) -> dict:
    return {
        "registration_id": report["registration_id"],
        "report_fingerprint": report["report_fingerprint"],
    }


def _plain(value: object) -> object:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "registration_engine_failed", "Registration engine output is invalid."
        ) from exc


def _select_final_hypothesis(
    coarse_results: list[dict], fine_results: list[dict], config: dict
) -> tuple[dict, float | None, bool]:
    del coarse_results, config
    if type(fine_results) is not list or not fine_results:
        raise ModelMatchingError(
            "registration_engine_failed", "No fine registration result exists."
        )
    try:
        normalized = []
        for result in fine_results:
            if type(result) is not dict or type(result.get("score")) not in {
                int,
                float,
            }:
                raise ValueError("invalid registration score")
            score = float(result["score"])
            if not np.isfinite(score):
                raise ValueError("invalid registration score")
            normalized.append((score, str(result["hypothesis_id"]), result))
        normalized.sort(key=lambda item: (-item[0], item[1]))
        best = normalized[0]
        margin = None if len(normalized) == 1 else best[0] - normalized[1][0]
        equivalent = best[2].get("symmetry_equivalent", False)
        if type(equivalent) is not bool:
            raise ValueError("invalid symmetry equivalence")
        return _plain(best[2]), margin, equivalent
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "registration_engine_failed", "Registration results are invalid."
        ) from exc


def _preview(frozen: dict, validated: dict | None) -> dict:
    points: list[list[float]] = []
    if validated is not None:
        matrix = np.asarray(validated["matrix"], dtype=np.float64)
        model = np.asarray(frozen["model_points"][:256], dtype=np.float64)
        homogeneous = np.column_stack([model, np.ones(len(model))])
        points = np.round((matrix @ homogeneous.T).T[:, :3], 12).tolist()
    return {
        "schema_version": "1.0",
        "coordinate_unit": "m",
        "authoritative": False,
        "transformed_model_points": points,
    }


def _build_registration_artifacts(
    *,
    frozen: dict,
    config: dict,
    description: EngineDescription | None,
    hypotheses: list[dict],
    coarse: list[dict],
    fine: list[dict],
    validated: dict | None,
    metrics: dict,
    gate: dict,
    operation: dict,
    registration_id: str,
    error: dict | None = None,
) -> tuple[dict, dict]:
    artifacts = {
        "registration_input.json": {
            "schema_version": "1.0",
            **frozen,
        },
        "initial_hypotheses.json": {
            "schema_version": "1.0",
            "hypotheses": hypotheses,
        },
        "coarse_results.json": {
            "schema_version": "1.0",
            "results": coarse,
        },
        "fine_results.json": {"schema_version": "1.0", "results": fine},
        "residual_report.json": {
            "schema_version": "1.0",
            "metrics": metrics,
            "gate": gate,
        },
        "transformed_preview.json": _preview(frozen, validated),
    }
    artifacts = _plain(artifacts)
    artifact_fingerprints = {
        name: _canonical_fingerprint(artifacts[name]) for name in _ARTIFACT_NAMES
    }
    candidate = frozen.get("candidate_evidence", {})
    retrieval = frozen.get("retrieval_evidence", {})
    completed = error is None
    selected = None
    if fine:
        try:
            selected = min(
                fine,
                key=lambda item: (
                    -float(item["score"]),
                    str(item["hypothesis_id"]),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelMatchingError(
                "registration_engine_failed", "Registration results are invalid."
            ) from exc
    report = {
        "schema_version": "1.0",
        "registration_id": registration_id,
        "asset_id": retrieval.get("asset_id"),
        "source_id": retrieval.get("source_id"),
        "instance_id": retrieval.get("instance_id"),
        "retrieval_run_id": retrieval.get("retrieval_run_id"),
        "candidate_rank": candidate.get("candidate_rank"),
        "candidate_model_id": candidate.get("model_id"),
        "candidate_version_id": candidate.get("version_id"),
        "candidate_release_id": candidate.get("release_id"),
        "candidate_representation_id": candidate.get("representation_id"),
        "candidate_representation_fingerprint": candidate.get(
            "representation_fingerprint"
        ),
        "candidate_feature_id": candidate.get("feature_id"),
        "candidate_feature_vector_fingerprint": candidate.get(
            "feature_vector_fingerprint"
        ),
        "object_fingerprint": frozen.get("object_fingerprint"),
        "engine": None if description is None else description.name,
        "engine_version": None if description is None else description.version,
        "engine_production": None if description is None else description.production,
        "config_id": config.get("config_id"),
        "config_fingerprint": config.get("config_fingerprint"),
        "initial_hypotheses": hypotheses,
        "coarse_results": coarse,
        "fine_results": fine,
        "rigid_transform_4x4": None if validated is None else validated["matrix"],
        "coarse_metrics": None if selected is None else selected.get("coarse_metrics"),
        "fine_metrics": None if selected is None else selected.get("fine_metrics"),
        "residual_metrics": metrics,
        "gate_status": gate.get("status") if completed else None,
        "gate_reasons": gate.get("reasons", []) if completed else [],
        "artifacts": artifact_fingerprints,
        "operation_id": operation["operation_id"],
        "owner_request_id": operation["request_id"],
        "owner_request_fingerprint": operation["request_fingerprint"],
        "generated_by": operation["actor_id"],
        "generated_at": operation["started_event_at"],
        "status": "completed" if completed else "failed",
        "error": error,
    }
    report = _plain(report)
    report["report_fingerprint"] = _canonical_fingerprint(report)
    return report, artifacts


def _validate_report_artifacts(directory: Path, report: dict) -> None:
    declared = report.get("artifacts")
    if type(declared) is not dict or set(declared) != set(_ARTIFACT_NAMES):
        raise _integrity("Registration artifact declaration is invalid.")
    loaded = {name: _read_canonical(directory / name) for name in _ARTIFACT_NAMES}
    if any(
        _canonical_fingerprint(loaded[name]) != declared[name]
        for name in _ARTIFACT_NAMES
    ):
        raise _integrity("Registration artifact fingerprint differs.")
    residual = loaded["residual_report.json"]
    frozen = loaded["registration_input.json"]
    candidate = frozen.get("candidate_evidence", {})
    retrieval = frozen.get("retrieval_evidence", {})
    gate = residual.get("gate", {})
    if (
        loaded["initial_hypotheses.json"].get("hypotheses")
        != report.get("initial_hypotheses")
        or loaded["coarse_results.json"].get("results")
        != report.get("coarse_results")
        or loaded["fine_results.json"].get("results") != report.get("fine_results")
        or residual.get("metrics") != report.get("residual_metrics")
        or gate.get("status") != report.get("gate_status")
        or gate.get("reasons", []) != report.get("gate_reasons")
        or retrieval.get("asset_id") != report.get("asset_id")
        or retrieval.get("source_id") != report.get("source_id")
        or retrieval.get("instance_id") != report.get("instance_id")
        or retrieval.get("retrieval_run_id") != report.get("retrieval_run_id")
        or candidate.get("candidate_rank") != report.get("candidate_rank")
        or candidate.get("representation_fingerprint")
        != report.get("candidate_representation_fingerprint")
        or frozen.get("object_fingerprint") != report.get("object_fingerprint")
    ):
        raise _integrity("Registration artifact contents differ from the report.")


def _validate_audit(root: Path, report: dict) -> None:
    try:
        snapshot = read_verified_operation_snapshot(root, report["operation_id"])
        operation = snapshot["operation"]
        events = snapshot["events"]
        event_type = (
            "model_registration.artifacts_published"
            if report["status"] == "completed"
            else "model_registration.diagnostic_published"
        )
        publications = [event for event in events if event["event_type"] == event_type]
        valid_terminal = (
            operation["status"] == "completed"
            and operation.get("result") == _result(report)
            if report["status"] == "completed"
            else operation["status"] == "failed"
            and operation.get("error", {}).get("code") == report["error"]["code"]
        )
        if (
            operation["operation_type"] != "model_registration.run"
            or not valid_terminal
            or not events
            or events[0]["event_type"] != "operation.started"
            or events[0]["actor_id"] != report["generated_by"]
            or events[0]["timestamp"] != report["generated_at"]
            or len(publications) != 1
            or publications[0]["details"] != _result(report)
            or operation["request_id"] != report["owner_request_id"]
            or operation["request_fingerprint"]
            != report["owner_request_fingerprint"]
        ):
            raise ValueError("registration audit differs")
    except (KeyError, TypeError, ValueError, ModelMatchingError) as exc:
        if isinstance(exc, ModelMatchingError) and exc.code == "operation_busy":
            raise
        raise _integrity("Registration audit evidence is invalid.") from exc


def _load_published_registration(
    root: Path,
    *,
    asset_id: str,
    source_id: str,
    instance_id: str,
    registration_id: str,
    validate_audit: bool,
) -> dict:
    directory = _registration_root(
        root, asset_id, source_id, instance_id, registration_id
    )
    try:
        _require_plain(directory, directory=True)
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "model_registration_not_found", "Model registration does not exist."
        ) from exc
    try:
        report = _read_canonical(directory / "registration_report.json")
    except FileNotFoundError as exc:
        raise ModelMatchingError(
            "publication_recovery_required",
            "Registration publication is incomplete.",
        ) from exc
    owner = _read_canonical(directory / "operation_owner.json")
    expected_fingerprint = report.get("report_fingerprint")
    basis = {key: value for key, value in report.items() if key != "report_fingerprint"}
    if (
        set(owner) != _OWNER_FIELDS
        or report.get("schema_version") != "1.0"
        or report.get("registration_id") != registration_id
        or report.get("asset_id") != asset_id
        or report.get("source_id") != source_id
        or report.get("instance_id") != instance_id
        or report.get("status") not in {"completed", "failed"}
        or report.get("operation_id") != owner.get("operation_id")
        or owner.get("registration_id") != registration_id
        or report.get("owner_request_id") != owner.get("request_id")
        or report.get("owner_request_fingerprint")
        != owner.get("request_fingerprint")
        or _canonical_fingerprint(basis) != expected_fingerprint
    ):
        raise _integrity("Registration report identity is invalid.")
    _validate_report_artifacts(directory, report)
    if validate_audit:
        _validate_audit(root, report)
    return _plain(report)


def load_model_registration(
    project_root: Path,
    *,
    asset_id: str,
    source_id: str,
    instance_id: str,
    registration_id: str,
) -> dict:
    try:
        values = [
            validate_identifier(value, "registration_identity")
            for value in (asset_id, source_id, instance_id, registration_id)
        ]
    except (TypeError, ValueError) as exc:
        raise _integrity("Registration identity is invalid.") from exc
    return _load_published_registration(
        Path(project_root),
        asset_id=values[0],
        source_id=values[1],
        instance_id=values[2],
        registration_id=values[3],
        validate_audit=True,
    )


def _publish_registration_artifacts(
    project_root: Path,
    *,
    report: dict,
    artifacts: dict,
    operation: dict,
) -> dict:
    root = Path(project_root)
    directory = _registration_root(
        root,
        report["asset_id"],
        report["source_id"],
        report["instance_id"],
        report["registration_id"],
    )
    with model_resource_lock(
        root,
        "model-registration",
        report["asset_id"],
        report["source_id"],
        report["instance_id"],
        report["registration_id"],
    ):
        directory.mkdir(parents=True, exist_ok=True)
        expected_owner = _owner(operation, report["registration_id"])
        try:
            actual_owner = _read_canonical(directory / "operation_owner.json")
        except FileNotFoundError:
            actual_owner = None
        if actual_owner is not None and actual_owner != expected_owner:
            raise ModelMatchingError(
                "operation_busy", "Registration candidate has another owner."
            )
        _publish_exact_json(
            directory / "operation_owner.json",
            expected_owner,
            conflict_code="operation_busy",
            conflict_message="Registration owner conflicts.",
        )
        for name in _ARTIFACT_NAMES:
            _publish_exact_json(
                directory / name,
                artifacts[name],
                conflict_code="artifact_integrity_failed",
                conflict_message="Registration artifact conflicts.",
            )
        _publish_exact_json(
            directory / "registration_report.json",
            report,
            conflict_code="artifact_integrity_failed",
            conflict_message="Registration report conflicts.",
        )
        published = _load_published_registration(
            root,
            asset_id=report["asset_id"],
            source_id=report["source_id"],
            instance_id=report["instance_id"],
            registration_id=report["registration_id"],
            validate_audit=False,
        )
        if published != report:
            raise _integrity("Published registration differs from the producer result.")
        return published


def _record_publication(root: Path, operation_id: str, report: dict) -> None:
    event_type = (
        "model_registration.artifacts_published"
        if report["status"] == "completed"
        else "model_registration.diagnostic_published"
    )
    ensure_operation_event(root, operation_id, event_type, _result(report))


def _recover_visible_report(root: Path, operation: dict, request: dict) -> dict | None:
    try:
        report = _load_published_registration(
            root,
            asset_id=request["asset_id"],
            source_id=request["source_id"],
            instance_id=request["instance_id"],
            registration_id=request["registration_id"],
            validate_audit=False,
        )
    except ModelMatchingError as exc:
        if exc.code in {"model_registration_not_found", "publication_recovery_required"}:
            return None
        raise
    expected_owner = _owner(operation, request["registration_id"])
    directory = _registration_root(
        root,
        request["asset_id"],
        request["source_id"],
        request["instance_id"],
        request["registration_id"],
    )
    if _read_canonical(directory / "operation_owner.json") != expected_owner:
        raise ModelMatchingError("operation_busy", "Registration owner differs.")
    _record_publication(root, operation["operation_id"], report)
    if report["status"] == "completed":
        try:
            complete_operation(root, operation["operation_id"], _result(report))
        except ModelMatchingError as exc:
            raise ModelMatchingError(
                "publication_recovery_required",
                "Registration report is visible but audit completion is pending.",
            ) from exc
        return load_model_registration(
            root,
            asset_id=request["asset_id"],
            source_id=request["source_id"],
            instance_id=request["instance_id"],
            registration_id=request["registration_id"],
        )
    error = report["error"]
    fail_operation(root, operation["operation_id"], error["code"], error["message"])
    raise ModelMatchingError(error["code"], error["message"])


def _description(engine: RegistrationEngine, expected_name: str) -> EngineDescription:
    try:
        description = engine.describe()
    except Exception as exc:
        raise ModelMatchingError(
            "registration_engine_failed", "Registration engine description failed."
        ) from exc
    if (
        not isinstance(description, EngineDescription)
        or description.name != expected_name
        or type(description.version) is not str
        or not description.version
        or type(description.production) is not bool
    ):
        raise ModelMatchingError(
            "registration_engine_unavailable", "Registration engine is incompatible."
        )
    return description


def _validated_engine_points(frozen: dict, config: dict) -> tuple[np.ndarray, np.ndarray]:
    try:
        model_points = np.asarray(frozen["model_points"], dtype=np.float64)
        object_points = np.asarray(frozen["object_points"], dtype=np.float64)
        minimum = config["preprocessing"]["minimum_points"]
        maximum = config["preprocessing"]["maximum_points"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "registration_input_incomplete", "Registration points are invalid."
        ) from exc
    if (
        model_points.ndim != 2
        or object_points.ndim != 2
        or model_points.shape[1:] != (3,)
        or object_points.shape[1:] != (3,)
        or not np.isfinite(model_points).all()
        or not np.isfinite(object_points).all()
        or not minimum <= len(model_points) <= maximum
        or not minimum <= len(object_points) <= maximum
    ):
        raise ModelMatchingError(
            "registration_input_incomplete",
            "Registration point count or coordinates violate the selected config.",
        )
    return model_points, object_points


def register_model_candidate(
    project_root: Path,
    *,
    registration_id: str,
    asset_id: str,
    source_id: str,
    instance_id: str,
    retrieval_run_id: str,
    candidate_rank: int,
    config_id: str,
    engine_resolver: Callable[[str], RegistrationEngine],
    principal: Principal,
    operation_id: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    require_any_role(principal, {"expert"})
    try:
        registration_id = validate_identifier(registration_id, "registration_id")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError(
            "registration_input_incomplete", "Registration identity is invalid."
        ) from exc
    root = Path(project_root)
    request = {
        "registration_id": registration_id,
        "asset_id": asset_id,
        "source_id": source_id,
        "instance_id": instance_id,
        "retrieval_run_id": retrieval_run_id,
        "candidate_rank": candidate_rank,
        "config_id": config_id,
    }
    operation, replayed = start_operation(
        root,
        operation_id=operation_id,
        operation_type="model_registration.run",
        principal=principal,
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_payload=request,
    )
    if replayed and operation["status"] == "completed":
        return load_model_registration(
            root,
            asset_id=asset_id,
            source_id=source_id,
            instance_id=instance_id,
            registration_id=registration_id,
        )
    if replayed and operation["status"] == "failed":
        error = operation.get("error") or {}
        raise ModelMatchingError(
            error.get("code", "registration_engine_failed"),
            error.get("message", "Registration failed."),
        )
    if replayed:
        recovered = _recover_visible_report(root, operation, request)
        if recovered is not None:
            return recovered

    snapshot = read_verified_operation_snapshot(root, operation["operation_id"])
    context = {**operation, "started_event_at": snapshot["events"][0]["timestamp"]}
    frozen: dict = {
        "retrieval_evidence": {
            "asset_id": asset_id,
            "source_id": source_id,
            "instance_id": instance_id,
            "retrieval_run_id": retrieval_run_id,
        },
        "candidate_evidence": {"candidate_rank": candidate_rank},
        "object_fingerprint": None,
        "model_points": [],
        "object_points": [],
        "symmetry_transforms": [],
        "coordinate_unit": "m",
    }
    config: dict = {"config_id": config_id, "config_fingerprint": None}
    description: EngineDescription | None = None
    hypotheses: list[dict] = []
    coarse: list[dict] = []
    fine: list[dict] = []
    try:
        frozen = load_registration_input(
            root,
            asset_id=asset_id,
            source_id=source_id,
            instance_id=instance_id,
            retrieval_run_id=retrieval_run_id,
            candidate_rank=candidate_rank,
            principal=principal,
        )
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.input_verified",
            {
                "object_fingerprint": frozen["object_fingerprint"],
                "representation_fingerprint": frozen["candidate_evidence"][
                    "representation_fingerprint"
                ],
            },
        )
        config = load_registration_config(root, config_id)
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.config_verified",
            {
                "config_id": config["config_id"],
                "config_fingerprint": config["config_fingerprint"],
            },
        )
        model_points, object_points = _validated_engine_points(frozen, config)
        try:
            engine = engine_resolver(config["engine_name"])
        except ModelMatchingError:
            raise
        except Exception as exc:
            raise ModelMatchingError(
                "registration_engine_unavailable", "Registration engine is unavailable."
            ) from exc
        description = _description(engine, config["engine_name"])
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.engine_resolved",
            asdict(description),
        )
        prepared = engine.preprocess(model_points, object_points, config)
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.preprocessed",
            {"model_point_count": len(model_points), "object_point_count": len(object_points)},
        )
        hypotheses = generate_initial_hypotheses(
            model_points,
            object_points,
            frozen["symmetry_transforms"],
            config["initial_hypotheses"],
        )
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.hypotheses_generated",
            {"count": len(hypotheses)},
        )
        coarse = _plain(engine.coarse_register(prepared, hypotheses, config))
        if not coarse:
            gate = {
                "status": "rejected",
                "reasons": ["coarse_registration_failed"],
                "fine_regression_ratio": None,
            }
            return _finish_completed(
                root, context, registration_id, frozen, config, description,
                hypotheses, coarse, [], None, {}, gate
            )
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.coarse_completed",
            {"count": len(coarse)},
        )
        fine = _plain(engine.fine_register(prepared, coarse, config))
        if not fine:
            gate = {
                "status": "rejected",
                "reasons": ["fine_registration_failed"],
                "fine_regression_ratio": None,
            }
            return _finish_completed(
                root, context, registration_id, frozen, config, description,
                hypotheses, coarse, fine, None, {}, gate
            )
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.fine_completed",
            {"count": len(fine)},
        )
        selected, margin, equivalent = _select_final_hypothesis(coarse, fine, config)
        try:
            validated = validate_rigid_transform(
                selected["matrix"], config["transform_validation"]
            )
        except ModelMatchingError as exc:
            if exc.code == "non_rigid_transform":
                raise ModelMatchingError(
                    "registration_engine_failed",
                    "Registration engine returned a non-rigid transform.",
                ) from exc
            raise
        ensure_operation_event(
            root,
            operation_id,
            "model_registration.transform_validated",
            {key: value for key, value in validated.items() if key != "matrix"},
        )
        evidence = engine.nearest_neighbor_evidence(
            prepared, np.asarray(validated["matrix"]), config
        )
        metrics = compute_registration_metrics(
            model_points,
            object_points,
            np.asarray(validated["matrix"]),
            evidence,
            config["residual_metrics"],
        )
        gate = evaluate_registration_gate(
            {**metrics, "category_id": frozen["candidate_evidence"]["category_id"]},
            coarse_metrics=selected["coarse_metrics"],
            fine_metrics=selected["fine_metrics"],
            pose_score_margin=margin,
            symmetry_equivalent=equivalent,
            config=config,
        )
        return _finish_completed(
            root, context, registration_id, frozen, config, description,
            hypotheses, coarse, fine, validated, metrics, gate
        )
    except Exception as exc:
        error = exc if isinstance(exc, ModelMatchingError) else ModelMatchingError(
            "registration_engine_failed", "Registration engine execution failed."
        )
        cause = exc.__cause__
        cause_code = cause.code if isinstance(cause, ModelMatchingError) else None
        diagnostic_error = {
            "code": error.code,
            "message": str(error),
            "cause_code": cause_code,
        }
        if error.code in {"operation_busy", "publication_recovery_required"}:
            raise error
        try:
            report, artifacts = _build_registration_artifacts(
                frozen=frozen,
                config=config,
                description=description,
                hypotheses=hypotheses,
                coarse=coarse,
                fine=fine,
                validated=None,
                metrics={},
                gate={},
                operation=context,
                registration_id=registration_id,
                error=diagnostic_error,
            )
            published = _publish_registration_artifacts(
                root, report=report, artifacts=artifacts, operation=context
            )
            _record_publication(root, operation_id, published)
            fail_operation(root, operation_id, error.code, str(error))
        except ModelMatchingError as publication_error:
            if publication_error.code in {
                "operation_busy",
                "publication_recovery_required",
            }:
                raise
            current = load_operation(root, operation_id)
            if current["status"] == "running":
                fail_operation(root, operation_id, error.code, str(error))
        raise error


def _finish_completed(
    root: Path,
    operation: dict,
    registration_id: str,
    frozen: dict,
    config: dict,
    description: EngineDescription,
    hypotheses: list[dict],
    coarse: list[dict],
    fine: list[dict],
    validated: dict | None,
    metrics: dict,
    gate: dict,
) -> dict:
    report, artifacts = _build_registration_artifacts(
        frozen=frozen,
        config=config,
        description=description,
        hypotheses=hypotheses,
        coarse=coarse,
        fine=fine,
        validated=validated,
        metrics=metrics,
        gate=gate,
        operation=operation,
        registration_id=registration_id,
    )
    published = _publish_registration_artifacts(
        root, report=report, artifacts=artifacts, operation=operation
    )
    _record_publication(root, operation["operation_id"], published)
    try:
        complete_operation(root, operation["operation_id"], _result(published))
    except ModelMatchingError as exc:
        raise ModelMatchingError(
            "publication_recovery_required",
            "Registration report is visible but audit completion is pending.",
        ) from exc
    return load_model_registration(
        root,
        asset_id=published["asset_id"],
        source_id=published["source_id"],
        instance_id=published["instance_id"],
        registration_id=published["registration_id"],
    )
