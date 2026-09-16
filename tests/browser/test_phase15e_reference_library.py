"""扫描参考模板原生工作台的真实浏览器验收。"""

import json

import pytest
from playwright.sync_api import expect


def _open(page_factory, role="expert", model_id=None, *, connect=True):
    page = page_factory(role, model_id)
    expect(page.get_by_role("heading", name="扫描参考模板")).to_be_visible()
    if connect:
        page.locator("details.connection summary").click()
        page.locator("#token").fill(role + "-token")
        page.get_by_role("button", name="连接", exact=True).click()
        expect(page.locator("#role-note")).not_to_contain_text("尚未连接")
    return page


def _fill_import(page):
    page.locator("#model-id").fill("scan-browser")
    page.locator("#display-name").fill("<b>扫描泵</b>")
    page.locator("#category-id").fill("pump")
    page.locator("#manufacturer").fill("示例厂商")
    page.locator("#model-number").fill("P-1")
    page.locator("#keywords").fill("pump")
    page.locator("#tags").fill("参考")
    page.locator("#version-id").fill("v1")
    page.locator("#staged-source").fill("imports/models/browser-scan.ply")
    page.locator("#license").fill("内部扫描授权")
    page.locator("#provenance").fill('{"source":"浏览器夹具"}')


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_expert_imports_reviews_publishes_and_sees_bounded_projections(open_reference_library, browser_server):
    page = _open(open_reference_library)
    authorization = []
    page.on("request", lambda request: authorization.append(request.headers.get("authorization")) if request.url.endswith("/model-library/models") else None)
    _fill_import(page)
    page.get_by_role("button", name="创建并导入", exact=True).click()
    expect(page.locator("#detail-title")).to_have_text("<b>扫描泵</b> · v1")
    expect(page.locator("#point-count")).to_have_text("64")
    expect(page.locator("#dimensions")).to_contain_text("3.000000 m")
    expect(page.locator("canvas")).to_have_count(3)
    expect(page.locator("#detail-title b")).to_have_count(0)
    expect(page.get_by_role("button", name="确认通过", exact=True)).to_be_disabled()
    for declaration in ("single_object", "metadata_and_rights", "coverage_limitations"):
        page.locator("#" + declaration).check()
    page.locator("#review-reason").fill("已核对单对象、来源许可与扫描覆盖范围")
    expect(page.get_by_role("button", name="确认通过", exact=True)).to_be_enabled()
    page.get_by_role("button", name="确认通过", exact=True).click()
    page.locator("#release-reason").fill("发布已核验扫描参考")
    page.get_by_role("button", name="发布模板", exact=True).click()
    expect(page.get_by_text("模板已发布，索引需要更新", exact=True)).to_be_visible()
    assert (browser_server["project_root"] / "models" / "scan-browser" / "reviews" / "v1" / "review.json").exists()
    assert authorization == ["Bearer expert-token"]


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_existing_asset_import_and_dynamic_warning_acknowledgements(open_reference_library, browser_server):
    page = _open(open_reference_library, model_id="scan-readonly")
    page.locator("#create-new-asset").uncheck()
    page.locator("#model-id").fill("scan-readonly")
    page.locator("#version-id").fill("warning-v2")
    page.locator("#staged-source").fill("imports/models/warning-scan.ply")
    page.locator("#license").fill("内部扫描授权")
    page.locator("#provenance").fill('{"source":"平面扫描"}')
    requests = []
    page.on("request", lambda request: requests.append(request) if request.method == "POST" else None)
    page.get_by_role("button", name="创建并导入", exact=True).click()
    expect(page.locator("#detail-title")).to_contain_text("warning-v2")
    assert not any(request.url.endswith("/model-library/models") for request in requests)
    risk = page.locator('#risk-acknowledgements input[data-risk-code="geometry_planar"]')
    expect(risk).to_have_count(1)
    for declaration in ("single_object", "metadata_and_rights", "coverage_limitations"):
        page.locator("#" + declaration).check()
    page.locator("#review-reason").fill("已核对动态风险")
    expect(page.get_by_role("button", name="确认通过", exact=True)).to_be_disabled()
    risk.check()
    with page.expect_request(lambda request: request.url.endswith("/review") and request.method == "POST") as captured:
        page.get_by_role("button", name="确认通过", exact=True).click()
    assert sorted(captured.value.post_data_json["acknowledgements"]) == [
        "coverage_limitations", "geometry_planar", "metadata_and_rights", "single_object",
    ]
    expect(page.locator("#message")).to_have_text("操作已保存。")
    review = json.loads((browser_server["project_root"] / "models" / "scan-readonly" / "reviews" / "warning-v2" / "review.json").read_text(encoding="utf-8"))
    assert review["decision"] == "approved"
    assert sorted(review["acknowledgements"]) == sorted(captured.value.post_data_json["acknowledgements"])


