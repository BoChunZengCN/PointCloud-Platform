from pathlib import Path

import pytest

from pc_system.las_reader import LasReaderDependencyError, read_las_info


class FakeHeader:
    point_count = 42
    mins = [1.0, 2.0, 3.0]
    maxs = [4.0, 5.0, 6.0]
    scales = [0.001, 0.001, 0.001]
    offsets = [100.0, 200.0, 300.0]
    point_format = "3"

    def parse_crs(self):
        return "EPSG:4978"


class FakeLas:
    header = FakeHeader()
    point_format = type(
        "FakePointFormat",
        (),
        {"dimension_names": ["X", "Y", "Z", "red", "green", "blue", "classification"]},
    )()


class FakeLaspy:
    @staticmethod
    def read(path):
        return FakeLas()


def test_read_las_info_from_injected_laspy_module():
    info = read_las_info(Path("scan.las"), laspy_module=FakeLaspy)

    assert info.point_count == 42
    assert info.bounds == {"min": [1.0, 2.0, 3.0], "max": [4.0, 5.0, 6.0]}
    assert info.has_rgb is True
    assert info.has_classification is True
    assert info.has_crs is True
    assert info.scale == [0.001, 0.001, 0.001]
    assert info.offset == [100.0, 200.0, 300.0]
    assert info.point_format == "3"


def test_read_las_info_raises_clear_error_when_laspy_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "laspy":
            raise ModuleNotFoundError("No module named 'laspy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(LasReaderDependencyError, match="Install laspy"):
        read_las_info(Path("scan.las"))
