import json

import pytest

from fastapi.testclient import TestClient

from pc_system.api import create_app
from pc_system.cli import main
from pc_system.reference_store import load_bundle
from phase15e_support import EXPERT, import_request, review_request, scan_asset, scan_source


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


def _release_payload(version_id, release_id, expected_current_release_id):
    return {
        "version_id": version_id,
        "release_id": release_id,
        "action": "activate",
        "expected_current_release_id": expected_current_release_id,
        "rollback_of_release_id": None,
        "reason": "发布已核验扫描参考",
        "operation_id": "release-" + release_id,
        "request_id": "req-release-" + release_id,
        "idempotency_key": "idem-release-" + release_id,
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
    linked = staging / "linked.ply"
    try:
        linked.symlink_to("scan.ply")
    except OSError:
        pytest.skip("测试主机不允许创建文件符号链接")
    link = client.post(
        "/model-library/models/scan-pump/scanned-versions",
        json={**payload, "operation_id": "link", "request_id": "link", "idempotency_key": "link", "staged_source": "imports/models/linked.ply"},
        headers=_headers("expert"),
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert absolute.status_code == traversal.status_code == 400
    assert absolute.json()["detail"]["code"] == traversal.json()["detail"]["code"] == "invalid_staged_source"
    assert link.status_code == 400 and link.json()["detail"]["code"] == "invalid_staged_source"


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
    auditor_import = client.post("/model-library/models/scan-pump/scanned-versions", json=_import_payload("v2"), headers=_headers("auditor"))
    auditor_denied = client.post("/model-library/models/scan-pump/scanned-versions/v1/review", json=review, headers=_headers("auditor"))
    audited = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("auditor"))
    approved = client.post("/model-library/models/scan-pump/scanned-versions/v1/review", json=review, headers=_headers("expert"))
    replayed = client.post("/model-library/models/scan-pump/scanned-versions/v1/review", json=review, headers=_headers("expert"))

    assert denied.status_code == 403
    assert auditor_import.status_code == 403
    assert auditor_denied.status_code == 403
    assert audited.status_code == 200 and audited.json()["license"] == "内部扫描授权正文"
    assert approved.status_code == 201 and approved.json()["decision"] == "approved"
    assert replayed.status_code == 201 and replayed.json() == approved.json()
    public = client.get("/model-library/models/scan-pump")
    assert public.status_code == 200
    assert "内部扫描授权正文" not in json.dumps(public.json(), ensure_ascii=False)


def test_api_catalog_keeps_historical_approved_version_publishable(tmp_path):
    """Collapsing historical approvals into only `published` must fail this filter."""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)
    staging = tmp_path / "imports" / "models"
    scan_source(staging / "scan-v2.ply")
    second = _import_payload("v2")
    second["staged_source"] = "imports/models/scan-v2.ply"
    assert client.post("/model-library/models/scan-pump/scanned-versions", json=second, headers=_headers("expert")).status_code == 201
    for version_id in ("v1", "v2"):
        review = {
            "decision": "approved", "reason": "已核对单对象、来源许可与扫描覆盖范围",
            "acknowledgements": ["single_object", "metadata_and_rights", "coverage_limitations"],
            "operation_id": "review-" + version_id, "request_id": "req-review-" + version_id,
            "idempotency_key": "idem-review-" + version_id,
        }
        assert client.post(f"/model-library/models/scan-pump/scanned-versions/{version_id}/review", json=review, headers=_headers("expert")).status_code == 201
        expected = None if version_id == "v1" else "release-v1"
        assert client.post(
            "/model-library/models/scan-pump/releases", json=_release_payload(version_id, "release-" + version_id, expected), headers=_headers("expert"),
        ).status_code == 201

    publishable = client.get("/model-library/models/scan-pump/scanned-versions?status=publishable", headers=_headers("operator"))
    published = client.get("/model-library/models/scan-pump/scanned-versions?status=published", headers=_headers("operator"))

    assert publishable.status_code == published.status_code == 200
    assert [item["version_id"] for item in publishable.json()["items"]] == ["v1"]
    assert [item["version_id"] for item in published.json()["items"]] == ["v1", "v2"]