@pytest.mark.parametrize("browser_server", ["reference-history"], indirect=True)
def test_filters_and_real_release_history(open_reference_library):
    page = _open(open_reference_library, model_id="scan-readonly")
    expected = {
        "全部": 4,
        "待核验": 1,
        "可发布": 1,
        "已发布": 2,
        "未通过": 1,
    }
    for label, count in expected.items():
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.locator("#catalog button")).to_have_count(count)
    page.get_by_role("button", name="全部", exact=True).click()
    expect(page.locator("#catalog button")).to_have_count(4)
    page.locator("#catalog button").filter(has_text="扫描泵 · v2").click()
    expect(page.locator("#publication-note")).to_contain_text("release-history-v1")
    expect(page.locator("#publication-note")).to_contain_text("release-history-v2")


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_auditor_is_readonly_and_token_stays_out_of_storage(open_reference_library):
    page = _open(open_reference_library, "auditor", "scan-readonly")
    expect(page.locator("#role-note")).to_contain_text("审计员")
    page.get_by_role("button", name="全部", exact=True).click()
    page.locator("#catalog button").filter(has_text="扫描泵 · v1").click()
    expect(page.locator("#quality-summary")).to_contain_text("自动质量")
    for button in page.locator("[data-write]").all():
        expect(button).to_be_disabled()
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    assert "token" not in page.url

    operator = _open(open_reference_library, "operator", "scan-readonly")
    operator.get_by_role("button", name="全部", exact=True).click()
    operator.locator("#catalog button").filter(has_text="扫描泵 · v1").click()
    expect(operator.locator("#quality-summary")).to_have_text("自动质量：业务安全摘要")
    expect(operator.locator("#source-summary")).to_have_text("来源：业务安全摘要")


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_changing_model_id_invalidates_loaded_detail_and_frozen_write_identity(open_reference_library):
    page = _open(open_reference_library, "expert", "scan-readonly")
    page.get_by_role("button", name="全部", exact=True).click()
    page.locator("#catalog button").filter(has_text="扫描泵 · v1").click()
    for declaration in ("single_object", "metadata_and_rights", "coverage_limitations"):
        page.locator("#" + declaration).check()
    page.locator("#review-reason").fill("先确认 A/v1")
    expect(page.get_by_role("button", name="确认通过", exact=True)).to_be_enabled()

    page.locator("#model-id").fill("other-model")

    expect(page.locator("#detail")).to_be_hidden()
    expect(page.locator("#approve")).to_be_disabled()
    expect(page.locator("#reject")).to_be_disabled()
    expect(page.locator("#release")).to_be_disabled()
    expect(page.locator("#risk-acknowledgements input")).to_have_count(0)


