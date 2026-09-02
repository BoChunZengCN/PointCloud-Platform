import pytest
from playwright.sync_api import expect

from pc_system.model_match_decision import list_decision_bundles
from pc_system.model_decision_queue import load_model_decision_item
from phase15d_support import AUDITOR


def select_first(page):
    page.get_by_test_id("decision-row").first.click()
    expect(page.get_by_test_id("case-status")).to_have_text("待处理")


def post_action(page, label, path_suffix):
    with page.expect_response(lambda response: response.url.endswith(path_suffix) and response.request.method == "POST", timeout=30000) as pending:
        page.get_by_role("button", name=label, exact=True).click()
    return pending.value


def test_operator_confirms_and_server_binding_matches_page(open_workbench, browser_server):
    page = open_workbench()
    select_first(page)
    page.get_by_test_id("decision-reason").fill("现场核验一致")
    assert post_action(page, "确认", "/model-matching/decisions").status == 201
    expect(page.get_by_test_id("case-status")).to_have_text("已处理")
    bundle = list_decision_bundles(browser_server["project_root"])[0]
    expect(page.get_by_test_id("binding-id")).to_have_text(bundle["binding"]["binding_id"])
    page.get_by_role("button", name="已处理", exact=False).click()
    expect(page.get_by_test_id("decision-row")).to_have_count(1)
    page.get_by_role("button", name="全部", exact=False).click()
    expect(page.get_by_test_id("decision-row")).to_have_count(1)


def test_candidate_rejection_then_no_match_and_filters(open_workbench, browser_server):
    page = open_workbench()
    select_first(page)
    page.get_by_test_id("decision-reason").fill("候选不对应实物")
    assert post_action(page, "拒绝", "/model-matching/decisions").status == 201
    expect(page.get_by_test_id("case-status")).to_have_text("待处理")
    expect(page.get_by_role("button", name="确认", exact=True)).to_be_disabled()
    assert post_action(page, "无匹配", "/model-matching/decisions").status == 201
    expect(page.get_by_test_id("case-status")).to_have_text("已处理")
    assert all(bundle["binding"] is None for bundle in list_decision_bundles(browser_server["project_root"]))
    page.get_by_role("button", name="全部", exact=False).click()
    page.locator("#asset-filter").fill("not-present")
    page.get_by_role("button", name="应用筛选").click()
    expect(page.locator("#message")).to_contain_text("暂无符合条件")
    expect(page.get_by_test_id("decision-row")).to_have_count(0)


@pytest.mark.parametrize("browser_server", ["review_required"], indirect=True)
def test_expert_review_rerun_replace_restore_and_auditor_readonly(open_workbench, browser_server):
    business = open_workbench()
    select_first(business)
    expect(business.get_by_role("button", name="确认", exact=True)).to_be_disabled()
    page = open_workbench("expert", professional=True)
    select_first(page)
    page.get_by_test_id("decision-reason").fill("专家复核通过")
    page.locator("#scope").select_option("expert_pose")
    assert post_action(page, "确认", "/model-matching/decisions").status == 201
    expect(page.get_by_test_id("case-status")).to_have_text("已处理")
    first = page.get_by_test_id("binding-id").inner_text()
    expect(page.locator("#registration-config option")).not_to_have_count(0)
    assert post_action(page, "重新配准", "/model-matching/registrations").status == 200
    expect(page.get_by_test_id("case-status")).to_have_text("待处理")
    options = page.locator("#candidate option").evaluate_all("options => options.map(option => option.value)")
    page.locator("#candidate").select_option(next(value for value in options if value != "registration-1"))
    assert post_action(page, "替换当前绑定", "/supersede").status == 201
    expect(page.get_by_test_id("case-status")).to_have_text("已处理")
    page.locator("#restore-target").select_option(first)
    assert post_action(page, "恢复历史版本", "/restore").status == 201
    expect(page.locator("#binding-history li")).to_have_count(3)
    item = load_model_decision_item(browser_server["project_root"], case_id=browser_server["case_id"], principal=AUDITOR)
    assert item["technical"]["binding_history"][0]["restores_binding_id"] == first
    readonly = open_workbench("auditor", professional=True)
    readonly.get_by_role("button", name="全部", exact=False).click()
    readonly.get_by_test_id("decision-row").first.click()
    expect(readonly.locator("#role-status")).to_have_text("审计员 · 只读")
    for button in readonly.locator("[data-action]").all():
        expect(button).to_be_disabled()
    expect(readonly.locator("#matrix td")).to_have_count(16)
    page.screenshot(path=str(browser_server["project_root"] / "professional-workbench.png"), full_page=True)


def test_two_independent_pages_conflict_and_refresh(open_workbench):
    first, second = open_workbench(), open_workbench()
    select_first(first)
    select_first(second)
    for page in (first, second):
        page.get_by_test_id("decision-reason").fill("同一对象核验")
    assert post_action(first, "确认", "/model-matching/decisions").status == 201
    assert post_action(second, "确认", "/model-matching/decisions").status == 409
    expect(second.locator("#message")).to_contain_text("记录已被其他用户处理，请刷新")
    expect(second.get_by_role("button", name="确认", exact=True)).to_be_disabled()
    second.get_by_role("button", name="刷新", exact=True).click()
    expect(second.get_by_test_id("case-status")).to_have_text("已处理")


@pytest.mark.parametrize("browser_server", ["empty"], indirect=True)
def test_empty_and_transport_failure_are_visible(open_workbench):
    page = open_workbench()
    expect(page.locator("#message")).to_contain_text("暂无符合条件")
    page.context.set_offline(True)
    page.get_by_role("button", name="刷新", exact=True).click()
    expect(page.locator("#message")).to_contain_text("请求失败")


@pytest.mark.parametrize("browser_server", ["slow"], indirect=True)
def test_loading_state_prevents_silently_lost_filter_actions(open_workbench):
    page = open_workbench()
    expect(page.locator("#message")).to_have_text("正在加载清单…")
    expect(page.get_by_role("button", name="应用筛选")).to_be_disabled()
    expect(page.get_by_test_id("decision-row")).to_have_count(1)
    expect(page.get_by_role("button", name="应用筛选")).to_be_enabled()
