"""受控 CI 的私有真实 LAS 前置校验；不输出点云内容。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pc_system.full_preview_source import inspect_las_source


EXPECTED_SIZE = 218_266_379
EXPECTED_SHA256 = "ee1ac0dc4f49f9634d803d4a62313fda903d4d07d08dc86c5bd4afd040bffb69"
EXPECTED_POINTS = 8_394_852


def verify_fixture(
    path: Path, *, expected_size: int, expected_sha256: str, expected_points: int
) -> None:
    """拒绝缺失、替换、截断或不兼容的私有样本。"""

    path = Path(path)
    if path.is_symlink():
        raise ValueError("样本不能是符号链接。")
    if not path.is_file():
        raise FileNotFoundError("样本文件缺失。")
    if path.stat().st_size != expected_size:
        raise ValueError("样本文件大小不符。")
    source = inspect_las_source(path, "private-real-las", declared_unit="m")
    if source.sha256 != expected_sha256.lower():
        raise ValueError("样本 SHA-256 不符。")
    if source.point_count != expected_points:
        raise ValueError("样本点数不符。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验受控 CI 的私有真实 LAS 样本")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_fixture(
            args.path,
            expected_size=EXPECTED_SIZE,
            expected_sha256=EXPECTED_SHA256,
            expected_points=EXPECTED_POINTS,
        )
    except (OSError, ValueError) as exc:
        print(f"样本校验失败：{exc}", file=sys.stderr)
        return 1
    print(f"样本校验通过：{EXPECTED_POINTS:,} 点；SHA-256 匹配。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
