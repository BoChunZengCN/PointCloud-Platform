import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


class FlsIngestFailed(RuntimeError):
    """FLS 转 LAS 外部转换失败时抛出的明确错误。"""


FlsRunner = Callable[[list[str]], int]


@dataclass(frozen=True)
class FlsIngestRequest:
    """FLS 原始站点数据接入请求。"""

    asset_id: str
    raw_files: list[Path]
    output_las: Path
    registration: str = "external"


def subprocess_runner(command: list[str]) -> int:
    """默认 FLS 转换命令执行器，测试时通过 fake runner 注入。"""

    return subprocess.run(command, check=False).returncode


def build_fls_ingest_plan(request: FlsIngestRequest) -> dict:
    """生成 FLS 原始数据转 LAS 的执行计划。"""

    return {
        "asset_id": request.asset_id,
        "raw_format": "fls",
        "raw_files": [path.as_posix() for path in request.raw_files],
        "output_las": request.output_las.as_posix(),
        "registration": request.registration,
        "status": "planned",
    }


def write_fls_ingest_plan(plan: dict, output_dir: Path) -> Path:
    """写出 FLS 接入计划 manifest。"""

    return write_json(plan, output_dir / "fls_ingest_plan.json")


def execute_fls_ingest_plan(
    plan_path: Path,
    converter_path: Path,
    runner: FlsRunner = subprocess_runner,
) -> dict:
    """执行 FLS 接入计划，并验证转换后的 LAS 文件存在。"""

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    output_las = Path(plan["output_las"])
    output_las.parent.mkdir(parents=True, exist_ok=True)
    command = [converter_path.as_posix(), "--output", str(output_las), *plan["raw_files"]]
    exit_code = runner(command)
    if exit_code != 0:
        raise FlsIngestFailed(f"FLS ingest converter failed with exit code {exit_code}")
    if not output_las.exists():
        raise FlsIngestFailed(f"FLS ingest output LAS was not created: {output_las}")
    plan["status"] = "completed"
    plan["execution"] = {
        "engine": "fls-converter",
        "converter_path": converter_path.as_posix(),
        "output_las": str(output_las),
    }
    write_json(plan, plan_path)
    return plan
