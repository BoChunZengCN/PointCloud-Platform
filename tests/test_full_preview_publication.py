"""完整 LAS 分层预览版本发布与回滚门禁。"""

import json
import struct
from pathlib import Path

import pytest


def _small_las() -> bytes:
    header = bytearray(227)
    header[:4] = b"LASF"
    header[24:26] = bytes((1, 2))
    struct.pack_into("<H", header, 94, 227)
    struct.pack_into("<I", header, 96, 227)
    header[104] = 2
    struct.pack_into("<H", header, 105, 26)
    struct.pack_into("<I", header, 107, 2)
    struct.pack_into("<3d", header, 131, 0.01, 0.01, 0.01)
    struct.pack_into("<3d", header, 155, 0.0, 0.0, 0.0)
    struct.pack_into("<6d", header, 179, 2.0, 0.0, 3.0, 1.0, 4.0, 2.0)
    return bytes(header) + bytes(52)


def _inputs(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "machine zoom.las"
    source.write_bytes(_small_las())
    converter = tmp_path / "converter.exe"
    converter.write_bytes(b"fake converter")
    return project, source, converter


def _fake_convert(source, converter, run_dir):
    from pc_system.full_preview_conversion import ValidatedPotreeOutput

    run_dir.mkdir()
    (run_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (run_dir / "hierarchy.bin").write_bytes(b"h")
    (run_dir / "octree.bin").write_bytes(b"o")
    return ValidatedPotreeOutput(
        run_dir, run_dir / "metadata.json", source.point_count, source.bounds, True
    )


def test_success_publishes_immutable_run_and_audit(tmp_path, monkeypatch):
    import pc_system.full_preview_publication as publication

    monkeypatch.setattr(publication, "convert_and_validate", _fake_convert)
    project, source, converter = _inputs(tmp_path)
    result = publication.prepare_full_preview(project, source, "machine-zoom", converter)
    active = publication.get_active_preview(project, "machine-zoom")
    assert active == result
    assert result.metadata_url == f"/previews/machine-zoom/runs/{result.run_id}/metadata.json"
    assert result.manifest["source_format"] == "las"
    assert result.manifest["representation_kind"] == "point_cloud"
    assert result.manifest["viewer_format"] == "potree2"
    assert result.manifest["source_point_count"] == 2
    assert len(publication.list_preview_versions(project, "machine-zoom")) == 1
    events = [json.loads(line) for line in (project / "workspace/previews/machine-zoom/audit.jsonl").read_text().splitlines()]
    assert {event["event"] for event in events} >= {"prepare_started", "publish_intent"}


def test_failed_conversion_never_replaces_active_pointer(tmp_path, monkeypatch):
    import pc_system.full_preview_publication as publication

    project, source, converter = _inputs(tmp_path)
    monkeypatch.setattr(publication, "convert_and_validate", _fake_convert)
    first = publication.prepare_full_preview(project, source, "machine-zoom", converter)

    def fail(_source, _converter, run_dir):
        run_dir.mkdir()
        raise RuntimeError("synthetic converter failure")

    monkeypatch.setattr(publication, "convert_and_validate", fail)
    with pytest.raises(RuntimeError, match="synthetic converter failure"):
        publication.prepare_full_preview(project, source, "machine-zoom", converter)
    assert publication.get_active_preview(project, "machine-zoom") == first
    assert len(publication.list_preview_versions(project, "machine-zoom")) == 1


def test_concurrent_same_asset_prepare_is_rejected(tmp_path, monkeypatch):
    import pc_system.full_preview_publication as publication

    project, source, converter = _inputs(tmp_path)
    lock = project / "workspace/previews/machine-zoom/.prepare-lock"
    lock.mkdir(parents=True)
    monkeypatch.setattr(publication, "convert_and_validate", _fake_convert)
    with pytest.raises(RuntimeError, match="正在准备"):
        publication.prepare_full_preview(project, source, "machine-zoom", converter)
    assert publication.get_active_preview(project, "machine-zoom") is None


def test_rollback_only_to_completed_run(tmp_path, monkeypatch):
    import pc_system.full_preview_publication as publication

    project, source, converter = _inputs(tmp_path)
    monkeypatch.setattr(publication, "convert_and_validate", _fake_convert)
    first = publication.prepare_full_preview(project, source, "machine-zoom", converter)
    second = publication.prepare_full_preview(project, source, "machine-zoom", converter)
    assert first.run_id != second.run_id
    assert publication.get_active_preview(project, "machine-zoom") == second
    assert publication.activate_preview_version(project, "machine-zoom", first.run_id) == first
    assert publication.get_active_preview(project, "machine-zoom") == first
    with pytest.raises(ValueError, match="已完成"):
        publication.activate_preview_version(project, "machine-zoom", "unknown-run")


def test_incomplete_or_corrupt_pointer_is_not_active_after_restart(tmp_path, monkeypatch):
    import pc_system.full_preview_publication as publication

    project, source, converter = _inputs(tmp_path)
    monkeypatch.setattr(publication, "convert_and_validate", _fake_convert)
    publication.prepare_full_preview(project, source, "machine-zoom", converter)
    pointer = project / "workspace/previews/machine-zoom/active.json"
    pointer.write_text('{"run_id":', encoding="utf-8")
    assert publication.get_active_preview(project, "machine-zoom") is None
    assert len(publication.list_preview_versions(project, "machine-zoom")) == 1


def test_corrupt_manifest_cannot_become_active_after_restart(tmp_path, monkeypatch):
    import pc_system.full_preview_publication as publication

    project, source, converter = _inputs(tmp_path)
    monkeypatch.setattr(publication, "convert_and_validate", _fake_convert)
    published = publication.prepare_full_preview(project, source, "machine-zoom", converter)
    manifest = published.run_dir / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["metadata_url"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert publication.get_active_preview(project, "machine-zoom") is None
    assert publication.list_preview_versions(project, "machine-zoom") == []
