import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.json_io import write_json
from pc_system.phase11_batch import build_batch_run_plan
from pc_system.phase11_project_gate import build_project_gate, write_project_gate_report


def _load_registry(paths: dict[str, Path]) -> dict:
    """读取资产 registry，缺失时抛出清晰错误。"""

    path = paths["assets"] / "asset_index.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_asset_gates(project_root: Path, registry: dict) -> dict[str, dict]:
    """读取 registry 中每个资产的 quality gate；缺失资产由项目门禁标为 missing。"""

    gates = {}
    for asset in registry.get("assets", []):
        asset_id = asset.get("asset_id", "")
        path = project_root / "reports" / "quality_gates" / asset_id / "quality_gate.json"
        if path.exists():
            gates[asset_id] = json.loads(path.read_text(encoding="utf-8"))
    return gates


def run_check_project_gate(project_root: Path) -> int:
    """从所有资产 quality gate 生成项目级门禁报告。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    try:
        registry = _load_registry(paths)
        gate = build_project_gate(registry, _load_asset_gates(project_root, registry))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write_project_gate_report(gate, paths["reports"] / "project_gate")
    return 0


def run_plan_batch_run(project_root: Path, operations: list[str] | None = None) -> int:
    """根据资产 registry 生成批处理计划，不直接执行。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    try:
        registry = _load_registry(paths)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    asset_ids = [asset.get("asset_id", "") for asset in registry.get("assets", []) if asset.get("asset_id")]
    plan = build_batch_run_plan(asset_ids, operations=operations, project_root=str(project_root))
    write_json(plan, paths["reports"] / "batch" / "batch_run_plan.json")
    return 0
