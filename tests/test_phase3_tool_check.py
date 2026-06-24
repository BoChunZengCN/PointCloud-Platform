import json
from pathlib import Path
from uuid import uuid4

from pc_system.phase3_tool_check import ToolSpec, build_tool_check_report, write_tool_check_report


def case_dir(name: str) -> Path:
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_tool_check_report_marks_available_and_missing_tools():
    existing = Path("C:/tools/existing.exe")
    missing = Path("C:/tools/missing.exe")

    report = build_tool_check_report(
        [
            ToolSpec(name="pdal", path=existing, required=True),
            ToolSpec(name="3dgs_trainer", path=missing, required=True),
            ToolSpec(name="open3d_script", path=None, required=False),
        ],
        exists=lambda path: path == existing,
    )

    assert report["phase"] == "Phase 3"
    assert report["ready"] is False
    assert report["tools"][0]["status"] == "available"
    assert report["tools"][1]["status"] == "missing"
    assert report["tools"][2]["status"] == "not_configured"


def test_write_tool_check_report_outputs_json_and_markdown():
    workspace = case_dir("phase3-tool-check")
    report = build_tool_check_report(
        [ToolSpec(name="pdal", path=Path("C:/tools/pdal.exe"), required=True)],
        exists=lambda path: True,
    )

    outputs = write_tool_check_report(report, workspace)

    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert payload["ready"] is True
    assert "| pdal | required | available |" in markdown
    assert outputs["json"].name == "phase3_tool_check.json"
    assert outputs["markdown"].name == "phase3_tool_check.md"
