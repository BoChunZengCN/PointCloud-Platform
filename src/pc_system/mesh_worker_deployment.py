"""从管理员部署环境解析固定的外部网格工作器。"""

import os
import stat
from pathlib import Path
from typing import Mapping

from pc_system.mesh_open3d_worker import Open3DSubprocessWorker


_PYTHON_KEY = "PC_SYSTEM_MESH_WORKER_PYTHON"
_SCRIPT_KEY = "PC_SYSTEM_MESH_WORKER_SCRIPT"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def _plain_absolute_file(value: str | None, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"网格工作器{label}必须是绝对普通文件路径。")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"网格工作器{label}必须是绝对普通文件路径。")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"网格工作器{label}不存在或无法访问。") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)
    ):
        raise ValueError(f"网格工作器{label}必须是普通文件。")
    return str(path)


def resolve_admin_mesh_worker(
    settings: Mapping[str, str] | None = None,
) -> Open3DSubprocessWorker | None:
    """管理员配置只在进程环境读取，不从项目或业务请求解析命令。"""

    values = os.environ if settings is None else settings
    python_path = values.get(_PYTHON_KEY)
    script_path = values.get(_SCRIPT_KEY)
    if python_path is None and script_path is None:
        return None
    return Open3DSubprocessWorker(
        (
            _plain_absolute_file(python_path, " Python 路径"),
            _plain_absolute_file(script_path, "脚本路径"),
        )
    )
