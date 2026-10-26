"""完整 LAS 分层预览的本机 CLI 薄适配层。"""

import json
from pathlib import Path

from pc_system.full_preview_publication import (
    activate_preview_version,
    list_preview_versions,
    prepare_full_preview,
)
from pc_system.full_preview_server import create_preview_server


def _display(result) -> dict:
    return {
        "asset_id": result.asset_id,
        "run_id": result.run_id,
        "metadata_url": result.metadata_url,
    }


def run_prepare_full_preview(
    project_root: Path, source_path: Path, asset_id: str, converter_path: Path
) -> int:
    result = prepare_full_preview(project_root, source_path, asset_id, converter_path)
    print(json.dumps(_display(result), ensure_ascii=False))
    return 0


def run_list_full_preview_versions(project_root: Path, asset_id: str) -> int:
    versions = list_preview_versions(project_root, asset_id)
    print(json.dumps({"asset_id": asset_id, "versions": [_display(item) for item in versions]}, ensure_ascii=False))
    return 0


def run_activate_full_preview_version(project_root: Path, asset_id: str, run_id: str) -> int:
    result = activate_preview_version(project_root, asset_id, run_id)
    print(json.dumps(_display(result), ensure_ascii=False))
    return 0


def run_serve_full_preview(
    project_root: Path, frontend_root: Path, viewer_root: Path, port: int
) -> int:
    server = create_preview_server(
        project_root, frontend_root, "127.0.0.1", port, viewer_root=viewer_root
    )
    try:
        print(f"完整点云本机预览：http://127.0.0.1:{server.server_port}/frontend/viewer.html", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
    return 0
