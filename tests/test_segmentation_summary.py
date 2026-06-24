import json
from pathlib import Path
from uuid import uuid4

from pc_system.segmentation_summary import build_segmentation_summary, write_segmentation_summary


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_segmentation_summary_counts_labels_by_name():
    labels = {
        "segmentation_id": "scan-room-a-baseline",
        "source_slice": "C:/out/scan-room-a.las",
        "rules": [{"label": "ground", "method": "height_threshold"}],
        "labels": [
            {"point_index": 0, "label": "ground"},
            {"point_index": 1, "label": "ground"},
            {"point_index": 2, "label": "noise"},
        ],
    }

    summary = build_segmentation_summary(labels)

    assert summary["segmentation_id"] == "scan-room-a-baseline"
    assert summary["source_slice"] == "C:/out/scan-room-a.las"
    assert summary["total_labeled_points"] == 3
    assert summary["label_counts"] == {"ground": 2, "noise": 1}


def test_write_segmentation_summary_outputs_json_and_html():
    workspace = case_dir("segmentation-summary")
    summary = {
        "segmentation_id": "scan-room-a-baseline",
        "source_slice": "C:/out/scan-room-a.las",
        "total_labeled_points": 1,
        "label_counts": {"<img src=x onerror=alert(1)>": 1},
    }

    outputs = write_segmentation_summary(summary, workspace)

    saved = json.loads(outputs["json"].read_text(encoding="utf-8"))
    html = outputs["html"].read_text(encoding="utf-8")
    assert saved["label_counts"] == {"<img src=x onerror=alert(1)>": 1}
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html
