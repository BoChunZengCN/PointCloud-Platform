import json
import subprocess
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json
from pc_system.rule_segmentation import RuleSegmentationResult


class Open3DSegmentationFailed(RuntimeError):
    """Open3D 分割脚本执行失败时抛出的明确错误。"""


Open3DRunner = Callable[[list[str]], int]


def subprocess_runner(command: list[str]) -> int:
    """默认 Open3D 脚本执行器。

    测试通过注入 fake runner，生产环境则用本机 Python 执行外部分割脚本。
    """

    return subprocess.run(command, check=False).returncode


def build_open3d_segmentation_config(plan: dict, destination: Path) -> dict:
    """根据规则分割计划生成 Open3D 脚本配置。

    该配置是系统与真实算法脚本之间的稳定边界：系统负责传入输入、规则和输出路径，
    算法脚本负责读取点云并写出 labels JSON。
    """

    return {
        "segmentation_id": plan["segmentation_id"],
        "source_slice": plan["source_slice"],
        "rules": plan["rules"],
        "labels_path": destination.as_posix(),
    }


def _count_labels(labels_path: Path) -> int:
    """读取 labels JSON 并统计标签条目数量。"""

    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    return len(payload.get("labels", []))


def open3d_rule_segmentation_adapter(
    plan: dict,
    destination: Path,
    python_path: Path = Path("python"),
    script_path: Path = Path("open3d_rule_segment.py"),
    runner: Open3DRunner = subprocess_runner,
) -> RuleSegmentationResult:
    """执行外部 Open3D 分割脚本，并返回统一分割结果。

    这里不直接 import open3d，避免把大型三维算法依赖变成系统启动依赖。
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    config_path = destination.parent / "open3d_rule_segmentation_config.json"
    write_json(build_open3d_segmentation_config(plan, destination), config_path)
    exit_code = runner([python_path.as_posix(), script_path.as_posix(), str(config_path)])
    if exit_code != 0:
        raise Open3DSegmentationFailed(f"Open3D rule segmentation failed with exit code {exit_code}")
    if not destination.exists():
        raise Open3DSegmentationFailed(f"Open3D labels file was not created: {destination}")
    return RuleSegmentationResult(
        labels_path=destination,
        label_count=_count_labels(destination),
        engine="open3d-rule-segmentation",
    )
