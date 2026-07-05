from pathlib import Path
from typing import Any


def _kind(path: Path) -> str:
    """根据文件扩展名和名称推断报告类型。"""

    if path.suffix.lower() == ".html":
        return "html"
    if path.suffix.lower() == ".md":
        return "markdown"
    if path.suffix.lower() == ".json":
        return "json"
    return "file"


def build_report_center(project_root: Path) -> dict[str, Any]:
    """扫描 reports/delivery 目录，生成统一报告中心索引。"""

    roots = [project_root / "reports", project_root / "delivery"]
    reports = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".html"}:
                continue
            reports.append({"name": path.name, "path": path.relative_to(project_root).as_posix(), "kind": _kind(path)})
    return {"schema_version": "1.0", "module": "Report Center", "report_count": len(reports), "reports": reports}
