import json
from pathlib import Path
from uuid import uuid4

from pc_system.module_status import build_module_status_report, write_module_status_report


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_module_status_report_lists_completed_phase1_modules():
    report = build_module_status_report()

    ids = [module["id"] for module in report["modules"]]
    assert ids == ["M1", "M2", "M3", "M4", "M5", "M6"]
    assert all(module["status"] == "completed" for module in report["modules"])
    assert "open3d" in report["external_dependencies"]
    assert report["next_steps"] == []


def test_write_module_status_report_outputs_json_and_markdown():
    workspace = case_dir("module-status")
    report = build_module_status_report()

    outputs = write_module_status_report(report, workspace)

    saved = json.loads(outputs["json"].read_text(encoding="utf-8"))
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert saved["phase"] == "Phase 1"
    assert "| M6 | Rule segmentation / summary report | completed |" in markdown
    assert outputs["json"].name == "module_status.json"
    assert outputs["markdown"].name == "module_status.md"
