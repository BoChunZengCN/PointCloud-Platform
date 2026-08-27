import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_resource_lock import model_resource_lock


def _run_lock_probe_in_child(project_root, resource_kind, identifier):
    script = """
import json
import sys
from pathlib import Path

from pc_system.model_matching_errors import ModelMatchingError
from pc_system.model_resource_lock import model_resource_lock

try:
    with model_resource_lock(
        Path(sys.argv[1]),
        sys.argv[2],
        sys.argv[3],
        timeout_seconds=0.1,
    ):
        result = {"status": "acquired"}
except ModelMatchingError as exc:
    result = {"code": exc.code}
print(json.dumps(result, sort_keys=True))
"""
    environment = os.environ.copy()
    source_root = Path(__file__).parents[1] / "src"
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (str(source_root), environment.get("PYTHONPATH", ""))
        if item
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(project_root),
            resource_kind,
            identifier,
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
        timeout=5,
    )
    return json.loads(completed.stdout)


def test_model_resource_lock_uses_stable_plain_file(tmp_path):
    with model_resource_lock(tmp_path, "release", "pump-a") as path:
        assert path == (
            tmp_path
            / "reports"
            / "model_matching_resource_locks"
            / "release-1373fa60d698c5e8bf6e679334ef39d51adde60f5d7d0aa0cad21b816e67a986.lock"
        )
        assert path.is_file()
    assert path.is_file()


def test_second_process_times_out_without_replacing_owner(tmp_path):
    with model_resource_lock(tmp_path, "release", "pump-a") as path:
        before = path.stat()
        result = _run_lock_probe_in_child(tmp_path, "release", "pump-a")
        after = path.stat()

    assert result == {"code": "operation_busy"}
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)


def test_model_resource_lock_rejects_invalid_identity_before_creating_root(
    tmp_path,
):
    with pytest.raises(ValueError):
        with model_resource_lock(tmp_path, "../release", "pump-a"):
            pass

    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("nan"), float("inf"), -0.1, True, "0.1"],
)
def test_model_resource_lock_rejects_invalid_timeout_before_filesystem_access(
    tmp_path, timeout_seconds
):
    with pytest.raises(ValueError, match="timeout_seconds"):
        with model_resource_lock(
            tmp_path,
            "release",
            "pump-a",
            timeout_seconds=timeout_seconds,
        ):
            pass

    assert not (tmp_path / "reports").exists()


def test_model_resource_lock_preserves_caller_oserror(tmp_path):
    caller_error = OSError("caller critical section failed")

    with pytest.raises(OSError) as exc_info:
        with model_resource_lock(tmp_path, "release", "pump-a"):
            raise caller_error

    assert exc_info.value is caller_error


def test_model_resource_lock_rejects_linked_lock_root(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    lock_root = reports / "model_matching_resource_locks"
    try:
        lock_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ModelMatchingError):
        with model_resource_lock(tmp_path, "release", "pump-a"):
            pass

    assert list(outside.iterdir()) == []
