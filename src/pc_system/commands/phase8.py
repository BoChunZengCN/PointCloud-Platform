import json
import sys
from pathlib import Path

from pc_system.config import ProjectConfig
from pc_system.quality_gate import build_quality_gate, write_quality_gate_report


def run_check_quality_gate(project_root: Path, asset_id: str) -> int:
    """从点云分析报告生成 Phase 8 质量门禁报告。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    analysis_path = paths["reports"] / "analysis" / asset_id / "point_cloud_analysis.json"
    if not analysis_path.exists():
        print(f"Point cloud analysis not found: {analysis_path}", file=sys.stderr)
        return 2
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    gate = build_quality_gate(asset_id, analysis)
    write_quality_gate_report(gate, paths["reports"] / "quality_gates" / asset_id)
    return 0
