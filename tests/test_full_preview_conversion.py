"""完整 LAS 转换门禁：假转换器只生成可控的 Potree 2.x 产物。"""

import hashlib
import json
import struct
import subprocess
from pathlib import Path

import pytest

from pc_system.full_preview_source import LasSource


def _source(tmp_path: Path) -> LasSource:
    path = tmp_path / "source.las"
    path.write_bytes(b"unchanged source")
    return LasSource(
        path=path,
        asset_id="machine-zoom",
        size=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        point_count=3,
        point_format=2,
        bounds={"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]},
        unit="m",
    )


def _write_output(run_dir: Path, **changes: object) -> None:
    metadata = {
        "type": "Potree",
        "version": "2.0",
        "points": 3,
        "encoding": "DEFAULT",
        "hierarchy": {"firstChunkSize": 22, "stepSize": 4, "depth": 0},
        "boundingBox": {"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]},
        "attributes": [
            {"name": "position", "size": 12, "numElements": 3, "elementSize": 4},
            {"name": "rgb", "size": 6, "numElements": 3, "elementSize": 2},
        ],
    }
    metadata.update(changes)
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (run_dir / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 3, 0, 54))
    (run_dir / "octree.bin").write_bytes(bytes(54))


def _execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer=_write_output):
    from pc_system.full_preview_conversion import convert_and_validate

    source = _source(tmp_path)
    converter = tmp_path / "PotreeConverter.exe"
    converter.write_bytes(b"fake executable")
    run_dir = tmp_path / "run-1"

    def fake_run(command, **kwargs):
        assert command == [str(converter), "-i", str(source.path), "-o", str(run_dir)]
        assert kwargs["shell"] is False
        assert kwargs["timeout"] > 0
        writer(run_dir)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return convert_and_validate(source, converter, run_dir)


def test_valid_output_is_verified_without_rewriting_source(tmp_path, monkeypatch):
    result = _execute(tmp_path, monkeypatch)
    assert result.point_count == 3
    assert result.rgb_available is True
    assert result.metadata_file.name == "metadata.json"
    assert result.run_dir == tmp_path / "run-1"
    assert (tmp_path / "source.las").read_bytes() == b"unchanged source"


def test_missing_converter_rejected(tmp_path):
    from pc_system.full_preview_conversion import convert_and_validate

    with pytest.raises(ValueError, match="转换器"):
        convert_and_validate(_source(tmp_path), tmp_path / "missing.exe", tmp_path / "run")


def test_nonzero_converter_exit_rejected(tmp_path, monkeypatch):
    from pc_system.full_preview_conversion import convert_and_validate

    source = _source(tmp_path)
    converter = tmp_path / "converter.exe"
    converter.write_bytes(b"fake")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 9),
    )
    with pytest.raises(RuntimeError, match="退出码 9"):
        convert_and_validate(source, converter, tmp_path / "run")


@pytest.mark.parametrize("filename", ["metadata.json", "hierarchy.bin", "octree.bin"])
def test_missing_artifact_rejected(tmp_path, monkeypatch, filename):
    def write_missing(run_dir):
        _write_output(run_dir)
        (run_dir / filename).unlink()

    with pytest.raises(ValueError, match="缺失"):
        _execute(tmp_path, monkeypatch, write_missing)


@pytest.mark.parametrize(
    "changes",
    [
        {"points": 2},
        {"attributes": [{"name": "position", "size": 12}]},
        {"boundingBox": {"min": [100.0, 2.0, 3.0], "max": [101.0, 5.0, 6.0]}},
        {"hierarchy": {"firstChunkSize": 44, "stepSize": 4, "depth": 0}},
    ],
)
def test_invalid_metadata_rejected(tmp_path, monkeypatch, changes):
    def write_bad(run_dir):
        _write_output(run_dir, **changes)

    with pytest.raises(ValueError):
        _execute(tmp_path, monkeypatch, write_bad)


def test_out_of_range_hierarchy_offset_rejected(tmp_path, monkeypatch):
    def write_bad(run_dir):
        _write_output(run_dir)
        (run_dir / "hierarchy.bin").write_bytes(struct.pack("<BBIQQ", 1, 0, 3, 55, 54))

    with pytest.raises(ValueError, match="偏移"):
        _execute(tmp_path, monkeypatch, write_bad)


def test_empty_inner_node_with_geometry_child_is_accepted(tmp_path, monkeypatch):
    def write_inner(run_dir):
        _write_output(run_dir, hierarchy={"firstChunkSize": 44, "stepSize": 4, "depth": 1})
        root = struct.pack("<BBIQQ", 0, 1, 0, 0, 0)
        child = struct.pack("<BBIQQ", 1, 0, 3, 0, 54)
        (run_dir / "hierarchy.bin").write_bytes(root + child)

    assert _execute(tmp_path, monkeypatch, write_inner).point_count == 3


def test_source_change_during_conversion_rejected(tmp_path, monkeypatch):
    def write_changed(run_dir):
        _write_output(run_dir)
        (tmp_path / "source.las").write_bytes(b"modified source")

    with pytest.raises(ValueError, match="来源.*变化"):
        _execute(tmp_path, monkeypatch, write_changed)
