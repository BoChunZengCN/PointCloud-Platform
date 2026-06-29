import os
from pathlib import Path
from typing import Callable

from pc_system.config import ProjectConfig
from pc_system.production_hardening import build_consistency_report, write_consistency_report

ServerRunner = Callable[[str, str, int], None]


def build_api_environment(project_root: Path, api_key: str | None, run_mode: str) -> dict[str, str]:
    """构造 serve-api 所需环境变量，便于 CLI 和测试复用。"""

    env = {
        "PC_SYSTEM_PROJECT_ROOT": str(project_root),
        "PC_SYSTEM_RUN_MODE": run_mode,
    }
    if api_key:
        env["PC_SYSTEM_API_KEY"] = api_key
    return env


def run_serve_api(
    project_root: Path,
    host: str,
    port: int,
    api_key: str | None,
    run_mode: str,
    server_runner: ServerRunner | None = None,
    dry_run: bool = False,
) -> int:
    """启动 FastAPI 服务；dry-run 只校验配置并返回。"""

    ProjectConfig(project_root=project_root).ensure_directories()
    env = build_api_environment(project_root, api_key, run_mode)
    os.environ.update(env)
    if dry_run:
        return 0
    if server_runner is None:
        import uvicorn

        server_runner = lambda app_path, host, port: uvicorn.run(app_path, host=host, port=port)
    server_runner("pc_system.api:app", host, port)
    return 0


def run_check_consistency(project_root: Path, asset_id: str) -> int:
    """生成 workspace 数据一致性检查报告。"""

    paths = ProjectConfig(project_root=project_root).ensure_directories()
    report = build_consistency_report(project_root, asset_id)
    write_consistency_report(report, paths["reports"] / "consistency" / asset_id)
    return 0
