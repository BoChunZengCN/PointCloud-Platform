import json
from pathlib import Path

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.segmentation_provenance import fingerprint_points
from pc_system.segmentation_service import run_segmentation


def sample_points():
    return [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 0.1, "y": 0.1, "z": 0.1},
    ]


def write_benchmark_source(project: Path) -> Path:
    source = project / "samples" / "scan.points.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(sample_points()), encoding="utf-8")
    labels = {
        "schema_version": "1.0",
        "point_labels": [
            {
                "point_index": index,
                **point,
                "instance_id": "gold-a",
                "class_id": "pipe",
                "is_noise": False,
            }
            for index, point in enumerate(sample_points())
        ],
        "boxes": [
            {
                "instance_id": "gold-a",
                "class_id": "pipe",
                "center": [0.05, 0.05, 0.05],
                "size": [0.1, 0.1, 0.1],
                "rotation": [0.0, 0.0, 0.0, 1.0],
            }
        ],
    }
    benchmark_source = project / "benchmark-source"
    benchmark_source.mkdir()
    (benchmark_source / "labels.json").write_text(
        json.dumps(labels), encoding="utf-8"
    )
    manifest = {
        "schema_version": "1.0",
        "benchmark_id": "bench-001",
        "benchmark_version": "v1",
        "split": "golden_regression",
        "scene_type": "pipe-rack",
        "point_density": 100.0,
        "coordinate_unit": "m",
        "label_version": "labels-v1",
        "license": "internal",
        "samples": [
            {
                "sample_id": "sample-001",
                "asset_id": "scan",
                "asset_version": "v1",
                "source_uri": str(source),
                "source_fingerprint": fingerprint_points(sample_points()),
                "labels_path": "labels.json",
                "labels_format": "json",
            }
        ],
    }
    manifest_path = benchmark_source / "benchmark.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def import_benchmark_cli(project: Path) -> None:
    manifest_path = write_benchmark_source(project)
    assert (
        main(
            [
                "import-segmentation-benchmark",
                "--project-root",
                str(project),
                "--manifest",
                str(manifest_path),
            ]
        )
        == 0
    )


def create_segmentation_run(project: Path, run_id: str = "seg-run-001") -> None:
    source = project / "samples" / "scan.points.json"
    run_segmentation(
        project,
        asset_id="scan",
        asset_version="v1",
        source_uri=str(source),
        points=sample_points(),
        config={
            "engine": "builtin_geometric",
            "distance_threshold": 0.3,
            "min_points": 1,
            "max_points": 1000,
        },
        run_id=run_id,
    )


def test_import_benchmark_cli_writes_normalized_manifest(tmp_path):
    import_benchmark_cli(tmp_path)

    assert (
        tmp_path / "benchmarks" / "bench-001" / "benchmark.json"
    ).is_file()


def test_evaluate_run_cli_writes_golden_summary(tmp_path):
    import_benchmark_cli(tmp_path)
    create_segmentation_run(tmp_path)
    config = tmp_path / "evaluation-config.json"
    config.write_text(
        json.dumps(
            {"instance_iou_threshold": 0.5, "box_iou_threshold": 0.5}
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "evaluate-segmentation-run",
            "--project-root",
            str(tmp_path),
            "--asset-id",
            "scan",
            "--run-id",
            "seg-run-001",
            "--benchmark-id",
            "bench-001",
            "--sample-id",
            "sample-001",
            "--evaluation-id",
            "eval-001",
            "--config",
            str(config),
        ]
    )

    summary_path = (
        tmp_path
        / "reports"
        / "segmentation_evaluations"
        / "scan"
        / "eval-001"
        / "evaluation_summary.json"
    )
    assert exit_code == 0
    assert json.loads(summary_path.read_text(encoding="utf-8"))[
        "evaluation_kind"
    ] == "golden_labels"


def write_completed_evaluation(project: Path, evaluation_id: str, score: float) -> None:
    evaluation_dir = (
        project
        / "reports"
        / "segmentation_evaluations"
        / "scan"
        / evaluation_id
    )
    evaluation_dir.mkdir(parents=True)
    summary = {
        "evaluation_kind": "golden_labels",
        "evaluation_id": evaluation_id,
        "point_miou": score,
        "instance_f1": score,
        "mean_box_iou": score,
        "noise_f1": score,
        "over_segmentation_count": 0,
        "under_segmentation_count": 0,
    }
    (evaluation_dir / "evaluation_run.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "evaluation_id": evaluation_id,
                "asset_id": "scan",
                "status": "completed",
                "summary": summary,
            }
        ),
        encoding="utf-8",
    )


def regression_thresholds():
    return {
        "point_miou": {"allowed_drop": 0.01},
        "instance_f1": {"allowed_drop": 0.01},
        "mean_box_iou": {"allowed_drop": 0.01},
        "noise_f1": {"allowed_drop": 0.01},
        "over_segmentation_count": {"allowed_increase": 0},
        "under_segmentation_count": {"allowed_increase": 0},
    }


