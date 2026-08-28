import json
from pathlib import Path

import pytest

import pc_system.commands.phase15 as phase15_commands
from pc_system.cli import main
from pc_system.model_import import import_model_version
from pc_system.model_library import create_model_asset
from pc_system.model_matching_audit import read_verified_operation_snapshot
from pc_system.model_matching_identity import Principal
from pc_system.model_sampling import list_sampled_representations


EXPERT = Principal("alice", frozenset({"expert"}), "configured_token")
FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def _reader(_path):
    return {
        "vertices": [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
        "faces": [[0, 1, 2]],
    }


def _prepare(project_root):
    create_model_asset(
        project_root,
        model_id="pump-a",
        display_name="Pump A",
        category_id="pump",
        manufacturer="Acme",
        model_number="A-100",
        keywords=["centrifugal"],
        tags=["pump"],
        principal=EXPERT,
        operation_id="op-asset-cli-sample",
        request_id="req-asset-cli-sample",
        idempotency_key="idem-asset-cli-sample",
    )
    import_model_version(
        project_root,
        model_id="pump-a",
        version_id="v1",
        source_path=FIXTURE,
        declared_unit="mm",
        license_name="internal",
        provenance={"supplier": "Acme"},
        principal=EXPERT,
        operation_id="op-import-cli-sample",
        request_id="req-import-cli-sample",
        idempotency_key="idem-import-cli-sample",
        mesh_reader=_reader,
    )


def _sample_args(root, *, sequence=1, point_count=16, random_seed=11):
    return [
        "sample-model-version",
        "--project-root", str(root),
        "--model-id", "pump-a",
        "--version-id", "v1",
        "--point-count", str(point_count),
        "--random-seed", str(random_seed),
        "--actor", "alice",
        "--operation-id", f"op-sample-cli-{sequence:03d}",
        "--request-id", f"req-sample-cli-{sequence:03d}",
        "--idempotency-key", f"idem-sample-cli-{sequence:03d}",
    ]


def test_sampling_cli_requires_explicit_point_count_and_seed(tmp_path):
    base = [
        "sample-model-version",
        "--project-root", str(tmp_path),
        "--model-id", "pump-a",
        "--version-id", "v1",
        "--actor", "alice",
        "--operation-id", "op-sample-required",
        "--request-id", "req-sample-required",
        "--idempotency-key", "idem-sample-required",
    ]
    with pytest.raises(SystemExit) as missing_count:
        main([*base, "--random-seed", "11"])
    assert missing_count.value.code == 2
    with pytest.raises(SystemExit) as missing_seed:
        main([*base, "--point-count", "16"])
    assert missing_seed.value.code == 2


def test_sampling_cli_outputs_verified_representation_path(tmp_path, monkeypatch, capsys):
    _prepare(tmp_path)
    monkeypatch.setattr(phase15_commands, "trimesh_mesh_reader", _reader)

    assert main(_sample_args(tmp_path)) == 0

    output = capsys.readouterr()
    path = Path(output.out.strip())
    assert output.err == ""
    assert path.name == "representation.json"
    assert path.is_file()
    representations = list_sampled_representations(tmp_path, "pump-a", "v1")
    assert [item["point_count"] for item in representations] == [16]


def test_sampling_cli_reuses_same_config_deterministically(tmp_path, monkeypatch, capsys):
    _prepare(tmp_path)
    monkeypatch.setattr(phase15_commands, "trimesh_mesh_reader", _reader)
    assert main(_sample_args(tmp_path, sequence=1)) == 0
    first_path = capsys.readouterr().out.strip()

    assert main(_sample_args(tmp_path, sequence=2)) == 0
    second_path = capsys.readouterr().out.strip()

    assert second_path == first_path
    assert len(list_sampled_representations(tmp_path, "pump-a", "v1")) == 1
    snapshot = read_verified_operation_snapshot(tmp_path, "op-sample-cli-002")
    assert [event["event_type"] for event in snapshot["events"]] == [
        "operation.started",
        "model_sampling.representation_reused",
        "operation.completed",
    ]


def test_sampling_cli_invalid_config_returns_stable_exit_two(tmp_path, capsys):
    assert main(_sample_args(tmp_path, point_count=0)) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("invalid_sampling_config:")


def test_list_model_representations_outputs_verified_json(tmp_path, monkeypatch, capsys):
    _prepare(tmp_path)
    monkeypatch.setattr(phase15_commands, "trimesh_mesh_reader", _reader)
    assert main(_sample_args(tmp_path)) == 0
    capsys.readouterr()

    assert main([
        "list-model-representations",
        "--project-root", str(tmp_path),
        "--model-id", "pump-a",
        "--version-id", "v1",
    ]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    values = json.loads(output.out)
    assert len(values) == 1
    assert values[0]["representation_type"] == "cad_sampled"
    assert values[0]["point_count"] == 16