@pytest.mark.parametrize("browser_server", ["reference-history"], indirect=True)
def test_reconnecting_to_lower_role_clears_professional_detail_and_preview(open_reference_library):
    page = _open(open_reference_library, "expert", "scan-readonly")
    page.get_by_role("button", name="全部", exact=True).click()
    page.locator("#catalog button").filter(has_text="扫描泵 · v2").click()
    expect(page.locator("#source-summary")).to_contain_text("来源：ply")
    expect(page.locator("#publication-note")).to_contain_text("release-history-v2")

    page.locator("#token").fill("operator-token")
    page.get_by_role("button", name="连接", exact=True).click()

    expect(page.locator("#role-note")).to_contain_text("业务角色")
    expect(page.locator("#detail")).to_be_hidden()
    expect(page.locator("#source-summary")).to_have_text("")
    expect(page.locator("#quality-summary")).to_have_text("")
    expect(page.locator("#publication-note")).to_have_text("")
    expect(page.locator("#risk-acknowledgements input")).to_have_count(0)


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_token_connection_is_memory_only_and_initially_disables_writes(open_reference_library):
    page = _open(open_reference_library, "expert", connect=False)
    expect(page.get_by_role("button", name="创建并导入", exact=True)).to_be_disabled()
    console = []
    requests = []
    page.on("console", lambda message: console.append(message.text))
    page.on("request", lambda request: requests.append({"url": request.url, "authorization": request.headers.get("authorization")}))
    page.locator("details.connection summary").click()
    page.locator("#token").fill("expert-token")
    page.get_by_role("button", name="连接", exact=True).click()
    expect(page.locator("#role-note")).to_contain_text("专家")
    assert page.locator("#token").input_value() == ""
    assert "expert-token" not in page.content()
    assert "expert-token" not in page.url
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    assert all("expert-token" not in entry["url"] for entry in requests)
    assert all("expert-token" not in line for line in console)
    assert any(entry["authorization"] == "Bearer expert-token" for entry in requests)


@pytest.mark.parametrize("browser_server", ["reference-slow"], indirect=True)
def test_slow_write_disables_connection_filters_catalog_and_all_writes(open_reference_library):
    page = _open(open_reference_library, model_id="scan-readonly")
    page.locator("#create-new-asset").uncheck()
    page.locator("#version-id").fill("slow-v2")
    page.locator("#staged-source").fill("imports/models/browser-scan.ply")
    page.locator("#license").fill("内部扫描授权")
    page.locator("#provenance").fill("{}")
    page.get_by_role("button", name="创建并导入", exact=True).click()
    expect(page.locator("#message")).to_contain_text("正在提交")
    expect(page.locator("#connect")).to_be_disabled()
    for button in page.locator("[data-status]").all():
        expect(button).to_be_disabled()
    for button in page.locator("#catalog button").all():
        expect(button).to_be_disabled()
    for button in page.locator("[data-write]").all():
        expect(button).to_be_disabled()
    expect(page.locator("#detail-title")).to_contain_text("slow-v2")


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_processed_then_aborted_create_retries_identical_envelope_and_continues_frozen_import(
    open_reference_library, browser_server
):
    page = _open(open_reference_library)
    _fill_import(page)
    captured = []

    def lose_first_response(route, request):
        captured.append(json.loads(request.post_data))
        if len(captured) == 1:
            response = route.fetch()
            assert response.status == 201
            route.abort()
        elif len(captured) == 2:
            route.fulfill(status=409, content_type="application/json", body=json.dumps({
                "detail":{"code":"operation_busy", "message":"原操作仍在恢复"},
            }))
        else:
            route.continue_()

    page.route("**/model-library/models", lose_first_response)
    page.get_by_role("button", name="创建并导入", exact=True).click()
    expect(page.locator("#retry")).to_be_visible()
    expect(page.get_by_role("button", name="创建并导入", exact=True)).to_be_disabled()
    page.get_by_role("button", name="重试原操作", exact=True).click()
    expect(page.locator("#retry")).to_be_visible()
    expect(page.locator("#message")).to_contain_text("请求结果未确认")
    page.get_by_role("button", name="重试原操作", exact=True).click()
    expect(page.locator("#detail-title")).to_have_text("<b>扫描泵</b> · v1")
    assert len(captured) == 3
    assert captured[0] == captured[1] == captured[2]
    assert (browser_server["project_root"] / "models" / "scan-browser" / "model_asset.json").exists()
    assert (browser_server["project_root"] / "models" / "scan-browser" / "versions" / "v1" / "commit.json").exists()
    assert len(list((browser_server["project_root"] / "models" / "scan-browser" / "versions").iterdir())) == 1


@pytest.mark.parametrize("browser_server", ["reference-labels"], indirect=True)
def test_business_and_professional_workbenches_label_legacy_cad(open_workbench):
    business = open_workbench("operator", professional=False)
    business.get_by_test_id("decision-row").first.click()
    expect(business.locator("#candidate-summary")).to_contain_text("来源：CAD 采样")
    professional = open_workbench("expert", professional=True)
    professional.get_by_test_id("decision-row").first.click()
    expect(professional.locator("#candidate-summary")).to_contain_text("来源：CAD 采样")
