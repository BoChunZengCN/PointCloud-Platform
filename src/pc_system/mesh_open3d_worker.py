"""管理员配置的 Open3D 外部进程适配器；永不使用 Shell。"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from pc_system.mesh_worker_contract import validate_mesh_worker_result
from pc_system.scan_to_mesh_errors import ScanToMeshError


Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Open3DSubprocessWorker:
    command: tuple[str, ...]
    runner: Runner = subprocess.run

    def __post_init__(self) -> None:
        if (
            type(self.command) is not tuple
            or not self.command
            or any(type(value) is not str or not value for value in self.command)
        ):
            raise ValueError("管理员工作器命令必须是固定非空参数元组。")

    def __call__(self, request: dict, workspace: Path) -> dict:
        timeout = request.get("limits", {}).get("timeout_seconds")
        if type(timeout) is not int or timeout <= 0:
            raise ScanToMeshError("mesh_worker_request_invalid", "工作器超时配置无效。")
        try:
            completed = self.runner(
                [*self.command, "--request", "worker_request.json"],
                cwd=Path(workspace),
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScanToMeshError("mesh_worker_failed", "Open3D 工作器未完成。") from exc
        if completed.returncode != 0:
            raise ScanToMeshError("mesh_worker_failed", "Open3D 工作器返回失败。")
        return validate_mesh_worker_result(workspace, request)
