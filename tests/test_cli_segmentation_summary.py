import json
from pathlib import Path
from uuid import uuid4

from pc_system.cli import main


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避开 Windows 环境下 pytest tmp_path 权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_cli_report_rule_segment_reads_labels_and_writes_summary_report():
    workspace = case_dir("cli-report-rule-segment")
    project = workspace / "workspace"
    segment_dir = project / "data" / "assets" / "scan" / "slices" / "room-a" / "segments" / "baseline"
    segment_dir.mkdir(parents=True)
    (segment_dir / "scan-room-a-baseline-labels.json").write_text(
        json.dumps(
            {
                "segmentation_id": "scan-room-a-baseline",
                "source_slice": "C:/out/scan-room-a.las",
                "rules": [],
                "labels": [
                    {"point_index": 0, "label": "ground"},
                    {"point_index": 1, "label": "plane"},
                    {"point_index": 2, "label": "ground"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (segment_dir / "rule_segmentation_plan.json").write_text(
        json.dumps(
            {
                "output": {"file_name": "scan-room-a-baseline-labels.json"},
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "report-rule-segment",
            "--project-root",
            str(project),
            "--asset-id",
            "scan",
            "--slice-name",
            "room-a",
            "--name",
            "baseline",
        ]
    )

    report_dir = project / "reports" / "scan" / "segments" / "room-a" / "baseline"
    saved = json.loads((report_dir / "segmentation_summary.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert saved["total_labeled_points"] == 3
    assert saved["label_counts"] == {"ground": 2, "plane": 1}
    assert (report_dir / "segmentation_summary.html").exists()
