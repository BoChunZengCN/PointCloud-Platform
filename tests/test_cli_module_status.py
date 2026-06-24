import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_module_status_writes_reports():
    workspace = case_dir("cli-module-status")
    project = workspace / "workspace"

    exit_code = main(["module-status", "--project-root", str(project)])

    json_path = project / "reports" / "module_status.json"
    markdown_path = project / "reports" / "module_status.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert markdown_path.exists()
