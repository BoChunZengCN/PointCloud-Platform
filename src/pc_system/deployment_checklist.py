import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


PathExists = Callable[[Path], bool]


@dataclass(frozen=True)
class DeploymentItem:
    """部署交付包中的单项检查。"""

    name: str
    required: bool
    path: str
    status: str

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的字典。"""

        return {
            "name": self.name,
            "required": self.required,
            "path": self.path,
            "status": self.status,
        }


def _expected_items(asset_id: str) -> list[tuple[str, bool, str]]:
    """列出一次交付所需的关键产物路径。"""

    return [
        ("asset_registry", True, "data/assets/asset_index.json"),
        ("production_run_plan", True, f"reports/production_runs/{asset_id}/production_run_plan.json"),
        ("production_run_report", True, f"reports/production_runs/{asset_id}/production_run_report.json"),
        ("phase3_tool_check", True, "reports/phase3_tool_check.json"),
        ("phase2_viewer_manifest", True, f"previews/{asset_id}/phase2_viewer_manifest.json"),
        ("quality_report", False, f"reports/{asset_id}/quality_report.html"),
        ("segmentation_summary", False, f"reports/{asset_id}/segments/room-a/baseline/segmentation_summary.html"),
        ("quality_gate", True, f"reports/quality_gates/{asset_id}/quality_gate.json"),
    ]


def build_deployment_checklist(project_root: Path, asset_id: str, exists: PathExists | None = None) -> dict:
    """生成 P3-M4 部署交付包检查清单。"""

    exists_func = exists or (lambda path: path.exists())
    items = []
    for name, required, relative_path in _expected_items(asset_id):
        path = project_root / relative_path
        status = "ready" if exists_func(path) else "missing"
        if name == "quality_gate" and status == "ready":
            gate = json.loads(path.read_text(encoding="utf-8"))
            status = gate.get("status", "missing")
        items.append(DeploymentItem(name=name, required=required, path=relative_path, status=status).to_dict())

    ready_count = sum(1 for item in items if item["status"] == "ready")
    missing_count = sum(1 for item in items if item["status"] == "missing")
    required_missing = [item for item in items if item["required"] and item["status"] not in {"ready", "passed"}]
    ready = not required_missing
    return {
        "phase": "Phase 3",
        "module": "P3-M4 Deployment Package Checklist",
        "asset_id": asset_id,
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "summary": {
            "total": len(items),
            "ready": ready_count,
            "missing": missing_count,
        },
        "items": items,
    }


def _requirement_label(required: bool) -> str:
    """把 required 布尔值渲染成报告标签。"""

    return "required" if required else "optional"


def _render_markdown(checklist: dict) -> str:
    """把部署检查清单渲染成 Markdown。"""

    lines = [
        "# Deployment Package Checklist",
        "",
        f"Asset: {checklist['asset_id']}",
        f"Status: {checklist['status']}",
        f"Ready: {checklist['ready']}",
        "",
        "| Item | Requirement | Status | Path |",
        "| --- | --- | --- | --- |",
    ]
    for item in checklist["items"]:
        lines.append(
            f"| {item['name']} | {_requirement_label(item['required'])} | {item['status']} | {item['path']} |"
        )
    return "\n".join(lines) + "\n"


def write_deployment_checklist(checklist: dict, output_dir: Path) -> dict[str, Path]:
    """写出部署交付检查清单 JSON 和 Markdown。"""

    json_path = write_json(checklist, output_dir / "deployment_checklist.json")
    markdown_path = output_dir / "deployment_checklist.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(checklist), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}

