import json
from pathlib import Path
from uuid import uuid4

from pc_system.phase2_status import build_phase2_status_report, write_phase2_status_report


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_phase2_status_report_marks_phase2_modules_completed():
    report = build_phase2_status_report()

    assert report["phase"] == "Phase 2"
    assert [module["id"] for module in report["modules"]] == ["P2-M1", "P2-M2", "P2-M3", "P2-M4"]
    assert all(module["status"] == "completed" for module in report["modules"])


def test_write_phase2_status_report_outputs_json_and_markdown():
    workspace = case_dir("phase2-status")
    outputs = write_phase2_status_report(build_phase2_status_report(), workspace)

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert payload["status"] == "completed"
    assert "| P2-M2 | Gaussian Splatting adapter | completed |" in markdown
