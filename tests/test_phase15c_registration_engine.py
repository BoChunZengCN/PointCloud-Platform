from dataclasses import FrozenInstanceError
import importlib
import importlib.util

import pytest


def _module():
    assert importlib.util.find_spec("pc_system.model_registration_engine") is not None
    return importlib.import_module("pc_system.model_registration_engine")


def test_engine_description_is_immutable_and_protocol_is_runtime_checkable():
    module = _module()

    class CompleteEngine:
        def describe(self):
            return module.EngineDescription(
                name="deterministic-test", version="1.0", production=False
            )

        def preprocess(self, model_points, object_points, config):
            return {"model_points": model_points, "object_points": object_points}

        def coarse_register(self, prepared, hypotheses, config):
            return []

        def fine_register(self, prepared, coarse_results, config):
            return []

        def nearest_neighbor_evidence(self, prepared, transform, config):
            return {
                "observed_to_model_distances_m": [],
                "model_to_observed_distances_m": [],
                "normal_cosines": None,
            }

    engine = CompleteEngine()
    description = engine.describe()

    assert isinstance(engine, module.RegistrationEngine)
    assert description == module.EngineDescription(
        name="deterministic-test", version="1.0", production=False
    )
    with pytest.raises(FrozenInstanceError):
        description.production = True


def test_incomplete_engine_does_not_satisfy_protocol():
    module = _module()

    class IncompleteEngine:
        def describe(self):
            return module.EngineDescription("incomplete", "1.0", False)

    assert not isinstance(IncompleteEngine(), module.RegistrationEngine)