@pytest.mark.parametrize("source_family", ["", None, "untrusted_family"])
def test_api_rejects_explicit_invalid_source_family_without_creating_cad_asset(tmp_path, source_family):
    """Defaulting an explicit invalid source family to CAD must not create an asset."""
    client = _client(tmp_path)
    model_id = "bad-family-" + ("null" if source_family is None else ("empty" if not source_family else "other"))

    payload = _asset_payload(model_id, source_family=source_family)
    payload["source_family"] = source_family
    response = client.post("/model-library/models", json=payload, headers=_headers("expert"))

    assert response.status_code == 400
    assert not (tmp_path / "models" / model_id / "model_asset.json").exists()


def test_api_maps_invalid_reference_review_decision_to_bad_request(tmp_path):
    """An invalid review decision is client input, not an internal server error."""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)
    response = client.post(
        "/model-library/models/scan-pump/scanned-versions/v1/review",
        json={
            "decision": "maybe", "reason": "非法决定", "acknowledgements": [],
            "operation_id": "review-invalid", "request_id": "req-review-invalid", "idempotency_key": "idem-review-invalid",
        }, headers=_headers("expert"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "reference_review_invalid"


def test_public_scan_detail_consumes_all_pages_before_projecting_current_release(tmp_path):
    """A one-page public scan projection would omit the last current version and leak no evidence."""
    from pc_system.reference_import import import_reference_version
    from pc_system.reference_review import review_reference_version
    from pc_system.model_release import release_model_version

    scan_asset(tmp_path)
    source = scan_source(tmp_path / "source.ply")
    for sequence in range(100):
        version_id = f"v{sequence:03d}"
        import_reference_version(tmp_path, **import_request(
            source, version_id=version_id, operation_id="import-" + version_id,
            request_id="req-" + version_id, idempotency_key="idem-" + version_id,
        ))
    current = import_reference_version(tmp_path, **import_request(
        source, version_id="z-current", operation_id="import-z", request_id="req-z", idempotency_key="idem-z",
    ))
    review_reference_version(tmp_path, **review_request(
        version_id="z-current", sequence="z", operation_id="review-z", request_id="req-review-z", idempotency_key="idem-review-z",
    ))
    release_model_version(tmp_path, model_id="scan-pump", version_id="z-current", release_id="release-z", action="activate",
        expected_current_release_id=None, rollback_of_release_id=None, reason="发布最后一页扫描版本", principal=EXPERT,
        operation_id="release-z", request_id="req-release-z", idempotency_key="idem-release-z")

    response = _client(tmp_path).get("/model-library/models/scan-pump")

    assert response.status_code == 200
    assert response.json()["version_count"] == 101
    assert response.json()["versions"][-1]["version_id"] == "z-current"
    assert response.json()["current_release"] == {"version_id": current["version_id"], "publication_status": "current"}
    assert "source_path" not in json.dumps(response.json(), ensure_ascii=False)


def test_api_and_cli_operator_list_share_the_same_business_projection(tmp_path, capsys):
    """Divergent transport adapters must not expose different operator catalog data."""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)
    api_projection = client.get(
        "/model-library/models/scan-pump/scanned-versions", headers=_headers("operator")
    ).json()

    assert main([
        "model-reference-list", "--project-root", str(tmp_path), "--model-id", "scan-pump",
        "--actor", "operator", "--role", "operator",
    ]) == 0

    assert json.loads(capsys.readouterr().out) == api_projection


def test_catalog_professional_preview_is_bounded_deterministic_and_role_projected(tmp_path):
    """目录预览必须来自已验证实测点，且业务角色不能取得专业证据。"""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)

    expert_first = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("expert"))
    expert_second = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("expert"))
    auditor_list = client.get("/model-library/models/scan-pump/scanned-versions", headers=_headers("auditor"))
    operator = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("operator"))

    assert expert_first.status_code == expert_second.status_code == auditor_list.status_code == operator.status_code == 200
    preview = expert_first.json()["preview"]
    assert expert_first.json()["viewer_role"] == "expert"
    assert expert_first.json()["dimensions_m"] == [3.0, 3.0, 3.0]
    assert preview == expert_second.json()["preview"]
    assert preview["schema_version"] == "1.0"
    assert preview["coordinate_unit"] == "m"
    assert preview["algorithm"] == "sha256_point_subset_v1"
    assert preview["random_seed"] == 0
    assert preview["point_count"] <= 4096
    assert preview["point_count"] == len(preview["points"])
    assert auditor_list.json()["viewer_role"] == "auditor"
    assert operator.json()["viewer_role"] == "operator"
    assert not {"preview", "quality", "source", "license", "provenance", "review"}.intersection(operator.json())


