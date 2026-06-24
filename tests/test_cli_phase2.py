import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_plan_and_execute_fls_ingest():
    workspace = case_dir("cli-fls-ingest")
    project = workspace / "workspace"
    raw = workspace / "scan.fls"
    raw.write_text("fls", encoding="utf-8")
    output_las = workspace / "site-a.las"

    plan_exit = main([
        "plan-fls-ingest",
        "--project-root",
        str(project),
        "--asset-id",
        "site-a",
        "--raw-files",
        str(raw),
        "--output-las",
        str(output_las),
    ])
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        output_las.write_text("las", encoding="utf-8")
        return 0

    execute_exit = main([
        "execute-fls-ingest",
        "--project-root",
        str(project),
        "--asset-id",
        "site-a",
        "--converter-path",
        "C:/tools/fls2las.exe",
    ], fls_runner=fake_runner)

    plan_path = project / "data" / "raw" / "fls" / "site-a" / "fls_ingest_plan.json"
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_exit == 0
    assert execute_exit == 0
    assert saved["status"] == "completed"
    assert calls[0][0] == "C:/tools/fls2las.exe"


def test_cli_plan_and_execute_gaussian_splat():
    workspace = case_dir("cli-gaussian-splat")
    project = workspace / "workspace"
    source_las = workspace / "site-a.las"
    source_las.write_text("las", encoding="utf-8")

    plan_exit = main([
        "plan-gaussian-splat",
        "--project-root",
        str(project),
        "--asset-id",
        "site-a",
        "--name",
        "baseline",
        "--source-las",
        str(source_las),
        "--iterations",
        "50",
    ])
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        config = json.loads((project / "previews" / "site-a" / "splats" / "baseline" / "gaussian_splat_config.json").read_text(encoding="utf-8"))
        Path(config["model_path"]).write_text("ply", encoding="utf-8")
        return 0

    execute_exit = main([
        "execute-gaussian-splat",
        "--project-root",
        str(project),
        "--asset-id",
        "site-a",
        "--name",
        "baseline",
        "--trainer-path",
        "C:/tools/train_3dgs.py",
    ], gaussian_runner=fake_runner)

    plan_path = project / "previews" / "site-a" / "splats" / "baseline" / "gaussian_splat_plan.json"
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_exit == 0
    assert execute_exit == 0
    assert saved["status"] == "completed"
    assert calls[0][1] == "C:/tools/train_3dgs.py"


def test_cli_publish_phase2_viewer_and_status():
    workspace = case_dir("cli-phase2-viewer")
    project = workspace / "workspace"

    viewer_exit = main([
        "publish-phase2-viewer",
        "--project-root",
        str(project),
        "--asset-id",
        "site-a",
        "--potree-path",
        "previews/site-a/potree/metadata.json",
        "--splat-path",
        "previews/site-a/splats/baseline/point_cloud.ply",
        "--report",
        "reports/site-a/quality_report.html",
    ])
    status_exit = main(["phase2-status", "--project-root", str(project)])

    assert viewer_exit == 0
    assert status_exit == 0
    assert (project / "previews" / "site-a" / "phase2_viewer_manifest.json").exists()
    assert (project / "reports" / "phase2_status.json").exists()
