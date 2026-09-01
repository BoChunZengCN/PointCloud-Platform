import copy

import numpy as np
import pytest

o3d = pytest.importorskip("open3d")

from pc_system.model_registration_open3d import Open3DRegistrationEngine
from pc_system.model_registration_transform import validate_rigid_transform
from phase15c_support import REGISTRATION_V1


def test_open3d_icp_preserves_model_to_object_transform_direction():
    generator = np.random.default_rng(20260901)
    model = generator.uniform([-1.0, -0.4, -0.2], [1.2, 0.7, 0.9], size=(500, 3))
    angle = 0.04
    rotation = np.asarray(
        [
            [np.cos(angle), -np.sin(angle), 0.0],
            [np.sin(angle), np.cos(angle), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    translation = np.asarray([0.12, -0.08, 0.05])
    observed = (rotation @ model.T).T + translation
    expected = np.eye(4)
    expected[:3, :3] = rotation
    expected[:3, 3] = translation
    config = copy.deepcopy(REGISTRATION_V1)
    config["engine_name"] = "open3d"
    engine = Open3DRegistrationEngine(o3d)
    prepared = engine.preprocess(model, observed, config)

    results = engine.fine_register(
        prepared,
        [
            {
                "hypothesis_id": "synthetic-exact",
                "source": "test",
                "matrix": expected.tolist(),
                "score": 1.0,
                "coarse_metrics": {"rmse_m": 0.0, "fitness": 1.0},
            }
        ],
        config,
    )

    assert results
    validated = validate_rigid_transform(
        results[0]["matrix"], config["transform_validation"]
    )
    transformed = (
        np.asarray(validated["matrix"])[:3, :3] @ model.T
    ).T + np.asarray(validated["matrix"])[:3, 3]
    assert np.sqrt(np.mean(np.square(transformed - observed))) < 0.01
