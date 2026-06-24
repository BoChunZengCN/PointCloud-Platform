import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_phase3_tool_check_writes_report():
    workspace = case_dir("cli-phase3-tool-check")
    project = workspace / "workspace"
    pdal = workspace / "pdal.exe"
    trainer = workspace / "train_3dgs.py"
    pdal.write_text("pdal", encoding="utf-8")
    trainer.write_text("trainer", encoding="utf-8")

    exit_code = main(
        [
            "phase3-tool-check",
            "--project-root",
            str(project),
            "--pdal-path",
            str(pdal),
            "--gaussian-trainer",
            str(trainer),
        ]
    )

    report_path = project / "reports" / "phase3_tool_check.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["ready"] is True
    assert (project / "reports" / "phase3_tool_check.md").exists()
