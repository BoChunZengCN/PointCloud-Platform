"""只用于验证部署入口确实调用固定子进程与受控请求文件。"""

import json
import os
import sys
from pathlib import Path

from pc_system.mesh_worker_contract import load_mesh_worker_request
from test_mesh_reconstruction import FakeWorker


if __name__ == "__main__":
    if sys.argv[1:] != ["--request", "worker_request.json"]:
        raise SystemExit(2)
    workspace = Path.cwd()
    request = load_mesh_worker_request(workspace)
    trace = Path(os.environ["PC_SYSTEM_MESH_STUB_TRACE"])
    trace.write_text(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "cwd": str(workspace),
                "algorithm": request["algorithm"],
                "script": str(Path(__file__).resolve()),
            }
        ),
        encoding="utf-8",
    )
    FakeWorker()(request, workspace)