def test_compare_cli_writes_regression_gate(tmp_path):
    write_completed_evaluation(tmp_path, "baseline", 0.8)
    write_completed_evaluation(tmp_path, "candidate", 0.9)
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(
        json.dumps(regression_thresholds()), encoding="utf-8"
    )

    exit_code = main(
        [
            "compare-segmentation-runs",
            "--project-root",
            str(tmp_path),
            "--asset-id",
            "scan",
            "--comparison-id",
            "cmp-001",
            "--baseline-evaluation-id",
            "baseline",
            "--candidate-evaluation-id",
            "candidate",
            "--thresholds",
            str(thresholds_path),
        ]
    )

    gate_path = (
        tmp_path
        / "reports"
        / "segmentation_comparisons"
        / "scan"
        / "cmp-001"
        / "regression_gate.json"
    )
    assert exit_code == 0
    assert json.loads(gate_path.read_text(encoding="utf-8"))["status"] == "passed"


def test_search_cli_uses_bounded_config_file(tmp_path):
    import_benchmark_cli(tmp_path)
    search_config = {
        "strategy": "grid",
        "parameter_space": {
            "distance_threshold": [0.3],
            "min_points": [1],
        },
        "base_config": {"engine": "builtin_geometric", "max_points": 1000},
        "evaluation_config": {
            "instance_iou_threshold": 0.5,
            "box_iou_threshold": 0.5,
        },
        "max_trials": 1,
        "seed": 3,
        "trial_timeout_seconds": 30,
        "weights": {
            "instance_f1": 0.4,
            "point_miou": 0.25,
            "mean_box_iou": 0.2,
            "noise_f1": 0.15,
            "over_segmentation": 0.05,
            "under_segmentation": 0.05,
            "runtime_seconds": 0.001,
        },
    }
    config_path = tmp_path / "search-config.json"
    config_path.write_text(json.dumps(search_config), encoding="utf-8")

    exit_code = main(
        [
            "search-segmentation-params",
            "--project-root",
            str(tmp_path),
            "--asset-id",
            "scan",
            "--benchmark-id",
            "bench-001",
            "--sample-id",
            "sample-001",
            "--search-id",
            "search-001",
            "--config",
            str(config_path),
        ]
    )

    recommendation_path = (
        tmp_path
        / "reports"
        / "segmentation_searches"
        / "scan"
        / "search-001"
        / "recommendation.json"
    )
    assert exit_code == 0
    recommendation = json.loads(recommendation_path.read_text(encoding="utf-8"))
    assert recommendation["status"] == "recommended"
    assert recommendation["config"]["min_points"] == 1
    assert recommendation["gate_status"] == "not_evaluated"
    assert recommendation["comparison_id"] is None


def test_api_lists_and_reads_phase13b_artifacts(tmp_path):
    import_benchmark_cli(tmp_path)
    create_segmentation_run(tmp_path)
    config = tmp_path / "evaluation-config.json"
    config.write_text("{}", encoding="utf-8")
    assert (
        main(
            [
                "evaluate-segmentation-run",
                "--project-root",
                str(tmp_path),
                "--asset-id",
                "scan",
                "--run-id",
                "seg-run-001",
                "--benchmark-id",
                "bench-001",
                "--sample-id",
                "sample-001",
                "--evaluation-id",
                "eval-001",
                "--config",
                str(config),
            ]
        )
        == 0
    )
    client = TestClient(create_app(tmp_path))

    benchmarks = client.get("/segmentation-benchmarks")
    benchmark = client.get("/segmentation-benchmarks/bench-001")
    evaluations = client.get("/segmentation-evaluations/scan")
    evaluation = client.get("/segmentation-evaluations/scan/eval-001")

    assert benchmarks.status_code == 200
    assert benchmarks.json()["benchmark_count"] == 1
    assert benchmark.json()["benchmark_id"] == "bench-001"
    assert evaluations.json()["evaluation_count"] == 1
    assert evaluation.json()["evaluation_kind"] == "golden_labels"


def test_api_validates_all_phase13b_identifiers(tmp_path):
    client = TestClient(create_app(tmp_path))

    assert client.get("/segmentation-benchmarks/bad$id").status_code == 400
    assert client.get("/segmentation-evaluations/bad$id").status_code == 400
    assert (
        client.get("/segmentation-comparisons/scan/bad$id").status_code == 400
    )
    assert client.get("/segmentation-searches/scan/bad$id").status_code == 400


def test_api_exposes_sorted_parameter_search_trials(tmp_path):
    search_root = (
        tmp_path
        / "reports"
        / "segmentation_searches"
        / "scan"
        / "search-001"
    )
    trials_root = search_root / "trials"
    trials_root.mkdir(parents=True)
    (search_root / "search_run.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "asset_id": "scan",
                "search_id": "search-001",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    for trial_id in ("trial-0002", "trial-0001"):
        (trials_root / f"{trial_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "trial_id": trial_id,
                    "status": "completed",
                }
            ),
            encoding="utf-8",
        )

    response = TestClient(create_app(tmp_path)).get(
        "/segmentation-searches/scan/search-001/trials"
    )

    assert response.status_code == 200
    assert response.json()["trial_count"] == 2
    assert [
        trial["trial_id"] for trial in response.json()["trials"]
    ] == ["trial-0001", "trial-0002"]