def test_catalog_preview_over_4096_is_deterministic_bounded_and_uses_only_normalized_measured_points(tmp_path):
    """预览算法不得插值、复制或返回标准化实测点之外的坐标。"""
    client = _client(tmp_path)
    staging = tmp_path / "imports" / "models"
    staging.mkdir(parents=True)
    source = staging / "large.ply"
    points = [(float(index), float(index % 97), float(index % 31)) for index in range(5000)]
    source.write_text(
        "ply\nformat ascii 1.0\nelement vertex 5000\nproperty float x\nproperty float y\nproperty float z\nend_header\n"
        + "".join(f"{x} {y} {z}\n" for x, y, z in points),
        encoding="ascii",
    )
    assert client.post(
        "/model-library/models", json=_asset_payload("large-scan", source_family="scanned_reference"),
        headers=_headers("expert"),
    ).status_code == 201
    payload = {**_import_payload(), "staged_source": "imports/models/large.ply"}
    assert client.post(
        "/model-library/models/large-scan/scanned-versions", json=payload, headers=_headers("expert"),
    ).status_code == 201

    first = client.get("/model-library/models/large-scan/scanned-versions/v1", headers=_headers("expert")).json()["preview"]
    second = client.get("/model-library/models/large-scan/scanned-versions/v1", headers=_headers("expert")).json()["preview"]
    measured = {tuple(point) for point in load_bundle(tmp_path, "large-scan", "v1")["normalized"]["points"]}

    assert first == second
    assert first["source_point_count"] == 5000
    assert first["point_count"] == len(first["points"]) == 4096
    assert all(tuple(point) in measured for point in first["points"])


def test_reference_session_and_professional_release_projection_are_trusted_and_cropped(tmp_path):
    """页面初始角色、发布头和历史只能由服务端只读投影给出。"""
    client = _client(tmp_path)
    _prepare_scan(tmp_path, client)
    for role in ("expert", "auditor", "operator"):
        response = client.get("/model-library/reference-session", headers=_headers(role))
        assert response.status_code == 200
        assert response.json() == {"viewer_role": role}
    review = {"decision":"approved", "reason":"已核对单对象、来源许可与扫描覆盖范围",
              "acknowledgements":["single_object","metadata_and_rights","coverage_limitations"],
              "operation_id":"review-release", "request_id":"req-review-release", "idempotency_key":"idem-review-release"}
    reviewed = client.post("/model-library/models/scan-pump/scanned-versions/v1/review", json=review, headers=_headers("expert"))
    assert reviewed.status_code == 201
    assert client.post("/model-library/models/scan-pump/releases", json=_release_payload("v1", "release-v1", None), headers=_headers("expert")).status_code == 201
    from pc_system.model_release import load_current_model_release
    from pc_system.reference_review import load_reference_review
    expert = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("expert"))
    operator = client.get("/model-library/models/scan-pump/scanned-versions/v1", headers=_headers("operator"))
    immutable_review = load_reference_review(tmp_path, "scan-pump", "v1")
    current = load_current_model_release(tmp_path, "scan-pump")
    assert immutable_review["decision"] == "approved"
    assert immutable_review["acknowledgements"] == sorted(review["acknowledgements"])
    assert current["model_id"] == "scan-pump"
    assert current["version_id"] == "v1"
    assert current["release_id"] == "release-v1"
    assert expert.json()["current_release_id"] == "release-v1"
    assert expert.json()["release_history"][0]["release_id"] == "release-v1"
    assert not {"current_release_id", "release_history"}.intersection(operator.json())
