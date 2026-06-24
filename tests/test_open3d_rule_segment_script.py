import importlib.util
import json
from pathlib import Path
from uuid import uuid4


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "open3d_rule_segment.py"
    spec = importlib.util.spec_from_file_location("open3d_rule_segment", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_open3d_rule_segment_labels_noise_from_json_points():
    module = load_script_module()
    points = [[0, 0, 0], [0, 0, 0.1], [100, 100, 100]]

    labels = module.build_labels(points, [{"label": "noise", "method": "statistical_outlier"}])

    assert labels == [
        {"point_index": 0, "label": "unclassified"},
        {"point_index": 1, "label": "unclassified"},
        {"point_index": 2, "label": "noise"},
    ]


def test_open3d_rule_segment_main_reads_config_and_writes_labels_json():
    module = load_script_module()
    workspace = case_dir("open3d-script")
    source = workspace / "points.json"
    labels_path = workspace / "labels.json"
    config_path = workspace / "open3d_rule_segmentation_config.json"
    source.write_text(json.dumps({"points": [[0, 0, 0], [0, 0, 0.1], [100, 100, 100]]}), encoding="utf-8")
    config_path.write_text(
        json.dumps(
            {
                "segmentation_id": "scan-room-a-baseline",
                "source_slice": str(source),
                "rules": [{"label": "noise", "method": "statistical_outlier"}],
                "labels_path": str(labels_path),
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main([str(config_path)])

    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["segmentation_id"] == "scan-room-a-baseline"
    assert payload["source_slice"] == str(source)
    assert payload["labels"][2] == {"point_index": 2, "label": "noise"}
