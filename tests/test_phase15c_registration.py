import hashlib
import json

import pytest

import pc_system.model_registration as registration_module
from pc_system.model_matching_audit import read_verified_operation_snapshot
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_registration import (
    load_model_registration,
    register_model_candidate,
)
from pc_system.model_sampling import _canonical_json_bytes
from phase15c_support import (
    EXPERT,
    DeterministicRegistrationEngine,
    prepare_phase15c_case,
)


def _arguments(prepared, *, sequence=1):
    return {
        **prepared,
        "registration_id": f"registration-{sequence}",
        "candidate_rank": 1,
        "principal": EXPERT,
        "operation_id": f"op-registration-{sequence}",
        "request_id": f"req-registration-{sequence}",
        "idempotency_key": f"idem-registration-{sequence}",
    }


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("passed", "passed"),
        ("review_required", "review_required"),
        ("rejected", "rejected"),
    ],
)
def test_registration_publishes_audited_completed_report(tmp_path, mode, expected):
    prepared = prepare_phase15c_case(tmp_path)
    engine = DeterministicRegistrationEngine(mode)

    report = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: engine,
        **_arguments(prepared),
    )

    assert report["status"] == "completed"
    assert report["gate_status"] == expected
    assert report["rigid_transform_4x4"][0][3] == pytest.approx(1.0)
    assert report["candidate_rank"] == 1
    assert len(report["artifacts"]) == 6
    snapshot = read_verified_operation_snapshot(tmp_path, "op-registration-1")
    assert snapshot["operation"]["status"] == "completed"
    assert snapshot["operation"]["result"]["report_fingerprint"] == report[
        "report_fingerprint"
    ]


def test_completed_replay_does_not_invoke_engine_again(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    first = DeterministicRegistrationEngine()
    arguments = _arguments(prepared)
    report = register_model_candidate(
        tmp_path, engine_resolver=lambda _name: first, **arguments
    )

    def forbidden(_name):
        raise AssertionError("completed replay must not resolve an engine")

    replayed = register_model_candidate(
        tmp_path, engine_resolver=forbidden, **arguments
    )

    assert replayed == report


def test_same_idempotency_key_with_different_candidate_conflicts(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    arguments = _arguments(prepared)
    register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        **arguments,
    )

    with pytest.raises(ModelMatchingError) as captured:
        register_model_candidate(
            tmp_path,
            engine_resolver=lambda _name: DeterministicRegistrationEngine(),
            **{**arguments, "candidate_rank": 2, "operation_id": "op-registration-2"},
        )

    assert captured.value.code == "idempotency_conflict"


def test_engine_unavailable_publishes_diagnostic_failed_report(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)

    def unavailable(_name):
        raise ModelMatchingError(
            "registration_engine_unavailable", "Registration engine is unavailable."
        )

    with pytest.raises(ModelMatchingError) as captured:
        register_model_candidate(
            tmp_path, engine_resolver=unavailable, **_arguments(prepared)
        )

    assert captured.value.code == "registration_engine_unavailable"
    report = load_model_registration(
        tmp_path,
        asset_id=prepared["asset_id"],
        source_id=prepared["source_id"],
        instance_id=prepared["instance_id"],
        registration_id="registration-1",
    )
    assert report["status"] == "failed"
    assert report["gate_status"] is None
    assert report["rigid_transform_4x4"] is None
    assert report["error"]["code"] == "registration_engine_unavailable"


def test_non_rigid_engine_output_fails_before_residual_metrics(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    engine = DeterministicRegistrationEngine("non_rigid")

    with pytest.raises(ModelMatchingError) as captured:
        register_model_candidate(
            tmp_path,
            engine_resolver=lambda _name: engine,
            **_arguments(prepared),
        )

    assert captured.value.code == "registration_engine_failed"
    assert engine.calls["nearest_neighbor_evidence"] == 0
    report = load_model_registration(
        tmp_path,
        asset_id=prepared["asset_id"],
        source_id=prepared["source_id"],
        instance_id=prepared["instance_id"],
        registration_id="registration-1",
    )
    assert report["error"]["cause_code"] == "non_rigid_transform"


def test_coarse_and_fine_empty_results_are_quality_rejections(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    coarse = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(
            "coarse_failed"
        ),
        **_arguments(prepared),
    )
    fine = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine("fine_failed"),
        **_arguments(prepared, sequence=2),
    )

    assert coarse["status"] == fine["status"] == "completed"
    assert coarse["gate_status"] == fine["gate_status"] == "rejected"
    assert "coarse_registration_failed" in coarse["gate_reasons"]
    assert "fine_registration_failed" in fine["gate_reasons"]


def test_visible_report_recovers_audit_without_rerunning_engine(tmp_path, monkeypatch):
    prepared = prepare_phase15c_case(tmp_path)
    engine = DeterministicRegistrationEngine()
    real_complete = registration_module.complete_operation
    calls = {"count": 0}

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ModelMatchingError(
                "audit_persistence_error", "Completion confirmation failed."
            )
        return real_complete(*args, **kwargs)

    monkeypatch.setattr(registration_module, "complete_operation", fail_once)
    arguments = _arguments(prepared)
    with pytest.raises(ModelMatchingError) as captured:
        register_model_candidate(
            tmp_path, engine_resolver=lambda _name: engine, **arguments
        )
    assert captured.value.code == "publication_recovery_required"
    calls_before = dict(engine.calls)

    recovered = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: (_ for _ in ()).throw(
            AssertionError("recovery must not resolve engine")
        ),
        **arguments,
    )

    assert recovered["status"] == "completed"
    assert engine.calls == calls_before


