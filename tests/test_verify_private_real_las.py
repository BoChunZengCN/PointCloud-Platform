"""私有 CI 样本进入转换前的文件门禁。"""

import hashlib
import importlib

import laspy
import pytest


def _verify_fixture():
    return importlib.import_module("scripts.verify_private_real_las").verify_fixture


def _las(tmp_path, *, point_format=2):
    path = tmp_path / "fixture.las"
    cloud = laspy.create(point_format=point_format, file_version="1.2")
    cloud.x = [0.0, 1.0, 2.0]
    cloud.y = [0.0, 1.0, 2.0]
    cloud.z = [0.0, 1.0, 2.0]
    if point_format == 2:
        cloud.red = [10, 20, 30]
        cloud.green = [20, 30, 40]
        cloud.blue = [30, 40, 50]
    cloud.write(path)
    return path, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()


def test_valid_rgb_las_passes_exact_size_hash_and_count(tmp_path):
    path, size, digest = _las(tmp_path)
    assert _verify_fixture()(path, expected_size=size, expected_sha256=digest, expected_points=3) is None


def test_missing_las_is_rejected(tmp_path):
    with pytest.raises((FileNotFoundError, ValueError)):
        _verify_fixture()(
            tmp_path / "missing.las", expected_size=227, expected_sha256="0" * 64, expected_points=1
        )


def test_wrong_size_is_rejected(tmp_path):
    path, size, digest = _las(tmp_path)
    with pytest.raises(ValueError, match="大小"):
        _verify_fixture()(path, expected_size=size + 1, expected_sha256=digest, expected_points=3)


def test_wrong_sha_is_rejected(tmp_path):
    path, size, _digest = _las(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        _verify_fixture()(path, expected_size=size, expected_sha256="0" * 64, expected_points=3)


def test_wrong_point_count_is_rejected(tmp_path):
    path, size, digest = _las(tmp_path)
    with pytest.raises(ValueError, match="点数"):
        _verify_fixture()(path, expected_size=size, expected_sha256=digest, expected_points=4)


def test_non_rgb_las_is_rejected(tmp_path):
    path, size, digest = _las(tmp_path, point_format=0)
    with pytest.raises(ValueError, match="RGB"):
        _verify_fixture()(path, expected_size=size, expected_sha256=digest, expected_points=3)


def test_symlink_is_rejected_when_supported(tmp_path):
    path, size, digest = _las(tmp_path)
    link = tmp_path / "linked.las"
    try:
        link.symlink_to(path)
    except OSError as exc:
        pytest.skip(f"本机不允许创建测试符号链接：{exc}")
    with pytest.raises(ValueError, match="符号链接"):
        _verify_fixture()(link, expected_size=size, expected_sha256=digest, expected_points=3)
