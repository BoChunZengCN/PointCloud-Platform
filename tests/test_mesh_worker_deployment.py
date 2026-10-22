"""部署管理员的工作器配置不进入业务请求。"""

import sys
from pathlib import Path

import pytest

from pc_system.mesh_open3d_worker import Open3DSubprocessWorker
from pc_system.mesh_worker_deployment import resolve_admin_mesh_worker


def test_worker_is_unavailable_without_admin_configuration():
    assert resolve_admin_mesh_worker({}) is None


def test_admin_configuration_uses_only_two_fixed_absolute_files(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text("# 部署脚本占位\n", encoding="utf-8")
    executable = str(Path(sys.executable).resolve())
    settings = {
        "PC_SYSTEM_MESH_WORKER_PYTHON": executable,
        "PC_SYSTEM_MESH_WORKER_SCRIPT": str(script),
    }

    worker = resolve_admin_mesh_worker(settings)

    assert isinstance(worker, Open3DSubprocessWorker)
    assert worker.command == (executable, str(script))
    for invalid in (
        {"PC_SYSTEM_MESH_WORKER_PYTHON": executable},
        {"PC_SYSTEM_MESH_WORKER_SCRIPT": str(script)},
        {**settings, "PC_SYSTEM_MESH_WORKER_SCRIPT": "worker.py"},
        {**settings, "PC_SYSTEM_MESH_WORKER_SCRIPT": str(tmp_path / "absent.py")},
    ):
        with pytest.raises(ValueError, match="网格工作器"):
            resolve_admin_mesh_worker(invalid)
