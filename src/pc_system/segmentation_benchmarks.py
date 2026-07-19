import json
import math
import re
from pathlib import Path

from pc_system.identifiers import validate_identifier
from pc_system.json_io import write_json


BENCHMARK_SPLITS = {"development", "validation", "golden_regression"}


class BenchmarkValidationError(ValueError):
    """带稳定错误码的黄金 benchmark 校验错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _validation_error(code: str, message: str) -> BenchmarkValidationError:
    return BenchmarkValidationError(code, message)


def _required_string(document: dict, field: str, code: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _validation_error(code, f"{field} must be a non-empty string.")
    return value.strip()


def _finite_vector(value, length: int, code: str, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise _validation_error(code, f"{label} must contain {length} numbers.")
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float))
        for item in value
    ):
        raise _validation_error(code, f"{label} must contain finite numbers.")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise _validation_error(code, f"{label} must contain finite numbers.")
    return result


def load_label_document(path: Path, labels_format: str) -> dict:
    """读取 JSON 或 JSONL 标签并归一化为单一文档。"""

    try:
        if labels_format == "json":
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise _validation_error("invalid_labels_document", "JSON labels must be an object.")
            return {
                "schema_version": document.get("schema_version", "1.0"),
                "point_labels": list(document.get("point_labels", [])),
                "boxes": list(document.get("boxes", [])),
            }
        if labels_format == "jsonl":
            point_labels = []
            boxes = []
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                record = json.loads(line)
                record_type = record.pop("record_type", None)
                if record_type == "point_label":
                    point_labels.append(record)
                elif record_type == "box":
                    boxes.append(record)
                else:
                    raise _validation_error(
                        "invalid_record_type",
                        f"Unsupported record_type on line {line_number}: {record_type}",
                    )
            return {
                "schema_version": "1.0",
                "point_labels": point_labels,
                "boxes": boxes,
            }
    except json.JSONDecodeError as exc:
        raise _validation_error("invalid_labels_json", str(exc)) from exc
    raise _validation_error(
        "unsupported_labels_format", f"Unsupported labels format: {labels_format}"
    )


def _validate_point_labels(point_labels: list[dict]) -> list[dict]:
    seen_indices: set[int] = set()
    normalized = []
    for item in point_labels:
        if not isinstance(item, dict):
            raise _validation_error("invalid_point_label", "Point labels must be objects.")
        point_index = item.get("point_index")
        if isinstance(point_index, bool) or not isinstance(point_index, int):
            raise _validation_error(
                "invalid_point_index", "point_index must be a non-negative integer."
            )
        if point_index < 0:
            raise _validation_error(
                "invalid_point_index", "point_index must be a non-negative integer."
            )
        if point_index in seen_indices:
            raise _validation_error(
                "duplicate_point_index", f"Duplicate point_index: {point_index}"
            )
        seen_indices.add(point_index)
        if not isinstance(item.get("instance_id"), str) or not isinstance(
            item.get("class_id"), str
        ):
            raise _validation_error(
                "invalid_point_label",
                "instance_id and class_id must be strings.",
            )
        try:
            instance_id = validate_identifier(item["instance_id"], "instance_id")
            class_id = validate_identifier(item["class_id"], "class_id")
        except ValueError as exc:
            raise _validation_error("invalid_point_label", str(exc)) from exc
        if not isinstance(item.get("is_noise"), bool):
            raise _validation_error(
                "invalid_is_noise", "is_noise must be an explicit boolean."
            )
        normalized_item = dict(item)
        normalized_item["point_index"] = point_index
        normalized_item["instance_id"] = instance_id
        normalized_item["class_id"] = class_id
        normalized_item["is_noise"] = item["is_noise"]
        present_xyz = [axis in item for axis in ("x", "y", "z")]
        if any(present_xyz) and not all(present_xyz):
            raise _validation_error(
                "invalid_point_coordinates", "Point label coordinates require x, y, and z."
            )
        if all(present_xyz):
            xyz = _finite_vector(
                [item["x"], item["y"], item["z"]],
                3,
                "invalid_point_coordinates",
                "Point coordinates",
            )
            normalized_item.update(dict(zip(("x", "y", "z"), xyz)))
        normalized.append(normalized_item)
    return normalized


def _validate_boxes(boxes: list[dict]) -> list[dict]:
    seen_instances: set[str] = set()
    normalized = []
    for item in boxes:
        if not isinstance(item, dict):
            raise _validation_error("invalid_box", "Boxes must be objects.")
        if not isinstance(item.get("instance_id"), str) or not isinstance(
            item.get("class_id"), str
        ):
            raise _validation_error(
                "invalid_box", "instance_id and class_id must be strings."
            )
        try:
            instance_id = validate_identifier(item["instance_id"], "instance_id")
            class_id = validate_identifier(item["class_id"], "class_id")
        except ValueError as exc:
            raise _validation_error("invalid_box", str(exc)) from exc
        if instance_id in seen_instances:
            raise _validation_error(
                "duplicate_instance_box", f"Duplicate box instance_id: {instance_id}"
            )
        seen_instances.add(instance_id)
        center = _finite_vector(
            item.get("center"), 3, "invalid_box_center", "Box center"
        )
        size = _finite_vector(item.get("size"), 3, "invalid_box_size", "Box size")
        if not all(value > 0 for value in size):
            raise _validation_error(
                "invalid_box_size", "Box size values must be greater than zero."
            )
        rotation = _finite_vector(
            item.get("rotation"), 4, "invalid_box_rotation", "Box rotation"
        )
        if math.sqrt(sum(value * value for value in rotation)) == 0:
            raise _validation_error(
                "invalid_box_rotation", "Box rotation quaternion must be non-zero."
            )
        normalized_item = dict(item)
        normalized_item.update(
            {
                "instance_id": instance_id,
                "class_id": class_id,
                "center": center,
                "size": size,
                "rotation": rotation,
            }
        )
        normalized.append(normalized_item)
    return normalized


def _normalize_labels(document: dict) -> dict:
    if document.get("schema_version") != "1.0":
        raise _validation_error(
            "unsupported_labels_schema",
            f"Unsupported labels schema: {document.get('schema_version')}",
        )
    point_labels = _validate_point_labels(list(document.get("point_labels", [])))
    boxes = _validate_boxes(list(document.get("boxes", [])))
    if boxes:
        box_instances = {item["instance_id"] for item in boxes}
        missing = sorted(
            {
                item["instance_id"]
                for item in point_labels
                if not item["is_noise"] and item["instance_id"] not in box_instances
            }
        )
        if missing:
            raise _validation_error(
                "missing_instance_box",
                f"Point labels reference missing instance boxes: {', '.join(missing)}",
            )
    return {
        "schema_version": "1.0",
        "point_labels": point_labels,
        "boxes": boxes,
    }


def _safe_labels_path(manifest_dir: Path, relative_path: str) -> Path:
    candidate = (manifest_dir / relative_path).resolve()
    root = manifest_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _validation_error(
            "unsafe_labels_path", f"Labels path escapes benchmark directory: {relative_path}"
        ) from exc
    if not candidate.is_file():
        raise _validation_error("labels_not_found", f"Labels file not found: {candidate}")
    return candidate


def import_benchmark(project_root: Path, manifest_path: Path) -> dict:
    """校验并导入一个不可覆盖的版本化黄金 benchmark。"""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _validation_error("invalid_manifest_json", str(exc)) from exc
    if not isinstance(manifest, dict):
        raise _validation_error(
            "invalid_manifest_document", "Benchmark manifest must be an object."
        )
    if manifest.get("schema_version") != "1.0":
        raise _validation_error(
            "unsupported_benchmark_schema",
            f"Unsupported benchmark schema: {manifest.get('schema_version')}",
        )
    if not isinstance(manifest.get("benchmark_id"), str):
        raise _validation_error(
            "invalid_benchmark_id", "benchmark_id must be a string."
        )
    try:
        benchmark_id = validate_identifier(
            manifest["benchmark_id"], "benchmark_id"
        )
    except ValueError as exc:
        raise _validation_error("invalid_benchmark_id", str(exc)) from exc
    benchmark_version = _required_string(
        manifest, "benchmark_version", "invalid_benchmark_version"
    )
    scene_type = _required_string(manifest, "scene_type", "invalid_scene_type")
    coordinate_unit = _required_string(
        manifest, "coordinate_unit", "invalid_coordinate_unit"
    )
    label_version = _required_string(
        manifest, "label_version", "invalid_label_version"
    )
    benchmark_license = _required_string(
        manifest, "license", "invalid_benchmark_license"
    )
    point_density = manifest.get("point_density")
    if (
        isinstance(point_density, bool)
        or not isinstance(point_density, (int, float))
        or not math.isfinite(float(point_density))
        or float(point_density) <= 0
    ):
        raise _validation_error(
            "invalid_point_density",
            "point_density must be a positive finite number.",
        )
    point_density = float(point_density)
    if manifest.get("split") not in BENCHMARK_SPLITS:
        raise _validation_error(
            "invalid_benchmark_split", f"Unsupported split: {manifest.get('split')}"
        )
    destination = project_root / "benchmarks" / benchmark_id
    if destination.exists():
        raise _validation_error(
            "benchmark_exists", f"Benchmark already exists: {benchmark_id}"
        )
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise _validation_error(
            "missing_benchmark_samples", "Benchmark requires at least one sample."
        )
    normalized_samples = []
    normalized_labels: list[tuple[str, dict]] = []
    seen_samples: set[str] = set()
    for sample in samples:
        if not isinstance(sample, dict):
            raise _validation_error(
                "invalid_benchmark_sample", "Benchmark samples must be objects."
            )
        if not isinstance(sample.get("sample_id"), str) or not isinstance(
            sample.get("asset_id"), str
        ):
            raise _validation_error(
                "invalid_benchmark_sample",
                "sample_id and asset_id must be strings.",
            )
        try:
            sample_id = validate_identifier(sample["sample_id"], "sample_id")
            asset_id = validate_identifier(sample["asset_id"], "asset_id")
        except ValueError as exc:
            raise _validation_error("invalid_benchmark_sample", str(exc)) from exc
        if sample_id in seen_samples:
            raise _validation_error(
                "duplicate_sample_id", f"Duplicate sample_id: {sample_id}"
            )
        seen_samples.add(sample_id)
        asset_version = _required_string(
            sample, "asset_version", "invalid_asset_version"
        )
        source_uri = _required_string(sample, "source_uri", "invalid_source_uri")
        source_fingerprint = _required_string(
            sample, "source_fingerprint", "invalid_source_fingerprint"
        )
        if re.fullmatch(r"[0-9a-fA-F]{64}", source_fingerprint) is None:
            raise _validation_error(
                "invalid_source_fingerprint",
                "source_fingerprint must be a 64-character SHA-256 hex digest.",
            )
        labels_format = _required_string(
            sample, "labels_format", "invalid_labels_format"
        )
        relative_labels_path = _required_string(
            sample, "labels_path", "invalid_labels_path"
        )
        labels_path = _safe_labels_path(
            manifest_path.parent, relative_labels_path
        )
        labels = _normalize_labels(load_label_document(labels_path, labels_format))
        normalized_sample = dict(sample)
        normalized_sample.update(
            {
                "sample_id": sample_id,
                "asset_id": asset_id,
                "asset_version": asset_version,
                "source_uri": source_uri,
                "source_fingerprint": source_fingerprint.lower(),
                "labels_path": f"samples/{sample_id}/labels.json",
                "labels_format": "json",
                "imported_labels_format": labels_format,
            }
        )
        normalized_samples.append(normalized_sample)
        normalized_labels.append((sample_id, labels))
    normalized_manifest = dict(manifest)
    normalized_manifest.update(
        {
            "benchmark_id": benchmark_id,
            "benchmark_version": benchmark_version,
            "scene_type": scene_type,
            "point_density": point_density,
            "coordinate_unit": coordinate_unit,
            "label_version": label_version,
            "license": benchmark_license,
        }
    )
    normalized_manifest["samples"] = normalized_samples
    write_json(normalized_manifest, destination / "benchmark.json")
    for sample_id, labels in normalized_labels:
        write_json(labels, destination / "samples" / sample_id / "labels.json")
    return normalized_manifest


def load_benchmark_sample(
    project_root: Path, benchmark_id: str, sample_id: str
) -> tuple[dict, dict]:
    """读取已导入 benchmark 的清单与单一样本标签。"""

    benchmark_id = validate_identifier(benchmark_id, "benchmark_id")
    sample_id = validate_identifier(sample_id, "sample_id")
    root = project_root / "benchmarks" / benchmark_id
    manifest_path = root / "benchmark.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample = next(
        (item for item in manifest.get("samples", []) if item.get("sample_id") == sample_id),
        None,
    )
    if sample is None:
        raise KeyError(f"Benchmark sample not found: {sample_id}")
    labels_path = root / sample["labels_path"]
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    return manifest, labels
