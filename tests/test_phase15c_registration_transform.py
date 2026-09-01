import copy
import importlib
import importlib.util
import math

import numpy as np
import pytest

from pc_system.model_matching_errors import ModelMatchingError
from phase15c_support import MODEL_POINTS, OBJECT_POINTS, REGISTRATION_V1


def _module():
    assert importlib.util.find_spec("pc_system.model_registration_transform") is not None
    return importlib.import_module("pc_system.model_registration_transform")


def _translation_matrix(x=1.0, y=2.0, z=3.0):
    return [
        [1.0, 0.0, 0.0, x],
        [0.0, 1.0, 0.0, y],
        [0.0, 0.0, 1.0, z],
        [0.0, 0.0, 0.0, 1.0],
    ]


def test_validate_rigid_transform_preserves_model_to_object_direction():
    module = _module()

    result = module.validate_rigid_transform(
        _translation_matrix(), REGISTRATION_V1["transform_validation"]
    )
    transformed = np.asarray(result["matrix"], dtype=float) @ np.asarray(
        [*MODEL_POINTS[0], 1.0]
    )

    assert transformed[:3].tolist() == pytest.approx(OBJECT_POINTS[0])
    assert result["translation_m"] == pytest.approx(math.sqrt(14.0))
    assert result["rotation_rad"] == pytest.approx(0.0)
    assert result["determinant"] == pytest.approx(1.0)
    assert result["singular_values"] == pytest.approx([1.0, 1.0, 1.0])


@pytest.mark.parametrize(
    "matrix",
    [
        [[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]],
        [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        [[1, 0.1, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1, 1]],
        [[1, 0, 0, float("nan")], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    ],
)
def test_validate_rigid_transform_rejects_non_rigid_or_nonfinite_matrix(matrix):
    module = _module()

    with pytest.raises(ModelMatchingError) as captured:
        module.validate_rigid_transform(
            matrix, REGISTRATION_V1["transform_validation"]
        )

    assert captured.value.code == "non_rigid_transform"


def test_validate_rigid_transform_enforces_translation_and_rotation_policy():
    module = _module()
    policy = copy.deepcopy(REGISTRATION_V1["transform_validation"])
    policy["maximum_translation_m"] = 1.0
    with pytest.raises(ModelMatchingError) as translation_error:
        module.validate_rigid_transform(_translation_matrix(), policy)
    assert translation_error.value.code == "non_rigid_transform"

    policy = copy.deepcopy(REGISTRATION_V1["transform_validation"])
    policy["maximum_rotation_rad"] = 0.1
    quarter_turn = [
        [0.0, -1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    with pytest.raises(ModelMatchingError) as rotation_error:
        module.validate_rigid_transform(quarter_turn, policy)
    assert rotation_error.value.code == "non_rigid_transform"


def test_initial_hypotheses_center_model_on_object_and_keep_identity_first():
    module = _module()

    hypotheses = module.generate_initial_hypotheses(
        np.asarray(MODEL_POINTS, dtype=float),
        np.asarray(OBJECT_POINTS, dtype=float),
        [],
        REGISTRATION_V1["initial_hypotheses"],
    )

    assert 1 <= len(hypotheses) <= 24
    assert hypotheses[0]["source"] == "identity"
    assert np.asarray(hypotheses[0]["matrix"])[:3, 3].tolist() == pytest.approx(
        [1.0, 2.0, 3.0]
    )
    assert [item["hypothesis_id"] for item in hypotheses] == [
        f"hypothesis-{index:03d}" for index in range(1, len(hypotheses) + 1)
    ]


def test_initial_hypotheses_deduplicate_symmetry_and_obey_maximum():
    module = _module()
    config = copy.deepcopy(REGISTRATION_V1["initial_hypotheses"])
    config["include_principal_axes"] = False
    config["maximum_hypotheses"] = 2
    half_turn = [
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, -1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]

    hypotheses = module.generate_initial_hypotheses(
        np.asarray(MODEL_POINTS, dtype=float),
        np.asarray(OBJECT_POINTS, dtype=float),
        [half_turn, half_turn],
        config,
    )

    assert len(hypotheses) == 2
    assert [item["source"] for item in hypotheses] == [
        "identity",
        "declared_symmetry",
    ]
