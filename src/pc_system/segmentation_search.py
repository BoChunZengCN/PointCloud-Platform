import itertools
import json
import math
import random
import time
from collections.abc import Callable
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json
from pc_system.segmentation_run import fingerprint_config, utc_now


SCORE_WEIGHT_KEYS = (
    "instance_f1",
    "point_miou",
    "mean_box_iou",
    "noise_f1",
    "over_segmentation",
    "under_segmentation",
    "runtime_seconds",
)


def _canonical_sort_key(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def generate_trial_configs(
    parameter_space: dict,
    *,
    strategy: str,
    max_trials: int,
    seed: int,
) -> list[dict]:
    """生成有界、确定且不重复的参数组合。"""

    if not isinstance(max_trials, int) or max_trials <= 0:
        raise ValueError("max_trials must be a positive integer.")
    if strategy not in {"grid", "random"}:
        raise ValueError(f"Unsupported search strategy: {strategy}")
    if not isinstance(parameter_space, dict) or not parameter_space:
        raise ValueError("parameter_space must contain at least one parameter.")
    names = sorted(parameter_space)
    value_lists = []
    for name in names:
        values = parameter_space[name]
        if not isinstance(values, list) or not values:
            raise ValueError(f"Search parameter requires a non-empty value list: {name}")
        unique = {
            _canonical_sort_key(value): value for value in values
        }
        value_lists.append([unique[key] for key in sorted(unique)])
    configs = [
        dict(zip(names, values))
        for values in itertools.product(*value_lists)
    ]
    count = min(max_trials, len(configs))
    if strategy == "grid":
        return configs[:count]
    indices = random.Random(int(seed)).sample(range(len(configs)), count)
    return [configs[index] for index in indices]


def _validated_weights(weights: dict) -> dict[str, float]:
    normalized = {}
    for key in SCORE_WEIGHT_KEYS:
        if key not in weights:
            raise ValueError(f"Search scoring weight is required: {key}")
        value = float(weights[key])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Search scoring weight must be non-negative: {key}")
        normalized[key] = value
    return normalized


def score_evaluation(summary: dict, weights: dict) -> float:
    """计算可审计的黄金评估综合评分。"""

    normalized = _validated_weights(weights)
    return (
        normalized["instance_f1"] * float(summary["instance_f1"])
        + normalized["point_miou"] * float(summary["point_miou"])
        + normalized["mean_box_iou"] * float(summary["mean_box_iou"])
        + normalized["noise_f1"] * float(summary["noise_f1"])
        - normalized["over_segmentation"]
        * float(summary["over_segmentation_count"])
        - normalized["under_segmentation"]
        * float(summary["under_segmentation_count"])
        - normalized["runtime_seconds"] * float(summary["runtime_seconds"])
    )


def run_parameter_search(
    project_root: Path,
    *,
    asset_id: str,
    search_id: str,
    search_config: dict,
    trial_runner: Callable[[str, dict], dict],
) -> dict:
    """执行有界参数试验并生成不自动应用的推荐。"""

    asset_id = validate_identifier(asset_id, "asset_id")
    search_id = validate_identifier(search_id, "search_id")
    output_dir = (
        project_root
        / "reports"
        / "segmentation_searches"
        / asset_id
        / search_id
    )
    if output_dir.exists():
        raise FileExistsError(output_dir)
    timeout = float(search_config.get("trial_timeout_seconds", 0))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("trial_timeout_seconds must be a positive finite number.")
    weights = _validated_weights(dict(search_config.get("weights", {})))
    trial_configs = generate_trial_configs(
        dict(search_config.get("parameter_space", {})),
        strategy=str(search_config.get("strategy", "grid")),
        max_trials=search_config.get("max_trials"),
        seed=int(search_config.get("seed", 0)),
    )
    search = {
        "schema_version": "1.0",
        "search_id": search_id,
        "asset_id": asset_id,
        "status": "planned",
        "strategy": str(search_config.get("strategy", "grid")),
        "seed": int(search_config.get("seed", 0)),
        "max_trials": int(search_config["max_trials"]),
        "trial_timeout_seconds": timeout,
        "parameter_space": dict(search_config["parameter_space"]),
        "weights": weights,
        "started_at": None,
        "completed_at": None,
        "trial_count": len(trial_configs),
        "completed_trial_count": 0,
        "failed_trial_count": 0,
        "recommendation": None,
    }
    write_json(search, output_dir / "search_run.json")
    search["status"] = "running"
    search["started_at"] = utc_now()
    write_json(search, output_dir / "search_run.json")
    completed_trials = []
    for sequence, config in enumerate(trial_configs, start=1):
        trial_id = f"trial-{sequence:04d}"
        trial = {
            "schema_version": "1.0",
            "trial_id": trial_id,
            "status": "running",
            "config": config,
            "config_fingerprint": fingerprint_config(config),
            "trial_timeout_seconds": timeout,
            "evaluation_id": None,
            "gate_status": None,
            "comparison_id": None,
            "runtime_seconds": None,
            "score": None,
            "error": None,
        }
        started = time.perf_counter()
        try:
            result = trial_runner(trial_id, dict(config))
            runtime_seconds = float(
                result.get("runtime_seconds", time.perf_counter() - started)
            )
            scoring_summary = {
                **dict(result["summary"]),
                "runtime_seconds": runtime_seconds,
            }
            trial.update(
                {
                    "status": "completed",
                    "evaluation_id": result["evaluation_id"],
                    "gate_status": str(
                        result.get("gate_status", "not_evaluated")
                    ),
                    "comparison_id": result.get("comparison_id"),
                    "runtime_seconds": runtime_seconds,
                    "summary": dict(result["summary"]),
                    "score": score_evaluation(scoring_summary, weights),
                }
            )
            completed_trials.append(trial)
        except Exception as exc:
            trial.update(
                {
                    "status": "failed",
                    "runtime_seconds": time.perf_counter() - started,
                    "error": {
                        "code": "trial_failed",
                        "message": str(exc),
                    },
                }
            )
        write_json(trial, output_dir / "trials" / f"{trial_id}.json")

    eligible = [
        trial
        for trial in completed_trials
        if trial.get("gate_status") in {"passed", "not_evaluated"}
    ]
    eligible.sort(
        key=lambda trial: (
            -float(trial["score"]),
            float(trial["runtime_seconds"]),
            str(trial["config_fingerprint"]),
        )
    )
    if eligible:
        best = eligible[0]
        recommendation = {
            "schema_version": "1.0",
            "status": "recommended",
            "search_id": search_id,
            "trial_id": best["trial_id"],
            "evaluation_id": best["evaluation_id"],
            "gate_status": best["gate_status"],
            "comparison_id": best["comparison_id"],
            "score": best["score"],
            "runtime_seconds": best["runtime_seconds"],
            "config": best["config"],
            "config_fingerprint": best["config_fingerprint"],
            "advisory_only": True,
        }
    else:
        recommendation = {
            "schema_version": "1.0",
            "status": "no_eligible_trial",
            "search_id": search_id,
            "trial_id": None,
            "evaluation_id": None,
            "gate_status": None,
            "comparison_id": None,
            "score": None,
            "config": None,
            "advisory_only": True,
        }
    write_json(recommendation, output_dir / "recommendation.json")
    search["status"] = "completed"
    search["completed_at"] = utc_now()
    search["completed_trial_count"] = len(completed_trials)
    search["failed_trial_count"] = len(trial_configs) - len(completed_trials)
    search["recommendation"] = recommendation
    write_json(search, output_dir / "search_run.json")
    return search
