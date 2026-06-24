import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


class GaussianSplattingFailed(RuntimeError):
    """Gaussian Splatting 训练器执行失败时抛出的明确错误。"""


GaussianRunner = Callable[[list[str]], int]


@dataclass(frozen=True)
class GaussianSplatRequest:
    """一次 Gaussian Splatting 训练/导出请求。"""

    name: str
    source_las: Path
    output_dir: Path
    iterations: int = 3000


def subprocess_runner(command: list[str]) -> int:
    """默认训练命令执行器，测试通过 fake runner 避免依赖真实 3DGS 环境。"""

    return subprocess.run(command, check=False).returncode


def build_gaussian_splat_plan(request: GaussianSplatRequest) -> dict:
    """生成 Gaussian Splatting 训练计划。"""

    return {
        "splat_id": request.name,
        "source_las": request.source_las.as_posix(),
        "output_dir": request.output_dir.as_posix(),
        "output": {"model_file": "point_cloud.ply"},
        "training": {"iterations": int(request.iterations)},
        "status": "planned",
    }


def write_gaussian_splat_plan(plan: dict, output_dir: Path) -> Path:
    """写出 Gaussian Splatting 计划 manifest。"""

    return write_json(plan, output_dir / "gaussian_splat_plan.json")


def _build_training_config(plan: dict) -> dict:
    """构建外部训练脚本读取的配置文件。"""

    output_dir = Path(plan["output_dir"])
    return {
        "splat_id": plan["splat_id"],
        "source_las": plan["source_las"],
        "output_dir": output_dir.as_posix(),
        "model_path": (output_dir / plan["output"]["model_file"]).as_posix(),
        "iterations": plan["training"]["iterations"],
    }


def execute_gaussian_splat_plan(
    plan_path: Path,
    trainer_path: Path,
    runner: GaussianRunner = subprocess_runner,
    python_path: Path = Path("python"),
) -> dict:
    """执行 Gaussian Splatting 训练计划，并验证模型文件存在。"""

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    output_dir = Path(plan["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = plan_path.parent / "gaussian_splat_config.json"
    config = _build_training_config(plan)
    write_json(config, config_path)
    exit_code = runner([python_path.as_posix(), trainer_path.as_posix(), str(config_path)])
    if exit_code != 0:
        raise GaussianSplattingFailed(f"Gaussian Splatting trainer failed with exit code {exit_code}")
    model_path = Path(config["model_path"])
    if not model_path.exists():
        raise GaussianSplattingFailed(f"Gaussian Splatting model was not created: {model_path}")
    plan["status"] = "completed"
    plan["execution"] = {
        "engine": "gaussian-splat-trainer",
        "trainer_path": trainer_path.as_posix(),
        "model_path": str(model_path),
    }
    write_json(plan, plan_path)
    return plan
