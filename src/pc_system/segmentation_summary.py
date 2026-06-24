from collections import Counter
from html import escape
from pathlib import Path

from pc_system.json_io import write_json


def build_segmentation_summary(labels_payload: dict) -> dict:
    """根据 labels JSON 生成分割摘要。

    labels JSON 是算法执行后的统一结果格式；这里先只统计标签数量，
    后续真实分割算法接入后可以继续扩展面积、体量、置信度等指标。
    """

    counter = Counter(str(item["label"]) for item in labels_payload.get("labels", []))
    return {
        "segmentation_id": labels_payload["segmentation_id"],
        "source_slice": labels_payload["source_slice"],
        "total_labeled_points": sum(counter.values()),
        "label_counts": dict(counter),
    }


def _render_summary_html(summary: dict) -> str:
    """渲染可人工查看的 HTML 摘要，并转义所有动态内容。"""

    rows = "\n".join(
        f"<tr><td>{escape(str(label))}</td><td>{escape(str(count))}</td></tr>"
        for label, count in summary["label_counts"].items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Segmentation Summary</title>
</head>
<body>
  <h1>{escape(str(summary["segmentation_id"]))}</h1>
  <p>Source slice: {escape(str(summary["source_slice"]))}</p>
  <p>Total labeled points: {escape(str(summary["total_labeled_points"]))}</p>
  <table>
    <thead><tr><th>Label</th><th>Count</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""


def write_segmentation_summary(summary: dict, output_dir: Path) -> dict[str, Path]:
    """写出分割摘要 JSON 和 HTML 报告。"""

    json_path = write_json(summary, output_dir / "segmentation_summary.json")
    html_path = output_dir / "segmentation_summary.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(_render_summary_html(summary), encoding="utf-8")
    return {"json": json_path, "html": html_path}
