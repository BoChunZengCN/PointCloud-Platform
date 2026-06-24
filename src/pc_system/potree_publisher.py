import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pc_system.json_io import write_json


class PotreeConverterNotFound(RuntimeError):
    """找不到 PotreeConverter 时抛出的明确错误。"""


class PotreeConverterFailed(RuntimeError):
    """PotreeConverter 返回非零退出码时抛出的错误。"""


class PotreeMetadataMissing(RuntimeError):
    """PotreeConverter 成功返回但缺少 metadata.json 时抛出的错误。"""


@dataclass(frozen=True)
class PotreePublishRequest:
    """一次 Potree 发布请求。

    这里不直接依赖全局配置，只接收发布所需的最小信息，方便 CLI、
    测试和后续批处理任务复用。
    """

    asset_id: str
    source_file: Path
    output_dir: Path
    converter_path: Path


PotreeRunner = Callable[[list[str]], int]


def subprocess_runner(command: list[str]) -> int:
    """默认外部命令执行器。

    测试时会注入 fake runner；生产环境下这里负责真正调用 PotreeConverter。
    """

    try:
        return subprocess.run(command, check=False).returncode
    except FileNotFoundError as exc:
        raise PotreeConverterNotFound(f"PotreeConverter not found: {command[0]}") from exc


def publish_potree(
    request: PotreePublishRequest,
    runner: PotreeRunner = subprocess_runner,
) -> Path:
    """调用 PotreeConverter 并生成 Potree 发布 manifest。

    数据流：
    LAS/LAZ 源文件 -> PotreeConverter -> potree 输出目录 -> 校验 metadata.json -> potree_manifest.json。
    """

    # 如果 converter_path 包含目录，就必须真实存在；若只是命令名，则交给 PATH 解析。
    if request.converter_path.parent != Path(".") and not request.converter_path.exists():
        raise PotreeConverterNotFound(f"PotreeConverter not found: {request.converter_path}")

    request.output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(request.converter_path),
        str(request.source_file),
        "-o",
        str(request.output_dir),
    ]
    exit_code = runner(command)
    if exit_code != 0:
        raise PotreeConverterFailed(f"PotreeConverter failed with exit code {exit_code}")

    metadata_file = request.output_dir / "metadata.json"
    if not metadata_file.exists():
        raise PotreeMetadataMissing(f"Potree metadata.json was not created: {metadata_file}")
    manifest = {
        "asset_id": request.asset_id,
        "source_file": str(request.source_file),
        "preview_status": "potree_ready",
        "converter": str(request.converter_path),
        "output_dir": str(request.output_dir),
        "metadata_file": str(metadata_file),
    }
    return write_json(manifest, request.output_dir.parent / "potree_manifest.json")
