import json
from pathlib import Path
from uuid import uuid4

import pytest

from pc_system.pdal_slice_adapter import PdalExecutionFailed, build_pdal_pipeline, pdal_slice_adapter


def case_dir(name: str) -> Path:
    # 使用项目内测试输出目录，避免依赖系统临时目录权限。
    path = Path(__file__).resolve().parent / "_output" / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_build_pdal_pipeline_includes_crop_and_optional_voxel_filter():
    # 这个测试锁定 PDAL pipeline 的核心结构：
    # reader -> crop -> voxel downsample -> writer。
    plan = {
        "source_file": "C:/data/scan.las",
        "crop_bounds": {"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]},
        "voxel_size": 0.05,
    }
    destination = Path("C:/out/scan-room-a.las")

    pipeline = build_pdal_pipeline(plan, destination)

    assert pipeline["pipeline"][0] == {"type": "readers.las", "filename": "C:/data/scan.las"}
    assert pipeline["pipeline"][1] == {
        "type": "filters.crop",
        "bounds": "([1.0,4.0],[2.0,5.0],[3.0,6.0])",
    }
    assert pipeline["pipeline"][2] == {"type": "filters.voxelcenternearestneighbor", "cell": 0.05}
    assert pipeline["pipeline"][3] == {"type": "writers.las", "filename": "C:/out/scan-room-a.las"}


def test_pdal_slice_adapter_writes_pipeline_and_invokes_runner():
    workspace = case_dir("pdal-adapter")
    destination = workspace / "scan-room-a.las"
    calls = []
    plan = {
        "source_file": "C:/data/scan.las",
        "crop_bounds": {"min": [1, 2, 3], "max": [4, 5, 6]},
        "voxel_size": None,
    }

    def fake_runner(command: list[str]) -> int:
        calls.append(command)
        destination.write_text("LAS slice placeholder", encoding="utf-8")
        return 0

    result = pdal_slice_adapter(plan, destination, pdal_path=Path("pdal"), runner=fake_runner)

    pipeline_path = workspace / "pdal_pipeline.json"
    assert calls == [["pdal", "pipeline", str(pipeline_path)]]
    assert json.loads(pipeline_path.read_text(encoding="utf-8"))["pipeline"][-1]["filename"] == destination.as_posix()
    assert result.engine == "pdal"
    assert result.output_path == destination


def test_pdal_slice_adapter_raises_clear_error_on_nonzero_exit():
    workspace = case_dir("pdal-failure")
    plan = {
        "source_file": "C:/data/scan.las",
        "crop_bounds": {"min": [1, 2, 3], "max": [4, 5, 6]},
        "voxel_size": None,
    }

    def fake_runner(command: list[str]) -> int:
        return 9

    with pytest.raises(PdalExecutionFailed, match="exit code 9"):
        pdal_slice_adapter(plan, workspace / "slice.las", runner=fake_runner)

