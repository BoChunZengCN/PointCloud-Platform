import json
from pathlib import Path
from uuid import uuid4

import pytest

from pc_system.potree_publisher import (
    PotreeConverterNotFound,
    PotreeMetadataMissing,
    PotreePublishRequest,
    publish_potree,
)


def case_dir(name: str) -> Path:
    # 测试输出放在项目内，避免 Windows 环境下系统临时目录权限问题。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_publish_potree_calls_converter_and_writes_manifest():
    # M4 Potree 发布契约：
    # 给定源 LAS 和输出目录，调用外部转换器，并记录转换结果 manifest。
    workspace = case_dir("potree-publish")
    source = workspace / "scan.las"
    source.write_text("LAS placeholder", encoding="utf-8")
    output_dir = workspace / "potree"
    calls = []

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "metadata.json").write_text("{}", encoding="utf-8")
        return 0

    manifest_path = publish_potree(
        PotreePublishRequest(
            asset_id="scan",
            source_file=source,
            output_dir=output_dir,
            converter_path=Path("PotreeConverter.exe"),
        ),
        runner=fake_runner,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert calls == [["PotreeConverter.exe", str(source), "-o", str(output_dir)]]
    assert manifest["asset_id"] == "scan"
    assert manifest["preview_status"] == "potree_ready"
    assert manifest["metadata_file"] == str(output_dir / "metadata.json")


def test_publish_potree_raises_clear_error_when_converter_missing():
    workspace = case_dir("potree-missing")

    with pytest.raises(PotreeConverterNotFound, match="PotreeConverter"):
        publish_potree(
            PotreePublishRequest(
                asset_id="scan",
                source_file=workspace / "scan.las",
                output_dir=workspace / "potree",
                converter_path=workspace / "missing.exe",
            )
        )


def test_publish_potree_fails_when_metadata_json_missing():
    # 可靠性要求：转换器退出码为 0 但没有 metadata.json 时，不应标记 potree_ready。
    workspace = case_dir("potree-metadata-missing")
    source = workspace / "scan.las"
    source.write_text("LAS placeholder", encoding="utf-8")
    converter = workspace / "PotreeConverter.exe"
    converter.write_text("placeholder", encoding="utf-8")

    def fake_runner(command: list[str]) -> int:
        return 0

    with pytest.raises(PotreeMetadataMissing, match="metadata.json"):
        publish_potree(
            PotreePublishRequest(
                asset_id="scan",
                source_file=source,
                output_dir=workspace / "potree",
                converter_path=converter,
            ),
            runner=fake_runner,
        )
