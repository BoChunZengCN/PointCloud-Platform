import json
from pathlib import Path
from uuid import uuid4

from pc_system.gaussian_splatting import (
    GaussianSplatRequest,
    build_gaussian_splat_plan,
    execute_gaussian_splat_plan,
    write_gaussian_splat_plan,
)


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_gaussian_splat_plan_records_source_and_outputs():
    plan = build_gaussian_splat_plan(
        GaussianSplatRequest(
            name="baseline",
            source_las=Path("C:/data/site-a.las"),
            output_dir=Path("C:/out/splats/site-a"),
            iterations=3000,
        )
    )

    assert plan["splat_id"] == "baseline"
    assert plan["source_las"] == "C:/data/site-a.las"
    assert plan["output"]["model_file"] == "point_cloud.ply"
    assert plan["training"]["iterations"] == 3000
    assert plan["status"] == "planned"


def test_execute_gaussian_splat_plan_writes_config_invokes_runner_and_marks_completed():
    workspace = case_dir("gaussian-splat")
    output_dir = workspace / "splat"
    plan_path = write_gaussian_splat_plan(
        build_gaussian_splat_plan(
            GaussianSplatRequest(
                name="baseline",
                source_las=Path("C:/data/site-a.las"),
                output_dir=output_dir,
                iterations=100,
            )
        ),
        workspace,
    )
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        config = json.loads((workspace / "gaussian_splat_config.json").read_text(encoding="utf-8"))
        Path(config["model_path"]).write_text("ply", encoding="utf-8")
        return 0

    updated = execute_gaussian_splat_plan(plan_path, trainer_path=Path("C:/tools/train_3dgs.py"), runner=fake_runner)

    assert calls == [["python", "C:/tools/train_3dgs.py", str(workspace / "gaussian_splat_config.json")]]
    assert updated["status"] == "completed"
    assert updated["execution"]["engine"] == "gaussian-splat-trainer"
