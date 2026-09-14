import json

from fastapi.testclient import TestClient

from pc_system.api import create_app
from phase15e_support import scan_source


def _client(root):
    return TestClient(create_app(
        root,
        run_mode="production",
        api_key="phase15e-service-key",
        principal_bindings={
            role + "-token": {"actor_id": role + "-actor", "roles": [role]}
            for role in ("operator", "expert", "auditor")
        },
    ))


def _headers(role):
    return {"x-api-key": role + "-token"}


def _asset_payload(model_id, *, source_family=None):
    value = {
        "model_id": model_id,
        "display_name": "扫描泵",
        "category_id": "pump",
        "manufacturer": "示例厂商",
        "model_number": "P-1",
        "keywords": ["pump"],
        "tags": ["参考"],
        "operation_id": "asset-" + model_id,
        "request_id": "req-asset-" + model_id,
        "idempotency_key": "idem-asset-" + model_id,
    }
    if source_family is not None:
        value["source_family"] = source_family
    return value


def _import_payload(version_id="v1"):
    return {
        "version_id": version_id,
        "staged_source": "imports/models/scan.ply",
        "declared_unit": "m",
        "license": "内部扫描授权正文",
        "provenance": {"source": "内部路径", "notes": "不得对业务角色公开"},
        "operation_id": "import-" + version_id,
        "request_id": "req-import-" + version_id,
        "idempotency_key": "idem-import-" + version_id,
    }


def _prepare_scan(root, client):
    staging = root / "imports" / "models"
    staging.mkdir(parents=True)
    scan_source(staging / "scan.ply")
    assert client.post(
        "/model-library/models", json=_asset_payload("scan-pump", source_family="scanned_reference"),
        headers=_headers("expert"),
    ).status_code == 201
    response = client.post(
        "/model-library/models/scan-pump/scanned-versions",
        json=_import_payload(), headers=_headers("expert"),
    )
    assert response.status_code == 201
    return response.json()


def test_api_creates_scanned_asset_and_preserves_omitted_cad_contract(tmp_path):
    """Dropping source-family dispatch must reject this scan or change old CAD output."""
    client = _client(tmp_path)

    cad = client.post("/model-library/models", json=_asset_payload("cad-pump"), headers=_headers("expert"))
    scan = client.post(
        "/model-library/models", json=_asset_payload("scan-pump", source_family="scanned_reference"),
        headers=_headers("expert"),
    )

    assert cad.status_code == scan.status_code == 201
    assert cad.json()["schema_version"] == "1.0" and "source_family" not in cad.json()
    assert scan.json()["schema_version"] == "1.1"
    assert scan.json()["source_family"] == "scanned_reference"


def test_api_imports_only_controlled_staging_and_replays_same_operation(tmp_path):
    """Replacing the staging resolver or idempotent domain call must fail this boundary test."""
    client = _client(tmp_path)
    staging = tmp_path / "imports" / "models"
    staging.mkdir(parents=True)
    scan_source(staging / "scan.ply")
    assert client.post(
        "/model-library/models", json=_asset_payload("scan-pump", source_family="scanned_reference"),
        headers=_headers("expert"),
    ).status_code == 201
    payload = _import_payload()

    first = client.post("/model-library/models/scan-pump/scanned-versions", json=payload, headers=_headers("expert"))
    replay = client.post("/model-library/models/scan-pump/scanned-versions", json=payload, headers=_headers("expert"))
    absolute = client.post(
        "/model-library/models/scan-pump/scanned-versions",
        json={**payload, "operation_id": "absolute", "request_id": "absolute", "idempotency_key": "absolute", "staged_source": str(staging / "scan.ply")},
        headers=_headers("expert"),
    )
    traversal = client.post(
        "/model-library/models/scan-pump/scanned-versions",
        json={**payload, "operation_id": "traversal", "request_id": "traversal", "idempotency_key": "traversal", "staged_source": "imports/models/../scan.ply"},
        headers=_headers("expert"),
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert absolute.status_code == traversal.status_code == 400
    assert absolute.json()["detail"]["code"] == traversal.json()["detail"]["code"] == "invalid_staged_source"


def test_api_catalog_paginates_binds_cursor_and_crops_operator_details(tmp_path):
    """A cursor without filters/role binding or an uncropped operator response must fail here."""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)
    staging = tmp_path / "imports" / "models"
    scan_source(staging / "scan-v2.ply")
    second = _import_payload("v2")
    second.update({"staged_source": "imports/models/scan-v2.ply"})
    assert client.post("/model-library/models/scan-pump/scanned-versions", json=second, headers=_headers("expert")).status_code == 201

    first = client.get("/model-library/models/scan-pump/scanned-versions?limit=1", headers=_headers("operator"))
    body = first.json()
    second_page = client.get(
        "/model-library/models/scan-pump/scanned-versions?limit=1&cursor=" + body["next_cursor"], headers=_headers("operator"),
    )
    changed_filter = client.get(
        "/model-library/models/scan-pump/scanned-versions?status=publishable&cursor=" + body["next_cursor"], headers=_headers("operator"),
    )
    changed_role = client.get(
        "/model-library/models/scan-pump/scanned-versions?limit=1&cursor=" + body["next_cursor"], headers=_headers("expert"),
    )
    detail = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("operator"))

    assert first.status_code == second_page.status_code == detail.status_code == 200
    assert [item["version_id"] for item in body["items"] + second_page.json()["items"]] == ["v1", "v2"]
    assert changed_filter.status_code == changed_role.status_code == 400
    assert detail.json()["quality_status"] == "passed"
    assert detail.json()["review_status"] == "pending"
    assert detail.json()["publication_status"] == "unpublished"
    assert detail.json()["index_status"] == "not_indexed"
    assert not {"source_path", "license", "provenance", "points"}.intersection(detail.json())


def test_api_enforces_roles_and_never_uses_claimed_body_identity(tmp_path):
    """Allowing a body role to grant write access would make this test pass incorrectly."""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)
    review = {
        "decision": "approved",
        "reason": "已核对单对象、来源许可与扫描覆盖范围",
        "acknowledgements": ["single_object", "metadata_and_rights", "coverage_limitations"],
        "operation_id": "review-1",
        "request_id": "req-review-1",
        "idempotency_key": "idem-review-1",
    }
    denied = client.post("/model-library/models/scan-pump/scanned-versions/v1/review", json={**review, "roles": ["expert"]}, headers=_headers("operator"))
    audited = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("auditor"))
    approved = client.post("/model-library/models/scan-pump/scanned-versions/v1/review", json=review, headers=_headers("expert"))

    assert denied.status_code == 403
    assert audited.status_code == 200 and audited.json()["license"] == "内部扫描授权正文"
    assert approved.status_code == 201 and approved.json()["decision"] == "approved"
    public = client.get("/model-library/models/scan-pump")
    assert public.status_code == 200
    assert "内部扫描授权正文" not in json.dumps(public.json(), ensure_ascii=False)
