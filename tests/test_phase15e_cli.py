import json

from pc_system.cli import main
from phase15e_support import scan_source


def _create_scan(root, capsys):
    assert main([
        "create-model-asset", "--project-root", str(root), "--model-id", "scan-pump",
        "--display-name", "扫描泵", "--category-id", "pump", "--actor", "alice",
        "--operation-id", "asset-scan", "--request-id", "req-asset", "--idempotency-key", "idem-asset",
        "--source-family", "scanned_reference",
    ]) == 0
    capsys.readouterr()


def test_cli_reference_commands_use_trusted_roles_and_same_safe_projection(tmp_path, capsys):
    """Missing commands, local role escalation, or uncropped list/show output must fail this flow."""
    _create_scan(tmp_path, capsys)
    source = scan_source(tmp_path / "scan.ply")
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"source": "内部采集", "notes": "不公开"}), encoding="utf-8")

    assert main([
        "model-reference-import", "--project-root", str(tmp_path), "--model-id", "scan-pump", "--version-id", "v1",
        "--source", str(source), "--unit", "m", "--license", "内部扫描授权正文", "--provenance", str(provenance),
        "--actor", "alice", "--operation-id", "import-1", "--request-id", "req-import-1", "--idempotency-key", "idem-import-1",
    ]) == 0
    capsys.readouterr()
    assert main([
        "model-reference-list", "--project-root", str(tmp_path), "--model-id", "scan-pump", "--actor", "operator",
    ]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["items"] == [{
        "model_id": "scan-pump", "display_name": "扫描泵", "version_id": "v1", "source_kind": "scanned_reference",
        "quality_status": "passed", "review_status": "pending", "publication_status": "unpublished", "index_status": "not_indexed",
        "risk_summary": ["review_pending"],
    }]
    assert main([
        "model-reference-show", "--project-root", str(tmp_path), "--model-id", "scan-pump", "--version-id", "v1", "--actor", "operator",
    ]) == 0
    assert "内部扫描授权正文" not in capsys.readouterr().out
    assert main([
        "model-reference-review", "--project-root", str(tmp_path), "--model-id", "scan-pump", "--version-id", "v1",
        "--decision", "approved", "--reason", "核对通过", "--acknowledgement", "single_object",
        "--acknowledgement", "metadata_and_rights", "--acknowledgement", "coverage_limitations", "--actor", "alice",
        "--operation-id", "review-1", "--request-id", "req-review-1", "--idempotency-key", "idem-review-1",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "approved"
