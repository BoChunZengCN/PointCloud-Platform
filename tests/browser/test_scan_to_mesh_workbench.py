"""点云转网格专家工作台的真实 Chromium 验收。"""

import json

import pytest
from playwright.sync_api import expect


def _open(factory, role="expert"):
    page = factory()
    expect(page.get_by_role("heading", name="点云转网格工作台")).to_be_visible()
    page.locator("details.connection summary").click()
    page.locator("#token").fill(role + "-token")
    page.get_by_role("button", name="连接", exact=True).click()
    expect(page.locator("#role-note")).not_to_contain_text("尚未连接")
    return page


def _fill_run(page, *, run_id="browser-mesh-1", source_kind="phase14_correction_release"):
    page.locator("#run-id").fill(run_id)
    page.locator("#source-kind").select_option(source_kind)
    if source_kind == "phase14_correction_release":
        page.locator("#asset-id").fill("scan-browser")
        page.locator("#source-release-id").fill("correction-release-v1")
        page.locator("#instance-id").fill("pipe-1")
    else:
        page.locator("#model-id").fill("scan-pipe")
        page.locator("#version-id").fill("v1")
    page.locator("#config-id").select_option("mesh-poisson-v1")
    page.locator("#policy-id").select_option("mesh-quality-v1")


@pytest.mark.parametrize("browser_server", ["mesh-review"], indirect=True)
def test_phase14_review_required_can_be_confirmed_and_published_with_bounded_overlay(
    open_scan_to_mesh, browser_server
):
    page = _open(open_scan_to_mesh)
    page.on("dialog", lambda dialog: dialog.accept())
    _fill_run(page)
    page.get_by_role("button", name="创建网格运行", exact=True).click()

    expect(page.locator("#detail-title")).to_contain_text("browser-mesh-1")
    expect(page.locator("#quality-summary")).to_contain_text("需要专家复核")
    expect(page.locator("#mesh-preview")).to_have_attribute("data-rendered", "true")
    expect(page.locator("#preview-legend")).to_contain_text("来源点云")
    expect(page.locator("#preview-legend")).to_contain_text("原始网格")
    expect(page.locator("#preview-legend")).to_contain_text("修复网格")
    risk = page.locator('input[data-risk-code="point_coverage_review"]')
    expect(risk).to_have_count(1)
    expect(page.get_by_role("button", name="批准网格", exact=True)).to_be_disabled()
    risk.check()
    page.locator("#decision-reason").fill("已核对覆盖率风险与叠加预览")
    expect(page.get_by_role("button", name="批准网格", exact=True)).to_be_enabled()
    page.get_by_role("button", name="批准网格", exact=True).click()
    expect(page.locator("#decision-summary")).to_contain_text("已批准")

    page.locator("#release-id").fill("browser-mesh-release-1")
    page.locator("#release-reason").fill("发布已复核网格")
    page.get_by_role("button", name="发布网格", exact=True).click()
    expect(page.locator("#publication-summary")).to_contain_text("当前发布")
    assert (
        browser_server["project_root"]
        / "scan_to_cad"
        / "mesh_objects"
    ).is_dir()


@pytest.mark.parametrize("browser_server", ["mesh-auto"], indirect=True)
def test_phase15e_passed_policy_auto_publishes_and_operator_sees_business_summary(
    open_scan_to_mesh,
):
    expert = _open(open_scan_to_mesh)
    expert.on("dialog", lambda dialog: dialog.accept())
    _fill_run(
        expert,
        run_id="browser-auto-1",
        source_kind="phase15e_scanned_reference",
    )
    expert.get_by_role("button", name="创建网格运行", exact=True).click()

    expect(expert.locator("#quality-summary")).to_contain_text("门禁通过")
    expect(expert.locator("#decision-summary")).to_contain_text("系统自动批准")
    expect(expert.locator("#publication-summary")).to_contain_text("当前发布")
    expect(expert.get_by_role("button", name="批准网格", exact=True)).to_be_hidden()

    operator = _open(open_scan_to_mesh, "operator")
    expect(operator.locator("#role-note")).to_contain_text("业务角色")
    expect(operator.locator("#run-list button")).to_have_count(1)
    operator.locator("#run-list button").click()
    expect(operator.locator("#business-summary")).to_contain_text("已发布")
    expect(operator.locator("#professional-detail")).to_be_hidden()
    assert "worker" not in operator.content().lower()
    assert ".ply" not in operator.content().lower()


