import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pc_system.config import ProjectConfig


def _registry_path(project_root: Path) -> Path:
    """返回项目资产索引路径。"""

    return ProjectConfig(project_root=project_root).paths()["assets"] / "asset_index.json"


def _load_registry(project_root: Path) -> dict:
    """读取资产索引；缺失时返回空 registry，便于前端先启动。"""

    path = _registry_path(project_root)
    if not path.exists():
        return {"schema_version": "1.0", "asset_count": 0, "assets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _asset_or_404(project_root: Path, asset_id: str) -> dict:
    """从 registry 查找资产；缺失时返回 404。"""

    registry = _load_registry(project_root)
    for asset in registry["assets"]:
        if asset["asset_id"] == asset_id:
            return asset
    raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")


def _read_json_or_404(path: Path, label: str) -> dict:
    """读取 JSON 文件；缺失时返回 API 404。"""

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{label} not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _production_dir(project_root: Path, asset_id: str) -> Path:
    """生产运行报告目录。"""

    return project_root / "reports" / "production_runs" / asset_id


def _file_kind(path: Path) -> str:
    """根据扩展名判断交付物类型。"""

    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".json" and "manifest" in path.name.lower():
        return "manifest"
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".pdf"}:
        return "report"
    return "file"


def _delivery_output(project_root: Path, relative_path: str | None) -> dict:
    """返回单个交付物的真实存在性与类型。"""

    if not relative_path:
        return {"path": "", "exists": False, "kind": "missing"}
    path = project_root / relative_path
    return {"path": relative_path, "exists": path.exists(), "kind": _file_kind(path)}


def create_app(project_root: Path) -> FastAPI:
    """创建最小 API 应用。"""

    app = FastAPI(title="Point Cloud Platform API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """健康检查，返回当前绑定的项目目录。"""

        return {"status": "ok", "project_root": str(project_root)}

    @app.get("/assets")
    def list_assets() -> dict:
        """返回项目资产索引。"""

        return _load_registry(project_root)

    @app.get("/assets/{asset_id}")
    def get_asset(asset_id: str) -> dict:
        """返回单个资产索引条目。"""

        return _asset_or_404(project_root, asset_id)

    @app.get("/runs/{asset_id}/plan")
    def get_run_plan(asset_id: str) -> dict:
        """返回生产运行计划。"""

        return _read_json_or_404(_production_dir(project_root, asset_id) / "production_run_plan.json", "Production run plan")

    @app.get("/runs/{asset_id}/report")
    def get_run_report(asset_id: str) -> dict:
        """返回生产运行报告。"""

        return _read_json_or_404(_production_dir(project_root, asset_id) / "production_run_report.json", "Production run report")

    @app.get("/runs/{asset_id}/jobs")
    def list_jobs(asset_id: str) -> dict:
        """返回资产关联的本地 job 状态列表。"""

        jobs_dir = project_root / "reports" / "jobs" / asset_id
        jobs = []
        if jobs_dir.exists():
            for path in sorted(jobs_dir.glob("*.json")):
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
        return {"asset_id": asset_id, "jobs": jobs}

    @app.get("/reports/{asset_id}")
    def list_reports(asset_id: str) -> dict:
        """返回常用报告路径，前端可直接生成链接。"""

        return {
            "asset_id": asset_id,
            "quality_report": f"reports/{asset_id}/quality_report.html",
            "production_plan": f"reports/production_runs/{asset_id}/production_run_plan.json",
            "production_report": f"reports/production_runs/{asset_id}/production_run_report.json",
            "deployment_checklist": f"reports/deployment/{asset_id}/deployment_checklist.json",
        }

    @app.get("/delivery/{asset_id}/status")
    def get_delivery_status(asset_id: str) -> dict:
        """返回资产交付物的真实文件存在性和类型。"""

        asset = _asset_or_404(project_root, asset_id)
        viewer_paths = asset.get("viewer_paths", {})
        report_paths = asset.get("report_paths", {})
        outputs = {
            "viewer_url": _delivery_output(project_root, viewer_paths.get("viewer_url") or viewer_paths.get("viewer_html_path")),
            "viewer_html_path": _delivery_output(project_root, viewer_paths.get("viewer_html_path") or viewer_paths.get("viewer_url")),
            "manifest_path": _delivery_output(project_root, viewer_paths.get("manifest_path")),
            "potree_manifest_path": _delivery_output(project_root, viewer_paths.get("potree_manifest_path")),
            "report_path": _delivery_output(project_root, viewer_paths.get("report_path") or report_paths.get("production_report")),
            "quality_report": _delivery_output(project_root, report_paths.get("quality_report")),
        }
        return {"asset_id": asset_id, "outputs": outputs}

    @app.get("/deployment/{asset_id}")
    def get_deployment(asset_id: str) -> dict:
        """返回部署交付检查清单。"""

        path = project_root / "reports" / "deployment" / asset_id / "deployment_checklist.json"
        return _read_json_or_404(path, "Deployment checklist")

    return app


app = create_app(Path(os.environ.get("PC_SYSTEM_PROJECT_ROOT", "workspace")))