def test_partial_artifact_publication_is_not_loadable_and_retries_in_place(
    tmp_path, monkeypatch
):
    prepared = prepare_phase15c_case(tmp_path)
    real_publish = registration_module._publish_exact_json
    calls = {"count": 0}

    def interrupt(path, value, **kwargs):
        calls["count"] += 1
        if calls["count"] == 4:
            raise ModelMatchingError(
                "publication_recovery_required", "Publication was interrupted."
            )
        return real_publish(path, value, **kwargs)

    monkeypatch.setattr(registration_module, "_publish_exact_json", interrupt)
    arguments = _arguments(prepared)
    with pytest.raises(ModelMatchingError) as captured:
        register_model_candidate(
            tmp_path,
            engine_resolver=lambda _name: DeterministicRegistrationEngine(),
            **arguments,
        )
    assert captured.value.code == "publication_recovery_required"
    with pytest.raises(ModelMatchingError) as not_ready:
        load_model_registration(
            tmp_path,
            asset_id=prepared["asset_id"],
            source_id=prepared["source_id"],
            instance_id=prepared["instance_id"],
            registration_id="registration-1",
        )
    assert not_ready.value.code == "publication_recovery_required"

    monkeypatch.setattr(registration_module, "_publish_exact_json", real_publish)
    recovered = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        **arguments,
    )
    assert recovered["status"] == "completed"


def test_each_declared_artifact_is_verified_on_read(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    report = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        **_arguments(prepared),
    )
    directory = (
        tmp_path
        / "reports"
        / "model_registrations"
        / prepared["asset_id"]
        / prepared["source_id"]
        / prepared["instance_id"]
        / "registration-1"
    )
    for filename in report["artifacts"]:
        path = directory / filename
        original = path.read_bytes()
        value = json.loads(original.decode("utf-8"))
        value["tampered"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ModelMatchingError) as captured:
            load_model_registration(
                tmp_path,
                asset_id=prepared["asset_id"],
                source_id=prepared["source_id"],
                instance_id=prepared["instance_id"],
                registration_id="registration-1",
            )
        assert captured.value.code == "artifact_integrity_failed"
        path.write_bytes(original)


def test_report_uses_metrics_from_stably_selected_best_hypothesis(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)

    class OutOfOrderEngine(DeterministicRegistrationEngine):
        def fine_register(self, prepared_points, coarse_results, config):
            best = super().fine_register(prepared_points, coarse_results, config)[0]
            lower = {
                **best,
                "hypothesis_id": "hypothesis-lower",
                "score": 0.90,
                "coarse_metrics": {"rmse_m": 0.020, "fitness": 0.80},
                "fine_metrics": {"rmse_m": 0.019, "fitness": 0.81},
            }
            best = {
                **best,
                "hypothesis_id": "hypothesis-best",
                "score": 0.98,
                "coarse_metrics": {"rmse_m": 0.016, "fitness": 0.92},
                "fine_metrics": {"rmse_m": 0.012, "fitness": 0.97},
            }
            return [lower, best]

    report = register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: OutOfOrderEngine(),
        **_arguments(prepared),
    )

    assert report["coarse_metrics"]["rmse_m"] == pytest.approx(0.016)
    assert report["fine_metrics"]["rmse_m"] == pytest.approx(0.012)


