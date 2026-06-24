import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


class RuleSegmentationError(RuntimeError):
    """规则分割执行结果不符合预期时抛出的明确错误。"""


@dataclass(frozen=True)
class RuleSegmentationRequest:
    """一次规则分割请求。

    M6 第一版先固化规则分割的 manifest 和执行接口，不直接实现复杂点云算法。
    后续 ground/plane/cluster/noise 可分别接入 Open3D、PDAL 或自研算法。
    """

    name: str
    methods: list[str]


@dataclass(frozen=True)
class RuleSegmentationResult:
    """规则分割执行器返回的统一结果。"""

    labels_path: Path
    label_count: int
    engine: str


RuleSegmentationAdapter = Callable[[dict, Path], RuleSegmentationResult]


RULE_METHODS = {
    "ground": "height_threshold",
    "plane": "ransac_plane",
    "cluster": "euclidean_cluster",
    "noise": "statistical_outlier",
}


def _rules_for_methods(methods: list[str]) -> list[dict[str, str]]:
    """把用户选择的规则名转换成 manifest 中的规则配置。"""

    return [{"label": method, "method": RULE_METHODS[method]} for method in methods]


def build_rule_segmentation_plan(slice_plan: dict, request: RuleSegmentationRequest) -> dict:
    """根据切片计划生成规则分割计划。"""

    slice_id = slice_plan["slice_id"]
    segmentation_id = f"{slice_id}-{request.name}"
    return {
        "asset_id": slice_plan["asset_id"],
        "slice_id": slice_id,
        "segmentation_id": segmentation_id,
        "source_slice": slice_plan["execution"]["output_path"],
        "rules": _rules_for_methods(request.methods),
        "output": {"file_name": f"{segmentation_id}-labels.json"},
        "status": "planned",
    }


def write_rule_segmentation_plan(plan: dict, output_dir: Path) -> Path:
    """写出 M6 规则分割计划 manifest。"""

    return write_json(plan, output_dir / "rule_segmentation_plan.json")


def placeholder_rule_segmentation_adapter(plan: dict, destination: Path) -> RuleSegmentationResult:
    """占位规则分割适配器。

    当前只写空 labels 文件，用来跑通状态机和目录结构。
    """

    labels = {
        "segmentation_id": plan["segmentation_id"],
        "source_slice": plan["source_slice"],
        "rules": plan["rules"],
        "labels": [],
    }
    write_json(labels, destination)
    return RuleSegmentationResult(
        labels_path=destination,
        label_count=0,
        engine="placeholder-rule-segmentation",
    )


def execute_rule_segmentation_plan(
    plan_path: Path,
    adapter: RuleSegmentationAdapter = placeholder_rule_segmentation_adapter,
) -> dict:
    """执行 rule_segmentation_plan.json，并把执行结果写回计划文件。"""

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    destination = plan_path.parent / plan["output"]["file_name"]
    result = adapter(plan, destination)
    if not result.labels_path.exists():
        raise RuleSegmentationError(f"Rule segmentation labels file was not created: {result.labels_path}")
    plan["status"] = "completed"
    plan["execution"] = {
        "engine": result.engine,
        "label_count": result.label_count,
        "labels_path": str(result.labels_path),
    }
    write_json(plan, plan_path)
    return plan
