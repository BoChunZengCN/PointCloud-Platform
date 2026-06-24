from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


@dataclass(frozen=True)
class ToolSpec:
    """Phase 3 外部工具检查项。"""

    name: str
    path: Path | None
    required: bool


PathExists = Callable[[Path], bool]


def build_tool_check_report(specs: list[ToolSpec], exists: PathExists | None = None) -> dict:
    """生成 Phase 3 外部工具可用性报告。

    生产阶段会依赖 FLS 转换器、PDAL、PotreeConverter、3DGS trainer 等外部程序；
    这里先把路径检查和报告格式固定下来，避免运行到中途才发现环境缺口。
    """

    exists_func = exists or (lambda path: path.exists())
    tools = []
    for spec in specs:
        if spec.path is None:
            status = "not_configured"
            path_value = None
        elif exists_func(spec.path):
            status = "available"
            path_value = spec.path.as_posix()
        else:
            status = "missing"
            path_value = spec.path.as_posix()
        tools.append(
            {
                "name": spec.name,
                "path": path_value,
                "required": spec.required,
                "status": status,
            }
        )
    ready = all(tool["status"] == "available" for tool in tools if tool["required"])
    return {
        "phase": "Phase 3",
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "tools": tools,
    }


def _required_label(required: bool) -> str:
    """把 required 布尔值渲染为报告中的短标签。"""

    return "required" if required else "optional"


def _render_markdown(report: dict) -> str:
    """把工具检查报告渲染为 Markdown。"""

    lines = [
        "# Phase 3 Tool Check",
        "",
        f"Status: {report['status']}",
        f"Ready: {report['ready']}",
        "",
        "| Tool | Requirement | Status | Path |",
        "| --- | --- | --- | --- |",
    ]
    for tool in report["tools"]:
        path = tool["path"] or ""
        lines.append(
            f"| {tool['name']} | {_required_label(tool['required'])} | {tool['status']} | {path} |"
        )
    return "\n".join(lines) + "\n"


def write_tool_check_report(report: dict, output_dir: Path) -> dict[str, Path]:
    """写出 Phase 3 工具检查 JSON 和 Markdown 报告。"""

    json_path = write_json(report, output_dir / "phase3_tool_check.json")
    markdown_path = output_dir / "phase3_tool_check.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}
