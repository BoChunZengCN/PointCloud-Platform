"""按已验证资产与版本来源统一读取 CAD 和扫描表达。"""

from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.model_library import load_model_asset
from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_sampling import load_sampled_representation
from pc_system.reference_representation import (
    load_reference_representation, load_reference_representation_with_points,
)


def model_source_family(asset: dict) -> str:
    if type(asset) is not dict:
        raise ModelMatchingError("model_representation_integrity_error", "模型资产来源类型未知。")
    if asset.get("schema_version") == "1.0":
        return "cad_mesh"
    if asset.get("schema_version") == "1.1" and asset.get("source_family") in {
        "cad_mesh", "scanned_reference",
    }:
        return asset["source_family"]
    raise ModelMatchingError("model_representation_integrity_error", "模型资产来源类型未知。")


def load_model_representation(root, model_id, version_id, representation_id) -> dict:
    asset = load_model_asset(Path(root), model_id)
    family = model_source_family(asset)
    if family == "cad_mesh":
        return load_sampled_representation(root, model_id, version_id, representation_id)
    if family == "scanned_reference":
        return load_reference_representation(root, model_id, version_id, representation_id)
    raise ModelMatchingError("model_representation_integrity_error", "模型资产来源类型未知。")


def load_representation_points(root, representation: dict) -> list[list[float]]:
    if type(representation) is not dict:
        raise ModelMatchingError("model_representation_integrity_error", "模型表达无效。")
    try:
        model_id = validate_identifier(representation.get("model_id"), "model_id")
        version_id = validate_identifier(representation.get("source_version_id"), "version_id")
        representation_id = validate_identifier(representation.get("representation_id"), "representation_id")
    except (TypeError, ValueError) as exc:
        raise ModelMatchingError("model_representation_integrity_error", "模型表达身份无效。") from exc
    asset = load_model_asset(Path(root), model_id)
    if model_source_family(asset) == "scanned_reference":
        try:
            current, points = load_reference_representation_with_points(
                root, model_id, version_id, representation_id
            )
        except ModelMatchingError as exc:
            if exc.code == "model_representation_not_found":
                raise ModelMatchingError("model_representation_integrity_error", "模型表达身份无效。") from exc
            raise
        if current != representation:
            raise ModelMatchingError("model_representation_integrity_error", "模型表达证据已改变。")
        return points
    current = load_model_representation(root, model_id, version_id, representation_id)
    if current != representation:
        raise ModelMatchingError("model_representation_integrity_error", "模型表达证据已改变。")
    from pc_system.model_sampling import _load_json, _representation_root
    points = _load_json(_representation_root(Path(root), current["model_id"], current["source_version_id"], current["representation_id"]) / "sampled_points.json")["points"]
    return [list(point) for point in points]
