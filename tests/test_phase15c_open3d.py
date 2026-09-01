from pathlib import Path
from types import SimpleNamespace

import pytest

import pc_system.model_registration_open3d as registration_open3d
from pc_system.model_matching_errors import ModelMatchingError


def test_open3d_loader_fails_closed_when_dependency_is_missing(monkeypatch):
    monkeypatch.setattr(registration_open3d, "_import_open3d", lambda: None)

    with pytest.raises(ModelMatchingError) as captured:
        registration_open3d.load_open3d_registration_engine()

    assert captured.value.code == "registration_engine_unavailable"


@pytest.mark.parametrize("version", ["0.18.0", "1.0.0", "unknown"])
def test_open3d_loader_rejects_incompatible_versions(monkeypatch, version):
    monkeypatch.setattr(
        registration_open3d,
        "_import_open3d",
        lambda: SimpleNamespace(__version__=version),
    )

    with pytest.raises(ModelMatchingError) as captured:
        registration_open3d.load_open3d_registration_engine()

    assert captured.value.code == "registration_engine_unavailable"


def test_open3d_loader_describes_supported_production_engine(monkeypatch):
    module = SimpleNamespace(__version__="0.19.1")
    monkeypatch.setattr(registration_open3d, "_import_open3d", lambda: module)

    engine = registration_open3d.load_open3d_registration_engine()

    assert engine.describe().name == "open3d"
    assert engine.describe().version == "0.19.1"
    assert engine.describe().production is True


def test_unknown_engine_name_fails_without_importing_open3d(monkeypatch):
    monkeypatch.setattr(
        registration_open3d,
        "_import_open3d",
        lambda: (_ for _ in ()).throw(AssertionError("must not import")),
    )

    with pytest.raises(ModelMatchingError) as captured:
        registration_open3d.resolve_registration_engine("unknown")

    assert captured.value.code == "registration_engine_unavailable"


def test_registration_dependency_is_optional_and_version_bounded():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"

    assert 'registration = ["open3d>=0.19,<1"]' in pyproject.read_text(
        encoding="utf-8"
    )


def test_transformed_coarse_source_reestimates_normals(monkeypatch):
    engine = registration_open3d.Open3DRegistrationEngine(
        SimpleNamespace(__version__="0.19.1")
    )
    calls = []

    class Cloud:
        def transform(self, matrix):
            calls.append(("transform", matrix.tolist()))

    cloud = Cloud()
    monkeypatch.setattr(engine, "_cloud", lambda _points: cloud)
    monkeypatch.setattr(
        engine,
        "_with_normals",
        lambda value, voxel, config: calls.append(("normals", voxel)) or value,
    )

    result = engine._coarse_source(
        {"source_down": SimpleNamespace(points=[[0.0, 0.0, 0.0]])},
        registration_open3d.np.eye(4),
        {"preprocessing": {}},
        0.08,
    )

    assert result is cloud
    assert [item[0] for item in calls] == ["transform", "normals"]
