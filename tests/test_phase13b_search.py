import json

import pytest

from pc_system.segmentation_search import (
    generate_trial_configs,
    run_parameter_search,
    score_evaluation,
)


def weights():
    return {
        "instance_f1": 0.4,
        "point_miou": 0.25,
        "mean_box_iou": 0.2,
        "noise_f1": 0.15,
        "over_segmentation": 0.05,
        "under_segmentation": 0.05,
        "runtime_seconds": 0.001,
    }


def search_config(**overrides):
    result = {
        "strategy": "grid",
        "parameter_space": {
            "distance_threshold": [0.1, 0.2],
            "min_points": [1, 2],
        },
        "max_trials": 4,
        "seed": 17,
        "trial_timeout_seconds": 30,
        "weights": weights(),
    }
    result.update(overrides)
    return result


def trial_result(
    *,
    evaluation_id="eval",
    gate_status="passed",
    instance_f1=0.8,
    runtime_seconds=1.0,
):
    return {
        "evaluation_id": evaluation_id,
        "gate_status": gate_status,
        "runtime_seconds": runtime_seconds,
        "summary": {
            "instance_f1": instance_f1,
            "point_miou": 0.75,
            "mean_box_iou": 0.7,
            "noise_f1": 0.9,
            "over_segmentation_count": 0,
            "under_segmentation_count": 0,
        },
    }


def test_grid_search_is_lexicographic_and_bounded():
    configs = generate_trial_configs(
        {
            "min_points": [1, 2],
            "distance_threshold": [0.1, 0.2],
        },
        strategy="grid",
        max_trials=3,
        seed=0,
    )

    assert configs == [
        {"distance_threshold": 0.1, "min_points": 1},
        {"distance_threshold": 0.1, "min_points": 2},
        {"distance_threshold": 0.2, "min_points": 1},
    ]


def test_random_search_is_repeatable_for_fixed_seed():
    arguments = {
        "parameter_space": {"min_points": [1, 2, 3], "voxel_size": [0.1, 0.2]},
        "strategy": "random",
        "max_trials": 4,
        "seed": 23,
    }

    assert generate_trial_configs(**arguments) == generate_trial_configs(**arguments)


def test_random_search_has_no_duplicate_trials():
    configs = generate_trial_configs(
        {"min_points": [1, 2, 3], "voxel_size": [0.1, 0.2]},
        strategy="random",
        max_trials=6,
        seed=23,
    )

    assert len(configs) == len({json.dumps(item, sort_keys=True) for item in configs})


@pytest.mark.parametrize("max_trials", [None, 0, -1])
def test_search_rejects_missing_or_nonpositive_budget(max_trials):
    kwargs = {
        "strategy": "grid",
        "seed": 0,
    }
    if max_trials is not None:
        kwargs["max_trials"] = max_trials

    with pytest.raises((TypeError, ValueError), match="max_trials"):
        generate_trial_configs({"min_points": [1]}, **kwargs)


def test_composite_score_uses_metrics_and_penalties():
    summary = trial_result()["summary"] | {"runtime_seconds": 2.0}

    score = score_evaluation(summary, weights())

    assert score == pytest.approx(
        0.4 * 0.8 + 0.25 * 0.75 + 0.2 * 0.7 + 0.15 * 0.9 - 0.001 * 2
    )


def test_failed_trial_is_retained_and_later_trials_continue(tmp_path):
    calls = []

    def trial_runner(trial_id, config):
        calls.append(trial_id)
        if config["min_points"] == 1:
            raise RuntimeError("trial failed")
        return trial_result(
            evaluation_id=f"{trial_id}-evaluation", instance_f1=0.9
        )

    search = run_parameter_search(
        tmp_path,
        asset_id="scan",
        search_id="search-001",
        search_config=search_config(
            parameter_space={"min_points": [1, 2]},
            max_trials=2,
        ),
        trial_runner=trial_runner,
    )

    assert calls == ["trial-0001", "trial-0002"]
    assert search["failed_trial_count"] == 1
    assert search["completed_trial_count"] == 1
    failed = json.loads(
        (
            tmp_path
            / "reports"
            / "segmentation_searches"
            / "scan"
            / "search-001"
            / "trials"
            / "trial-0001.json"
        ).read_text(encoding="utf-8")
    )
    assert failed["status"] == "failed"
    assert search["recommendation"]["trial_id"] == "trial-0002"


def test_recommendation_excludes_failed_regression_gate(tmp_path):
    def trial_runner(trial_id, config):
        return trial_result(
            evaluation_id=f"{trial_id}-evaluation",
            gate_status="failed" if config["min_points"] == 1 else "passed",
            instance_f1=0.99 if config["min_points"] == 1 else 0.8,
        )

    search = run_parameter_search(
        tmp_path,
        asset_id="scan",
        search_id="search-001",
        search_config=search_config(
            parameter_space={"min_points": [1, 2]},
            max_trials=2,
        ),
        trial_runner=trial_runner,
    )

    assert search["recommendation"]["trial_id"] == "trial-0002"


def test_search_records_per_trial_timeout_metadata(tmp_path):
    search = run_parameter_search(
        tmp_path,
        asset_id="scan",
        search_id="search-001",
        search_config=search_config(
            parameter_space={"min_points": [1]},
            max_trials=1,
            trial_timeout_seconds=12,
        ),
        trial_runner=lambda trial_id, config: trial_result(),
    )
    trial = json.loads(
        (
            tmp_path
            / "reports"
            / "segmentation_searches"
            / "scan"
            / "search-001"
            / "trials"
            / "trial-0001.json"
        ).read_text(encoding="utf-8")
    )

    assert search["trial_timeout_seconds"] == 12
    assert trial["trial_timeout_seconds"] == 12


def test_search_never_mutates_production_config(tmp_path):
    production_config = tmp_path / "config" / "segmentation.json"
    production_config.parent.mkdir()
    production_config.write_text('{"min_points": 99}', encoding="utf-8")
    before = production_config.read_text(encoding="utf-8")

    run_parameter_search(
        tmp_path,
        asset_id="scan",
        search_id="search-001",
        search_config=search_config(
            parameter_space={"min_points": [1]},
            max_trials=1,
        ),
        trial_runner=lambda trial_id, config: trial_result(),
    )

    assert production_config.read_text(encoding="utf-8") == before

