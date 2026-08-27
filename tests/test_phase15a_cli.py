import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import pc_system.commands.phase15 as phase15_commands
from pc_system.cli import main
from pc_system.cli_parser import build_parser
from pc_system.model_matching_audit import (
    load_operation,
    read_verified_operation_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "models" / "minimal.obj"


def test_cli_create_asset_and_import_version(tmp_path):
    assert main([
        "create-model-asset", "--project-root", str(tmp_path),
        "--model-id", "pump-a", "--display-name", "Pump A",
        "--category-id", "pump", "--manufacturer", "Acme",
        "--model-number", "A-100", "--keyword", "centrifugal",
        "--tag", "pump", "--actor", "alice",
        "--operation-id", "op-model-001", "--request-id", "request-model-001",
        "--idempotency-key", "idem-model-001",
    ]) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"supplier": "Acme"}), encoding="utf-8")
    assert main([
        "import-model", "--project-root", str(tmp_path),
        "--model-id", "pump-a", "--version-id", "v1",
        "--source", str(FIXTURE), "--unit", "mm", "--license", "internal",
        "--provenance", str(provenance), "--actor", "alice",
        "--operation-id", "op-import-001", "--request-id", "request-import-001",
        "--idempotency-key", "idem-import-001",
    ]) == 0
    assert (tmp_path / "models" / "pump-a" / "versions" / "v1" / "model_manifest.json").is_file()


def test_cli_rejects_invalid_model_identifier_before_writing(tmp_path):
    assert main([
        "create-model-asset", "--project-root", str(tmp_path),
        "--model-id", "../escape", "--display-name", "Bad",
        "--category-id", "pump", "--actor", "alice",
        "--operation-id", "op-model-001", "--request-id", "request-model-001",
        "--idempotency-key", "idem-model-001",
    ]) == 2
    assert not (tmp_path.parent / "escape").exists()


def test_cli_invalid_model_identifier_is_audited_before_rejection(tmp_path, capsys):
    assert main([
        "create-model-asset", "--project-root", str(tmp_path),
        "--model-id", "../escape", "--display-name", "Bad",
        "--category-id", "pump", "--actor", "alice",
        "--operation-id", "op-invalid-model", "--request-id", "request-invalid-model",
        "--idempotency-key", "idem-invalid-model",
    ]) == 2

    assert load_operation(tmp_path, "op-invalid-model")["status"] == "failed"
    assert capsys.readouterr().err.startswith("invalid_model_asset: ")
    assert not (tmp_path / "models").exists()


def _create_asset_arguments(project_root: Path) -> list[str]:
    return [
        "create-model-asset", "--project-root", str(project_root),
        "--model-id", "pump-a", "--display-name", "Pump A",
        "--category-id", "pump", "--actor", "alice",
        "--operation-id", "op-model-001", "--request-id", "request-model-001",
        "--idempotency-key", "idem-model-001",
    ]


def _import_arguments(project_root: Path, provenance: Path | None = None) -> list[str]:
    arguments = [
        "import-model", "--project-root", str(project_root),
        "--model-id", "pump-a", "--version-id", "v1", "--source", str(FIXTURE),
        "--unit", "mm", "--license", "internal", "--actor", "alice",
        "--operation-id", "op-import-001", "--request-id", "request-import-001",
        "--idempotency-key", "idem-import-001",
    ]
    if provenance is not None:
        arguments.extend(["--provenance", str(provenance)])
    return arguments


def _provenance_rejection_snapshot(project_root: Path) -> dict:
    snapshots = []
    root = project_root / "reports" / "model_matching_operations"
    for candidate in root.iterdir():
        snapshot = read_verified_operation_snapshot(project_root, candidate.name)
        operation = snapshot["operation"]
        if operation["error"] and operation["error"]["code"] == "invalid_model_provenance":
            snapshots.append(snapshot)
    assert len(snapshots) == 1
    return snapshots[0]


