import errno
import hashlib
import json
import math
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pc_system.identifiers import validate_identifier
from pc_system.model_matching_errors import ModelMatchingError

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_LOCK_ROOT_PARTS = ("reports", "model_matching_resource_locks")


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _require_plain_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or _is_link_or_reparse(info):
        raise ModelMatchingError(
            "audit_integrity_error",
            "Model resource lock path is not a plain directory.",
        )


def _lock_path_matches_descriptor(path: Path, descriptor: int) -> bool:
    try:
        path_info = path.lstat()
        opened_info = os.fstat(descriptor)
    except OSError:
        return False
    return (
        stat.S_ISREG(path_info.st_mode)
        and stat.S_ISREG(opened_info.st_mode)
        and not _is_link_or_reparse(path_info)
        and not _is_link_or_reparse(opened_info)
        and (path_info.st_dev, path_info.st_ino)
        == (opened_info.st_dev, opened_info.st_ino)
    )


def _acquire_kernel_byte_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_kernel_byte_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _write_lock_byte(descriptor: int) -> None:
    if os.fstat(descriptor).st_size != 0:
        return
    os.write(descriptor, b"\0")
    os.fsync(descriptor)


def _build_lock_path(
    project_root: Path,
    resource_kind: str,
    identifiers: tuple[str, ...],
) -> Path:
    identity = json.dumps(
        {"identifiers": list(identifiers), "resource_kind": resource_kind},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256(identity).hexdigest()
    return project_root.joinpath(*_LOCK_ROOT_PARTS, f"{resource_kind}-{digest}.lock")


@contextmanager
def model_resource_lock(
    project_root: Path,
    resource_kind: str,
    *identifiers: str,
    timeout_seconds: float = 2.0,
) -> Iterator[Path]:
    """取得永久资源锁文件的内核字节锁，竞争超时则返回 operation_busy。"""

    normalized_kind = validate_identifier(resource_kind, "resource_kind")
    normalized_identifiers = tuple(
        validate_identifier(value, "resource_identifier")
        for value in identifiers
    )
    if (
        type(timeout_seconds) not in {int, float}
        or not math.isfinite(timeout_seconds)
        or timeout_seconds < 0
    ):
        raise ValueError("timeout_seconds must be a finite non-negative number.")
    root = Path(project_root)
    lock_path = _build_lock_path(
        root, normalized_kind, normalized_identifiers
    )
    descriptor: int | None = None
    acquired = False
    yielded = False
    try:
        _require_plain_directory(root)
        reports_root = root / _LOCK_ROOT_PARTS[0]
        reports_root.mkdir(exist_ok=True)
        _require_plain_directory(reports_root)
        lock_root = reports_root / _LOCK_ROOT_PARTS[1]
        lock_root.mkdir(exist_ok=True)
        _require_plain_directory(lock_root)

        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        if not _lock_path_matches_descriptor(lock_path, descriptor):
            raise ModelMatchingError(
                "audit_integrity_error",
                "Model resource lock path is not a plain file.",
            )
        _write_lock_byte(descriptor)

        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                _acquire_kernel_byte_lock(descriptor)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EDEADLK,
                }:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelMatchingError(
                        "operation_busy", "Model resource is busy."
                    ) from exc
                time.sleep(min(0.01, remaining))

        if not _lock_path_matches_descriptor(lock_path, descriptor):
            raise ModelMatchingError(
                "audit_integrity_error",
                "Model resource lock path changed while locked.",
            )
        yielded = True
        yield lock_path
    except ModelMatchingError:
        raise
    except OSError as exc:
        if yielded:
            raise
        raise ModelMatchingError(
            "audit_persistence_error",
            "Model resource lock storage is unavailable.",
        ) from exc
    finally:
        if descriptor is not None:
            if acquired:
                try:
                    _release_kernel_byte_lock(descriptor)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass
