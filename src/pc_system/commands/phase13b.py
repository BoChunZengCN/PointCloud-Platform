import json
import sys
import time
from pathlib import Path

from pc_system.las_sampling import sample_points_from_source
from pc_system.segmentation_benchmarks import (
    import_benchmark,
    load_benchmark_sample,
)
from pc_system.segmentation_evaluation import evaluate_segmentation_run
from pc_system.segmentation_regression import compare_evaluations
from pc_system.segmentation_search import run_parameter_search
from pc_system.segmentation_service import run_segmentation


def _load_json_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Configuration must be a JSON object: {path}")
    return payload


def run_import_segmentation_benchmark(
    project_root: Path, manifest_path: Path
) -> int:
    manifest = import_benchmark(project_root, manifest_path)
    print(project_root / "benchmarks" / manifest["benchmark_id"] / "benchmark.json")
    return 0


def run_evaluate_segmentation(
    project_root: Path,
    *,
    asset_id: str,
    run_id: str,
    benchmark_id: str,
    sample_id: str,
    evaluation_id: str,
    config_path: Path,
) -> int:
    evaluate_segmentation_run(
        project_root,
        asset_id=asset_id,
        run_id=run_id,
        benchmark_id=benchmark_id,
        sample_id=sample_id,
        evaluation_id=evaluation_id,
        config=_load_json_object(config_path),
    )
    print(
        project_root
        / "reports"
        / "segmentation_evaluations"
        / asset_id
        / evaluation_id
        / "evaluation_run.json"
    )
    return 0


def run_compare_segmentation(
    project_root: Path,
    *,
    asset_id: str,
    comparison_id: str,
    baseline_evaluation_id: str,
    candidate_evaluation_id: str,
    thresholds_path: Path,
) -> int:
    compare_evaluations(
        project_root,
        asset_id=asset_id,
        comparison_id=comparison_id,
        baseline_evaluation_id=baseline_evaluation_id,
        candidate_evaluation_id=candidate_evaluation_id,
        thresholds=_load_json_object(thresholds_path),
    )
    print(
        project_root
        / "reports"
        / "segmentation_comparisons"
        / asset_id
        / comparison_id
        / "regression_gate.json"
    )
    return 0


def _sample_metadata(manifest: dict, sample_id: str) -> dict:
    sample = next(
        (
            item
            for item in manifest.get("samples", [])
            if item.get("sample_id") == sample_id
        ),
        None,
    )
    if sample is None:
        raise KeyError(f"Benchmark sample not found: {sample_id}")
    return sample


def run_search_segmentation(
    project_root: Path,
    *,
    asset_id: str,
    benchmark_id: str,
    sample_id: str,
    search_id: str,
    config_path: Path,
    baseline_evaluation_id: str | None,
) -> int:
    search_config = _load_json_object(config_path)
    manifest, _ = load_benchmark_sample(project_root, benchmark_id, sample_id)
    sample = _sample_metadata(manifest, sample_id)
    if sample.get("asset_id") != asset_id:
        raise ValueError("Benchmark sample asset_id does not match search asset_id.")
    source_path = Path(sample["source_uri"])
    if not source_path.is_absolute() and not source_path.exists():
        source_path = project_root / source_path
    base_config = dict(search_config.get("base_config", {}))
    evaluation_config = dict(search_config.get("evaluation_config", {}))
    max_points = int(base_config.get("max_points", 10000))
    points = sample_points_from_source(source_path, max_points=max_points)

    def trial_runner(trial_id: str, trial_config: dict) -> dict:
        started = time.perf_counter()
        segmentation_run_id = f"{search_id}-{trial_id}"
        evaluation_id = f"{segmentation_run_id}-eval"
        config = {**base_config, **trial_config, "max_points": max_points}
        run_segmentation(
            project_root,
            asset_id=asset_id,
            asset_version=str(sample.get("asset_version", "1.0")),
            source_uri=str(source_path),
            points=points,
            config=config,
            run_id=segmentation_run_id,
        )
        evaluation = evaluate_segmentation_run(
            project_root,
            asset_id=asset_id,
            run_id=segmentation_run_id,
            benchmark_id=benchmark_id,
            sample_id=sample_id,
            evaluation_id=evaluation_id,
            config=evaluation_config,
        )
        gate_status = "not_evaluated"
        comparison_id = None
        if baseline_evaluation_id:
            thresholds = search_config.get("regression_thresholds")
            if not isinstance(thresholds, dict):
                raise ValueError(
                    "Search with a baseline requires regression_thresholds."
                )
            comparison_id = f"{segmentation_run_id}-cmp"
            comparison = compare_evaluations(
                project_root,
                asset_id=asset_id,
                comparison_id=comparison_id,
                baseline_evaluation_id=baseline_evaluation_id,
                candidate_evaluation_id=evaluation_id,
                thresholds=thresholds,
            )
            gate_status = comparison["gate"]["status"]
        return {
            "evaluation_id": evaluation_id,
            "summary": evaluation["summary"],
            "gate_status": gate_status,
            "comparison_id": comparison_id,
            "runtime_seconds": time.perf_counter() - started,
        }

    run_parameter_search(
        project_root,
        asset_id=asset_id,
        search_id=search_id,
        search_config=search_config,
        trial_runner=trial_runner,
    )
    print(
        project_root
        / "reports"
        / "segmentation_searches"
        / asset_id
        / search_id
        / "recommendation.json"
    )
    return 0
