import copy
import importlib
import importlib.util
import json

import pytest

from pc_system.model_matching_audit import load_operation
from pc_system.model_matching_errors import ModelMatchingError
from phase15c_support import AUDITOR, EXPERT, REGISTRATION_V1


def _module():
    assert importlib.util.find_spec("pc_system.model_registration_config") is not None
    return importlib.import_module("pc_system.model_registration_config")


def _publish(module, root, *, sequence=1, config=None):
    return module.publish_registration_config(
        root,
        config_id="registration-v1",
        config=REGISTRATION_V1 if config is None else config,
        principal=EXPERT,
        operation_id=f"op-registration-config-{sequence}",
        request_id=f"req-registration-config-{sequence}",
        idempotency_key=f"idem-registration-config-{sequence}",
    )


def test_build_registration_config_is_canonical_and_fingerprinted():
    module = _module()

    config = module.build_registration_config("registration-v1", REGISTRATION_V1)

    assert config["schema_version"] == "1.0"
    assert config["config_id"] == "registration-v1"
    assert config["engine_name"] == "deterministic-test"
    assert config["fine_registration"]["levels"][2]["voxel_size_m"] == 0.02
    assert len(config["config_fingerprint"]) == 64


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["coarse_registration"].__setitem__(
                "maximum_iterations", 0
            ),
            "maximum_iterations",
        ),
        (
            lambda value: value["preprocessing"].__setitem__(
                "normal_radius_multiplier", float("nan")
            ),
            "normal_radius_multiplier",
        ),
        (
            lambda value: value["fine_registration"].__setitem__("levels", []),
            "levels",
        ),
        (lambda value: value.__setitem__("unexpected", True), "structure"),
    ],
)
def test_build_registration_config_rejects_invalid_or_unbounded_values(
    mutate, message
):
    module = _module()
    value = copy.deepcopy(REGISTRATION_V1)
    mutate(value)

    with pytest.raises(ModelMatchingError, match=message) as captured:
        module.build_registration_config("registration-v1", value)

    assert captured.value.code == "registration_config_invalid"


def test_publish_registration_config_is_immutable_audited_and_replayable(tmp_path):
    module = _module()

    first = _publish(module, tmp_path)
    replay = _publish(module, tmp_path)
    reused = _publish(module, tmp_path, sequence=2)

    assert replay == first == reused
    assert module.load_registration_config(tmp_path, "registration-v1") == first
    assert module.list_registration_configs(tmp_path) == [first]
    assert load_operation(tmp_path, "op-registration-config-1")["status"] == "completed"
    assert load_operation(tmp_path, "op-registration-config-2")["status"] == "completed"

    changed = copy.deepcopy(REGISTRATION_V1)
    changed["quality_gates"]["maximum_chamfer_m"] = 0.05
    with pytest.raises(ModelMatchingError) as captured:
        _publish(module, tmp_path, sequence=3, config=changed)
    assert captured.value.code == "artifact_integrity_failed"


def test_registration_config_rejects_unauthorized_publish(tmp_path):
    module = _module()

    with pytest.raises(ModelMatchingError) as captured:
        module.publish_registration_config(
            tmp_path,
            config_id="registration-v1",
            config=REGISTRATION_V1,
            principal=AUDITOR,
            operation_id="op-registration-config-auditor",
            request_id="req-registration-config-auditor",
            idempotency_key="idem-registration-config-auditor",
        )

    assert captured.value.code == "permission_denied"
    assert load_operation(tmp_path, "op-registration-config-auditor")["status"] == "failed"


def test_registration_config_detects_noncanonical_or_changed_artifact(tmp_path):
    module = _module()
    _publish(module, tmp_path)
    path = (
        tmp_path
        / "models"
        / "registration_configs"
        / "registration-v1"
        / "registration_config.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(ModelMatchingError) as captured:
        module.load_registration_config(tmp_path, "registration-v1")

    assert captured.value.code == "artifact_integrity_failed"
