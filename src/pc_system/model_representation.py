"""按已验证资产与版本来源统一读取 CAD 和扫描表达。"""

from pathlib import Path

from pc_system.model_library import load_model_asset
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_sampling import load_sampled_representation
from pc_system.reference_representation import (
    load_reference_representation, load_reference_representation_points,
)


def load_model_representation(root, model_id, version_id, representation_id) -> dict:
    family = load_model_asset(Path(root), model_id).get("source_family", "cad_mesh")
    if family == "cad_mesh":
        return load_sampled_representation(root, model_id, version_id, representation_id)
    if family == "scanned_reference":
        return load_reference_representation(root, model_id, version_id, representation_id)
    raise ModelMatchingError("model_representation_integrity_error", "模型资产来源类型未知。")


def load_representation_points(root, representation: dict) -> list[list[float]]:
    if type(representation) is not dict:
        raise ModelMatchingError("model_representation_integrity_error", "模型表达无效。")
    current = load_model_representation(root, representation.get("model_id"), representation.get("source_version_id"), representation.get("representation_id"))
    if current != representation:
        raise ModelMatchingError("model_representation_integrity_error", "模型表达证据已改变。")
    if current["representation_type"] == "scanned_reference":
        return load_reference_representation_points(root, current)
    from pc_system.model_sampling import _load_json, _representation_root
    points = _load_json(_representation_root(Path(root), current["model_id"], current["source_version_id"], current["representation_id"]) / "sampled_points.json")["points"]
    return [list(point) for point in points]
