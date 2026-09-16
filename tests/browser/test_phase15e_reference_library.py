"""扫描参考模板原生工作台的真实浏览器验收。"""

import pytest
from playwright.sync_api import expect


def _open(page_factory, role="expert", model_id=None):
    page = page_factory(role, model_id)
    expect(page.get_by_role("heading", name="扫描参考模板")).to_be_visible()
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
    _fill_import(page)
    page.get_by_role("button", name="创建并导入", exact=True).click()
    expect(page.locator("#detail-title")).to_have_text("<b>扫描泵</b> · v1")
    expect(page.locator("#point-count")).to_have_text("64")
    expect(page.locator("#dimensions")).to_contain_text("3.000000 m")
    expect(page.locator("canvas")).to_have_count(3)
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


@pytest.mark.parametrize("browser_server", ["reference"], indirect=True)
def test_auditor_is_readonly_and_token_stays_out_of_storage(open_reference_library):
    page = _open(open_reference_library, "auditor", "scan-readonly")
    expect(page.locator("#role-note")).to_contain_text("审计员")
    for button in page.locator("[data-write]").all():
        expect(button).to_be_disabled()
    assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    assert "token" not in page.url