def test_core_rejects_point_count_outside_config_before_engine_call(
    tmp_path, monkeypatch
):
    prepared = prepare_phase15c_case(tmp_path)
    frozen = registration_module.load_registration_input(
        tmp_path,
        asset_id=prepared["asset_id"],
        source_id=prepared["source_id"],
        instance_id=prepared["instance_id"],
        retrieval_run_id=prepared["retrieval_run_id"],
        candidate_rank=1,
        principal=EXPERT,
    )
    frozen["model_points"] = frozen["model_points"][:2]
    monkeypatch.setattr(
        registration_module,
        "load_registration_input",
        lambda *args, **kwargs: frozen,
    )
    engine = DeterministicRegistrationEngine()

    with pytest.raises(ModelMatchingError) as captured:
        register_model_candidate(
            tmp_path,
            engine_resolver=lambda _name: engine,
            **_arguments(prepared),
        )

    assert captured.value.code == "registration_input_incomplete"
    assert engine.calls["preprocess"] == 0


def test_registration_owner_request_binding_is_verified(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        **_arguments(prepared),
    )
    directory = (
        tmp_path
        / "reports"
        / "model_registrations"
        / prepared["asset_id"]
        / prepared["source_id"]
        / prepared["instance_id"]
        / "registration-1"
    )
    owner_path = directory / "operation_owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["request_id"] = "req-unrelated"
    owner_path.write_bytes(_canonical_json_bytes(owner))

    with pytest.raises(ModelMatchingError) as captured:
        load_model_registration(
            tmp_path,
            asset_id=prepared["asset_id"],
            source_id=prepared["source_id"],
            instance_id=prepared["instance_id"],
            registration_id="registration-1",
        )

    assert captured.value.code == "artifact_integrity_failed"


def test_recovery_loader_cross_checks_gate_artifact_against_report(tmp_path):
    prepared = prepare_phase15c_case(tmp_path)
    register_model_candidate(
        tmp_path,
        engine_resolver=lambda _name: DeterministicRegistrationEngine(),
        **_arguments(prepared),
    )
    directory = (
        tmp_path
        / "reports"
        / "model_registrations"
        / prepared["asset_id"]
        / prepared["source_id"]
        / prepared["instance_id"]
        / "registration-1"
    )
    residual_path = directory / "residual_report.json"
    residual = json.loads(residual_path.read_text(encoding="utf-8"))
    residual["gate"]["status"] = "rejected"
    residual_payload = _canonical_json_bytes(residual)
    residual_path.write_bytes(residual_payload)
    report_path = directory / "registration_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["artifacts"]["residual_report.json"] = hashlib.sha256(
        residual_payload
    ).hexdigest()
    report.pop("report_fingerprint")
    report["report_fingerprint"] = registration_module._canonical_fingerprint(report)
    report_path.write_bytes(_canonical_json_bytes(report))

    with pytest.raises(ModelMatchingError) as captured:
        registration_module._load_published_registration(
            tmp_path,
            asset_id=prepared["asset_id"],
            source_id=prepared["source_id"],
            instance_id=prepared["instance_id"],
            registration_id="registration-1",
            validate_audit=False,
        )

    assert captured.value.code == "artifact_integrity_failed"


def test_selected_hypothesis_rejects_non_boolean_symmetry_equivalence():
    with pytest.raises(ModelMatchingError) as captured:
        registration_module._select_final_hypothesis(
            [],
            [
                {
                    "hypothesis_id": "hypothesis-1",
                    "score": 0.9,
                    "symmetry_equivalent": "yes",
                }
            ],
            {},
        )

    assert captured.value.code == "registration_engine_failed"
