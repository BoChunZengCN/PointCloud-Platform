import json

from pc_system.cli import main
from phase14_helpers import write_completed_run


def test_cli_create_apply_submit_publish(tmp_path):
    write_completed_run(tmp_path)
    assert (
        main(
            [
                "create-segmentation-correction",
                "--project-root",
                str(tmp_path),
                "--asset-id",
                "scan",
                "--run-id",
                "run-001",
                "--session-id",
                "session-001",
                "--sample-id",
                "sample-001",
                "--actor",
                "alice",
            ]
        )
        == 0
    )
    operation = tmp_path / "operation.json"
    operation.write_text(
        json.dumps(
            {
                "type": "relabel",
                "instance_ids": ["obj-001"],
                "class_id": "pipe",
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "apply-segmentation-correction",
                "--project-root",
                str(tmp_path),
                "--asset-id",
                "scan",
                "--session-id",
                "session-001",
                "--actor",
                "alice",
                "--expected-revision",
                "0",
                "--client-request-id",
                "request-1",
                "--operation",
                str(operation),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "submit-segmentation-correction",
                "--project-root",
                str(tmp_path),
                "--asset-id",
                "scan",
                "--session-id",
                "session-001",
                "--actor",
                "alice",
                "--expected-revision",
                "1",
            ]
        )
        == 0
    )
    publication = tmp_path / "publication.json"
    publication.write_text(
        json.dumps(
            {
                "release_id": "release-001",
                "reviewer": "bob",
                "expected_revision": 2,
                "benchmark_split": "development",
                "license": "internal",
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "publish-segmentation-correction",
                "--project-root",
                str(tmp_path),
                "--asset-id",
                "scan",
                "--session-id",
                "session-001",
                "--publication",
                str(publication),
            ]
        )
        == 0
    )
    assert (
        tmp_path
        / "reports"
        / "segmentation_correction_releases"
        / "scan"
        / "release-001"
        / "correction_release.json"
    ).is_file()


def test_cli_invalid_operation_returns_validation_exit_code(tmp_path, capsys):
    write_completed_run(tmp_path)
    operation = tmp_path / "operation.json"
    operation.write_text('{"type":"unknown"}', encoding="utf-8")
    assert (
        main(
            [
                "create-segmentation-correction",
                "--project-root",
                str(tmp_path),
                "--asset-id",
                "scan",
                "--run-id",
                "run-001",
                "--session-id",
                "session-001",
                "--sample-id",
                "sample-001",
                "--actor",
                "alice",
            ]
        )
        == 0
    )

    exit_code = main(
        [
            "apply-segmentation-correction",
            "--project-root",
            str(tmp_path),
            "--asset-id",
            "scan",
            "--session-id",
            "session-001",
            "--actor",
            "alice",
            "--expected-revision",
            "0",
            "--client-request-id",
            "request-bad",
            "--operation",
            str(operation),
        ]
    )

    assert exit_code == 2
    assert "Unsupported correction operation" in capsys.readouterr().err