@pytest.mark.parametrize(
    "payload",
    ["{", "[]", "null", '{"score":NaN}', '{"score":Infinity}', '{"supplier":"A","supplier":"B"}'],
)
def test_cli_rejects_unsafe_provenance_file(tmp_path, payload, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text(payload, encoding="utf-8")

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err == (
        "invalid_model_provenance: Model provenance must be a safe JSON object.\n"
    )
    assert not (tmp_path / "models" / "pump-a" / "versions" / "v1").exists()


def test_cli_rejects_non_utf8_provenance_file(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_bytes(b'{"supplier":"\xff"}')

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")


def test_cli_audits_provenance_path_with_unpaired_surrogate(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    unsafe_path = Path(str(tmp_path / "provenance-") + "\ud800.json")

    assert main(_import_arguments(tmp_path, unsafe_path)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")
    snapshot = _provenance_rejection_snapshot(tmp_path)
    assert snapshot["operation"]["status"] == "failed"
    assert snapshot["operation"]["error"]["code"] == "invalid_model_provenance"


def test_cli_audits_provenance_rejection_without_recording_secret_input(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "raw-provenance-path-secret.json"
    provenance.write_text('{"secret":"raw-content-secret"', encoding="utf-8")
    arguments = _import_arguments(tmp_path, provenance)
    arguments[arguments.index("op-import-001")] = "raw-operation-secret"
    arguments[arguments.index("request-import-001")] = "raw-request-secret"
    arguments[arguments.index("idem-import-001")] = "raw-idempotency-secret"

    assert main(arguments) == 2
    assert capsys.readouterr().err == (
        "invalid_model_provenance: Model provenance must be a safe JSON object.\n"
    )

    snapshot = _provenance_rejection_snapshot(tmp_path)
    operation = snapshot["operation"]
    assert operation["operation_type"] == "model_version.import"
    assert operation["status"] == "failed"
    assert operation["error"]["code"] == "invalid_model_provenance"
    assert operation["operation_id"] != "raw-operation-secret"
    assert [event["event_type"] for event in snapshot["events"]] == [
        "operation.started", "operation.failed"
    ]
    audit_bytes = b"".join(
        path.read_bytes()
        for path in (tmp_path / "reports").rglob("*")
        if path.is_file()
    )
    for secret in (
        b"raw-provenance-path-secret",
        b"raw-content-secret",
        b"raw-operation-secret",
        b"raw-request-secret",
        b"raw-idempotency-secret",
    ):
        assert secret not in audit_bytes


def test_cli_audits_missing_provenance_file_before_domain(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0

    assert main(_import_arguments(tmp_path, tmp_path / "missing-provenance.json")) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")
    assert _provenance_rejection_snapshot(tmp_path)["operation"]["status"] == "failed"


@pytest.mark.parametrize("payload", ["1e999", "-1e999", "{\"nested\":[1e999]}"])
def test_cli_rejects_overflowing_provenance_numbers(tmp_path, payload, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text(payload, encoding="utf-8")

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")
    assert _provenance_rejection_snapshot(tmp_path)["operation"]["status"] == "failed"


def test_cli_maps_recursive_provenance_to_stable_rejection(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text("[" * 1200 + "]" * 1200, encoding="utf-8")

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")
    assert _provenance_rejection_snapshot(tmp_path)["operation"]["status"] == "failed"


def test_cli_fails_closed_when_provenance_rejection_audit_cannot_finish(
    tmp_path, monkeypatch, capsys
):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text("{", encoding="utf-8")

    def fail_audit(*_args, **_kwargs):
        raise OSError("audit write failed")

    monkeypatch.setattr(phase15_commands, "fail_operation", fail_audit)

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("audit_persistence_error: ")


def test_cli_fails_closed_when_provenance_rejection_audit_cannot_start(
    tmp_path, monkeypatch, capsys
):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text("{", encoding="utf-8")

    def fail_audit(*_args, **_kwargs):
        raise OSError("audit start failed")

    monkeypatch.setattr(phase15_commands, "start_operation", fail_audit)

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("audit_persistence_error: ")


def test_cli_fails_closed_on_generated_provenance_audit_collision(
    tmp_path, monkeypatch, capsys
):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        phase15_commands.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="a" * 32),
    )

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")
    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("audit_persistence_error: ")


def test_cli_rejects_oversized_provenance_file(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_bytes(b"{" + b"x" * (1024 * 1024) + b"}")

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")


def test_cli_rejects_reparse_point_provenance_file(tmp_path, monkeypatch, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0
    provenance = tmp_path / "provenance.json"
    provenance.write_text('{"supplier":"Acme"}', encoding="utf-8")
    original_lstat = Path.lstat

    def marked_reparse_point(path: Path):
        details = original_lstat(path)
        if path == provenance:
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_size=details.st_size,
                st_dev=details.st_dev,
                st_ino=details.st_ino,
                st_file_attributes=getattr(
                    phase15_commands.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
                ),
            )
        return details

    monkeypatch.setattr(Path, "lstat", marked_reparse_point)

    assert main(_import_arguments(tmp_path, provenance)) == 2
    assert capsys.readouterr().err.startswith("invalid_model_provenance: ")


def test_cli_import_without_provenance_uses_empty_object_and_prints_manifest(tmp_path, capsys):
    assert main(_create_asset_arguments(tmp_path)) == 0

    assert main(_import_arguments(tmp_path)) == 0
    manifest_path = tmp_path / "models" / "pump-a" / "versions" / "v1" / "model_manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["provenance"] == {}
    assert str(manifest_path) in capsys.readouterr().out


def test_cli_replays_model_mutations_idempotently(tmp_path):
    create = _create_asset_arguments(tmp_path)
    assert main(create) == 0
    assert main(create) == 0

    import_version = _import_arguments(tmp_path)
    assert main(import_version) == 0
    assert main(import_version) == 0


def test_cli_preserves_keyword_and_tag_argument_order(tmp_path):
    arguments = _create_asset_arguments(tmp_path)
    arguments[arguments.index("--actor"):arguments.index("--actor")] = [
        "--keyword", "first", "--keyword", "second",
        "--tag", "approved", "--tag", "catalog",
    ]

    assert main(arguments) == 0
    asset = json.loads(
        (tmp_path / "models" / "pump-a" / "model_asset.json").read_text(encoding="utf-8")
    )
    assert asset["keywords"] == ["first", "second"]
    assert asset["tags"] == ["approved", "catalog"]


@pytest.mark.parametrize("unit", ["inch", "MM"])
def test_import_model_parser_rejects_unknown_units(unit, tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([
            "import-model", "--project-root", str(tmp_path), "--model-id", "pump-a",
            "--version-id", "v1", "--source", str(FIXTURE), "--unit", unit,
            "--license", "internal", "--actor", "alice", "--operation-id", "op-1",
            "--request-id", "request-1", "--idempotency-key", "idem-1",
        ])
    assert exc_info.value.code == 2


def test_create_model_asset_parser_requires_audit_identifiers(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([
            "create-model-asset", "--project-root", str(tmp_path),
            "--model-id", "pump-a", "--display-name", "Pump A", "--category-id", "pump",
            "--actor", "alice",
        ])
    assert exc_info.value.code == 2


def test_import_model_parser_requires_unit(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([
            "import-model", "--project-root", str(tmp_path), "--model-id", "pump-a",
            "--version-id", "v1", "--source", str(FIXTURE), "--license", "internal",
            "--actor", "alice", "--operation-id", "op-1", "--request-id", "request-1",
            "--idempotency-key", "idem-1",
        ])
    assert exc_info.value.code == 2
