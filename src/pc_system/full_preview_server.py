"""仅供本机使用的完整点云只读服务和单段 HTTP Range。"""

from __future__ import annotations

import json
import mimetypes
import re
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pc_system.full_preview_publication import get_active_preview, list_preview_versions
from pc_system.full_preview_dependencies import verify_viewer_bundle
from pc_system.identifiers import validate_identifier

_RANGE = re.compile(r"^bytes=(\d+)-(\d+)$")
_MAX_RANGE = 64 * 1024 * 1024
_MAX_STATIC = 64 * 1024 * 1024
_REPARSE_POINT = 0x400


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _safe_file(root: Path, parts: list[str]) -> Path | None:
    if _reparse(root) or not root.is_dir():
        return None
    target = root
    for part in parts:
        if part in ("", ".", "..") or "\\" in part or ":" in part or "\x00" in part:
            return None
        target = target / part
        if _reparse(target):
            return None
    if not target.is_file():
        return None
    resolved_root = root.resolve()
    if not target.resolve().is_relative_to(resolved_root):
        return None
    return target


def _safe_root(path: Path) -> Path:
    original = Path(path).absolute()
    if any(_reparse(item) for item in (original, *original.parents)):
        raise ValueError("静态目录不可经过符号链接或重解析点。")
    return original.resolve(strict=True)


def create_preview_server(
    project_root: Path,
    frontend_root: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    viewer_root: Path | None = None,
) -> ThreadingHTTPServer:
    """固定路径服务；没有通用文件下载端点，也不对外网绑定。"""

    if host != "127.0.0.1":
        raise ValueError("完整预览服务仅允许绑定 127.0.0.1。")
    project_root = _safe_root(project_root)
    frontend_root = _safe_root(frontend_root)
    if not project_root.is_dir() or not frontend_root.is_dir():
        raise ValueError("项目或前端静态目录不存在。")
    viewer_root = _safe_root(viewer_root) if viewer_root is not None else None
    if viewer_root is not None and not viewer_root.is_dir():
        raise ValueError("Potree 查看器静态目录不存在。")
    if viewer_root is not None:
        verify_viewer_bundle(viewer_root)

    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            # 不将本机预览请求和路径写到控制台。
            return

        def _respond(self, status: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _serve_file(self, path: Path, *, max_size: int = _MAX_STATIC):
            size = path.stat().st_size
            if size > max_size:
                self._respond(413, b"resource too large")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return

        def _range_error(self, size: int):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _serve_range(self, path: Path):
            size = path.stat().st_size
            match = _RANGE.fullmatch(self.headers.get("Range", ""))
            if match is None:
                self._range_error(size)
                return
            start, end = int(match.group(1)), int(match.group(2))
            if start > end or end >= size or end - start + 1 > _MAX_RANGE:
                self._range_error(size)
                return
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    remaining -= len(chunk)

        def _catalog(self):
            previews_root = project_root / "workspace" / "previews"
            previews = []
            if previews_root.is_dir() and not _reparse(previews_root):
                for asset in previews_root.iterdir():
                    if not asset.is_dir() or _reparse(asset):
                        continue
                    try:
                        active = get_active_preview(project_root, asset.name)
                    except (ValueError, OSError):
                        continue
                    if active is None:
                        continue
                    manifest = active.manifest
                    previews.append({
                        "asset_id": active.asset_id,
                        "run_id": active.run_id,
                        "metadata_url": active.metadata_url,
                        "source_name": manifest.get("source_name"),
                        "source_point_count": manifest.get("source_point_count"),
                        "source_unit": manifest.get("source_unit"),
                        "rgb_available": manifest.get("rgb_available"),
                        "viewer_format": manifest.get("viewer_format"),
                    })
            body = json.dumps({"previews": sorted(previews, key=lambda item: item["asset_id"])}, ensure_ascii=False).encode("utf-8")
            self._respond(200, body, "application/json; charset=utf-8")

        def do_GET(self):
            expected_hosts = {"127.0.0.1", f"127.0.0.1:{self.server.server_port}"}
            if self.headers.get("Host") not in expected_hosts:
                self._respond(403, b"forbidden host")
                return
            path = unquote(urlsplit(self.path).path)
            if "\\" in path or "\x00" in path:
                self._respond(400, b"invalid path")
                return
            if path == "/api/full-previews":
                self._catalog()
                return
            if path == "/":
                path = "/frontend/viewer.html"
            parts = path.split("/")[1:]
            if any(part in ("", ".", "..") for part in parts):
                self._respond(400, b"invalid path")
                return
            if len(parts) == 5 and parts[0] == "previews" and parts[2] == "runs":
                _, asset_id, _, run_id, name = parts
                try:
                    validate_identifier(asset_id, "asset_id")
                    validate_identifier(run_id, "run_id")
                except ValueError:
                    self._respond(404, b"not found")
                    return
                if name not in ("metadata.json", "hierarchy.bin", "octree.bin"):
                    self._respond(404, b"not found")
                    return
                versions = list_preview_versions(project_root, asset_id)
                published = next((item for item in versions if item.run_id == run_id), None)
                if published is None:
                    self._respond(404, b"not found")
                    return
                file = _safe_file(published.run_dir, [name])
                if file is None:
                    self._respond(404, b"not found")
                elif name == "metadata.json":
                    self._serve_file(file, max_size=1024 * 1024)
                else:
                    self._serve_range(file)
                return
            if len(parts) >= 2 and parts[0] == "frontend":
                file = _safe_file(frontend_root, parts[1:])
            elif len(parts) >= 3 and parts[:2] == ["vendor", "potree"] and viewer_root is not None:
                file = _safe_file(viewer_root, parts[2:])
            else:
                file = None
            if file is None:
                self._respond(404, b"not found")
            else:
                self._serve_file(file)

    server = ThreadingHTTPServer((host, port), PreviewHandler)
    server.daemon_threads = True
    return server
