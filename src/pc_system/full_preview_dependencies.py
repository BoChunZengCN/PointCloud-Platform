"""固定的本机 Potree 转换器与查看器兼容依赖。"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

CONVERTER_VERSION = "2.1.1"
CONVERTER_SHA256 = "117dab69b06c99a01367f178019ce8f094a9faa2ecffcbaca7b6c554313c0a1e"
VIEWER_VERSION = "1.8.2"
VIEWER_SHA256 = "e503483823e30ff5398382840343fc46bbc3df604d7d589ee4508a014c38ca4c"


def _sha256_regular_file(path: Path, label: str) -> str:
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label}文件缺失。") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError(f"{label}必须是普通文件。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_converter_binary(path: Path) -> str:
    fingerprint = _sha256_regular_file(path, "转换器")
    if fingerprint != CONVERTER_SHA256:
        raise ValueError(f"转换器版本或指纹不符；需要 PotreeConverter {CONVERTER_VERSION} 的固定发布文件。")
    return fingerprint


def verify_viewer_bundle(root: Path) -> str:
    fingerprint = _sha256_regular_file(Path(root) / "build/potree/potree.js", "查看器")
    if fingerprint != VIEWER_SHA256:
        raise ValueError(f"查看器版本或指纹不符；需要 Potree {VIEWER_VERSION} 的固定发布文件。")
    return fingerprint
