"""真实生产模式 API 与静态工作台的本地浏览器验收夹具。"""

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect

from pc_system.api import create_app
from phase15c_support import DeterministicRegistrationEngine
from phase15d_support import prepare_decision_case


expect.set_options(timeout=15000)


@pytest.fixture
def browser_server(tmp_path, request):
    mode = getattr(request, "param", "passed")
    case = None if mode == "empty" else prepare_decision_case(tmp_path, mode="passed" if mode == "slow" else mode)
    app = create_app(tmp_path, run_mode="production", api_key="phase15d-test-service-key",
        principal_bindings={role + "-token": {"actor_id": role + "-browser", "roles": [role]}
                            for role in ("operator", "expert", "auditor")},
        registration_engine_resolver=lambda _: DeterministicRegistrationEngine("passed"))
    if mode == "slow":
        @app.middleware("http")
        async def latency(request, call_next):
            if request.url.path == "/model-matching/decision-items":
                await asyncio.sleep(1)
            return await call_next(request)
    app.mount("/workbench", StaticFiles(directory=Path(__file__).resolve().parents[2] / "frontend"), name="workbench")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    errors = []
    def run():
        try:
            server.run(sockets=[sock])
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started and not errors, f"HTTP 服务启动失败：{errors}"
        yield {"url":f"http://127.0.0.1:{sock.getsockname()[1]}", "project_root":tmp_path,
               "case_id":None if case is None else case.request_fields["case_id"], "case":case}
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "HTTP 测试服务未按时停止"
        assert not errors, f"HTTP 测试服务异常：{errors}"


@pytest.fixture
def open_workbench(new_context, browser_server):
    contexts, errors = [], []
    def open_page(role="operator", professional=False):
        context = new_context(extra_http_headers={"Authorization":"Bearer " + role + "-token"}, viewport={"width":1440,"height":1050})
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(browser_server["url"] + "/workbench/" + ("model-matching-lab.html" if professional else "model-decisions.html"))
        return page
    yield open_page
    for context in contexts:
        context.close()
    assert errors == [], f"页面 JavaScript 异常：{errors}"