@pytest.mark.parametrize("browser_server", ["mesh-rejected"], indirect=True)
def test_rejected_run_has_no_approve_or_release_bypass(open_scan_to_mesh):
    page = _open(open_scan_to_mesh)
    page.on("dialog", lambda dialog: dialog.accept())
    _fill_run(page, run_id="browser-rejected-1")
    page.get_by_role("button", name="创建网格运行", exact=True).click()

    expect(page.locator("#quality-summary")).to_contain_text("未通过硬门禁")
    expect(page.get_by_role("button", name="批准网格", exact=True)).to_be_hidden()
    expect(page.get_by_role("button", name="发布网格", exact=True)).to_be_disabled()
    page.locator("#decision-reason").fill("覆盖率低于硬门禁")
    page.get_by_role("button", name="驳回网格", exact=True).click()
    expect(page.locator("#decision-summary")).to_contain_text("已驳回")


@pytest.mark.parametrize("browser_server", ["mesh-review"], indirect=True)
def test_pending_processed_filters_and_uncertain_create_retry_reuse_exact_envelope(
    open_scan_to_mesh,
):
    page = _open(open_scan_to_mesh)
    page.on("dialog", lambda dialog: dialog.accept())
    _fill_run(page, run_id="browser-retry-1")
    captured = []

    def lose_first_response(route, request):
        if request.method != "POST":
            route.continue_()
            return
        captured.append(json.loads(request.post_data))
        if len(captured) == 1:
            response = route.fetch()
            assert response.status == 200
            route.abort()
        else:
            route.continue_()

    page.route("**/scan-to-mesh/runs", lose_first_response)
    page.get_by_role("button", name="创建网格运行", exact=True).click()
    expect(page.locator("#retry")).to_be_visible()
    expect(page.get_by_role("button", name="创建网格运行", exact=True)).to_be_disabled()
    page.get_by_role("button", name="重试原操作", exact=True).click()
    expect(page.locator("#detail-title")).to_contain_text("browser-retry-1")
    assert captured[0] == captured[1]

    page.get_by_role("button", name="待处理", exact=True).click()
    expect(page.locator("#run-list button")).to_have_count(1)
    page.locator('input[data-risk-code="point_coverage_review"]').check()
    page.locator("#decision-reason").fill("确认覆盖率风险")
    page.get_by_role("button", name="批准网格", exact=True).click()
    page.get_by_role("button", name="已处理", exact=True).click()
    expect(page.locator("#run-list button")).to_have_count(1)


@pytest.mark.parametrize("browser_server", ["mesh-slow"], indirect=True)
def test_active_create_disables_duplicate_connection_filters_and_writes(
    open_scan_to_mesh,
):
    page = _open(open_scan_to_mesh)
    page.on("dialog", lambda dialog: dialog.accept())
    _fill_run(page, run_id="browser-slow-1")
    page.get_by_role("button", name="创建网格运行", exact=True).click()

    expect(page.locator("#message")).to_contain_text("正在提交")
    expect(page.get_by_role("button", name="创建网格运行", exact=True)).to_be_disabled()
    expect(page.locator("#connect")).to_be_disabled()
    for button in page.locator("[data-status]").all():
        expect(button).to_be_disabled()
    expect(page.locator("#detail-title")).to_contain_text("browser-slow-1")


@pytest.mark.parametrize("browser_server", ["mesh-review"], indirect=True)
def test_auditor_is_readonly_and_professional_page_leaks_no_paths_or_worker_logs(
    open_scan_to_mesh,
):
    expert = _open(open_scan_to_mesh)
    expert.on("dialog", lambda dialog: dialog.accept())
    _fill_run(expert, run_id="browser-audit-1")
    expert.get_by_role("button", name="创建网格运行", exact=True).click()
    expect(expert.locator("#detail-title")).to_contain_text("browser-audit-1")

    auditor = _open(open_scan_to_mesh, "auditor")
    expect(auditor.locator("#role-note")).to_contain_text("审计员")
    auditor.locator("#run-list button").click()
    expect(auditor.locator("#audit-summary")).to_contain_text("mesh-quality-v1")
    for button in auditor.locator("[data-write]").all():
        expect(button).to_be_disabled()
    content = auditor.content().lower()
    assert "raw_mesh.ply" not in content
    assert "preview_mesh.glb" not in content
    assert "stderr" not in content
    assert "d:\\" not in content
    assert auditor.evaluate(
        "Object.keys(localStorage).length + Object.keys(sessionStorage).length"
    ) == 0
