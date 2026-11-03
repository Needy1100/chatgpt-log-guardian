"""Maintain ChatGPT desktop application logs and Codex diagnostic SQLite logs."""

from __future__ import annotations

import json
import contextlib
import ctypes
import hashlib
import msvcrt
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import zipfile
from ctypes import wintypes
from contextlib import closing
from datetime import datetime, timezone


DB = pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex").expanduser().resolve() / "logs_2.sqlite"
BACKUP = pathlib.Path(__file__).resolve().with_name("codex-log-backup-latest.sqlite")
TARGET_ESTIMATED_BYTES = 50 * 1024 * 1024
PHYSICAL_CAP_BYTES = 120 * 1024 * 1024
BACKUP_TIMEOUT_SECONDS = 30
LOCAL_DATA = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local")
DESKTOP_LOG_ROOTS = (
    LOCAL_DATA / "Codex" / "Logs",
    LOCAL_DATA / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0" / "LocalCache" / "Local" / "Codex" / "Logs",
)
DESKTOP_BACKUP = BACKUP.with_name("desktop-log-backup-latest.zip")
DESKTOP_TARGET_BYTES = 50 * 1024 * 1024
DESKTOP_KEEP_DAYS = 7


def open_inactive_log(path: pathlib.Path):
    """Deny writers while reading/deleting; an existing writer makes this fail."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 1 | 4, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")


def maintain_desktop_logs() -> dict:
    for artifact in DESKTOP_BACKUP.parent.glob(DESKTOP_BACKUP.stem + ".*.zip.tmp"):
        artifact.unlink()
    today = datetime.now(timezone.utc).date()
    files, seen = [], set()
    for root in DESKTOP_LOG_ROOTS:
        for path in root.glob("*/*/*/codex-desktop-*.log"):
            relative = path.relative_to(root)
            try:
                day = datetime.strptime(relative.parent.as_posix(), "%Y/%m/%d").date()
                # Never follow a junction/symlink into browser storage or another directory.
                if any(getattr(p.lstat(), "st_file_attributes", 0) & 0x400
                       for p in (root, path, *list(path.parents)[:3])):
                    continue
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if not path.is_file() or identity in seen:
                    continue
                seen.add(identity)
                files.append((stat.st_mtime, stat.st_size, path, day, relative))
            except (OSError, ValueError):
                continue
    total = sum(item[1] for item in files)
    remaining = total
    candidates = []
    for item in sorted(files, key=lambda item: item[0]):
        mtime, size, path, day, relative = item
        if day >= today or time.time() - mtime < 86400:
            continue
        if (today - day).days > DESKTOP_KEEP_DAYS or remaining > DESKTOP_TARGET_BYTES:
            candidates.append(item)
            remaining -= size
    deleted = skipped = 0
    backup = None
    if candidates:
        deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
        fd, name = tempfile.mkstemp(prefix=DESKTOP_BACKUP.stem + ".", suffix=".zip.tmp", dir=DESKTOP_BACKUP.parent)
        os.close(fd)
        temporary = pathlib.Path(name)
        snapshots = []
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for _, _, path, _, relative in candidates:
                    try:
                        source = open_inactive_log(path)
                    except OSError:
                        skipped += 1
                        continue
                    with source, archive.open(str(len(snapshots)) + "/" + relative.as_posix(), "w") as destination:
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            if time.monotonic() >= deadline:
                                raise TimeoutError("desktop backup deadline exceeded")
                            destination.write(chunk)
                            digest.update(chunk)
                        snapshots.append((path, digest.digest()))
            if snapshots:
                with zipfile.ZipFile(temporary) as verify:
                    if verify.testzip() is not None:
                        raise RuntimeError("desktop backup integrity check failed")
                if time.monotonic() >= deadline:
                    raise TimeoutError("desktop backup deadline exceeded")
                os.replace(temporary, DESKTOP_BACKUP)
                backup = str(DESKTOP_BACKUP)
                for path, expected in snapshots:
                    try:
                        with open_inactive_log(path) as source:
                            digest = hashlib.sha256()
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                digest.update(chunk)
                            if digest.digest() != expected:
                                skipped += 1
                                continue
                            path.unlink()
                            deleted += 1
                    except OSError:
                        skipped += 1
        finally:
            temporary.unlink(missing_ok=True)
    after = sum(file_bytes(item[2]) for item in files)
    return {"component": "desktop_logs", "status": "busy" if skipped else "ok",
            "files": len(files), "before_bytes": total, "after_bytes": after,
            "deleted_files": deleted, "skipped_files": skipped, "backup": backup}


def stats(connection: sqlite3.Connection) -> tuple[int, int]:
    return connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(estimated_bytes), 0) FROM logs"
    ).fetchone()


def file_bytes(path: pathlib.Path) -> int:
    return path.stat().st_size if path.exists() else 0


@contextlib.contextmanager
def run_lock(directory: pathlib.Path):
    """Hold a one-byte Windows lock so scheduled runs cannot overlap."""
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "codex-log-guardian.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_BINARY)
    with os.fdopen(fd, "r+b") as handle:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            handle.write(b"0")
            handle.truncate(1)
            handle.flush()
            yield True
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def cleanup_orphaned_backup_artifacts(directory: pathlib.Path, backup: pathlib.Path) -> int:
    """Remove only this guardian's leftover temp/journal files."""
    removed = 0
    pattern = backup.stem + ".*.sqlite.tmp*"
    for candidate in directory.glob(pattern):
        if candidate.is_file():
            candidate.unlink()
            removed += 1
    return removed


def backup_integrity(path: pathlib.Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]


def make_consistent_backup() -> str:
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS

    def check_deadline(*_):
        if time.monotonic() >= deadline:
            raise TimeoutError("backup deadline exceeded")

    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=BACKUP.stem + ".", suffix=".sqlite.tmp", dir=BACKUP.parent
    )
    os.close(fd)
    temp_path = pathlib.Path(temp_name)
    try:
        source_uri = DB.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=0.1)) as source:
            with closing(sqlite3.connect(temp_path)) as destination:
                source.backup(destination, pages=128, progress=check_deadline, sleep=0.1)
            check_deadline()
            if backup_integrity(temp_path) != "ok":
                raise RuntimeError("backup integrity check failed")
        check_deadline()
        os.replace(temp_path, BACKUP)
        return str(BACKUP)
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            artifact = temp_path.with_name(temp_path.name + suffix)
            if artifact.exists():
                artifact.unlink()


def _run_once() -> int:
    if not DB.exists():
        print(json.dumps({"status": "missing_database"}))
        return 0

    before_db = file_bytes(DB)
    before_wal = file_bytes(DB.with_name(DB.name + "-wal"))
    backup_path = None
    deleted = 0
    removed_artifacts = 0

    try:
        with closing(sqlite3.connect(DB, timeout=10)) as connection:
            connection.execute("PRAGMA busy_timeout=10000")
            before_rows, before_estimated = stats(connection)
            physical_before = before_db + before_wal

            if BACKUP.exists() and backup_integrity(BACKUP) == "ok":
                removed_artifacts = cleanup_orphaned_backup_artifacts(BACKUP.parent, BACKUP)

            if before_estimated > TARGET_ESTIMATED_BYTES or physical_before > PHYSICAL_CAP_BYTES:
                backup_path = make_consistent_backup()
                removed_artifacts = cleanup_orphaned_backup_artifacts(BACKUP.parent, BACKUP)

            if before_estimated > TARGET_ESTIMATED_BYTES:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    WITH ranked AS (
                        SELECT id, SUM(estimated_bytes) OVER (
                            ORDER BY ts DESC, ts_nanos DESC, id DESC
                        ) AS kept_bytes
                        FROM logs
                    )
                    DELETE FROM logs WHERE id IN (
                        SELECT id FROM ranked WHERE kept_bytes > ?
                    )
                    """,
                    (TARGET_ESTIMATED_BYTES,),
                )
                deleted = connection.execute("SELECT changes()").fetchone()[0]
                connection.commit()

            if deleted or physical_before > PHYSICAL_CAP_BYTES:
                connection.execute("VACUUM")

            checkpoint_busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            after_rows, after_estimated = stats(connection)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

        result = {
            "status": "checkpoint_busy" if checkpoint_busy else "ok",
            "checkpoint_status": "busy" if checkpoint_busy else "ok",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "before_rows": before_rows,
            "after_rows": after_rows,
            "deleted_rows": deleted,
            "before_estimated_bytes": before_estimated,
            "after_estimated_bytes": after_estimated,
            "database_bytes": file_bytes(DB),
            "wal_bytes": file_bytes(DB.with_name(DB.name + "-wal")),
            "integrity": integrity,
            "backup": backup_path,
            "removed_orphaned_backup_artifacts": removed_artifacts,
        }
        print(json.dumps(result, ensure_ascii=True))
        return 2 if integrity != "ok" else (3 if checkpoint_busy else 0)
    except (sqlite3.OperationalError, TimeoutError) as error:
        if BACKUP.exists():
            try:
                if backup_integrity(BACKUP) == "ok":
                    removed_artifacts = cleanup_orphaned_backup_artifacts(BACKUP.parent, BACKUP)
            except sqlite3.Error:
                pass
        print(json.dumps({
            "status": "backup_timeout" if isinstance(error, TimeoutError) else "busy_or_unavailable",
            "error_type": type(error).__name__,
            "removed_orphaned_backup_artifacts": removed_artifacts,
        }))
        return 3


def main() -> int:
    with run_lock(BACKUP.parent) as acquired:
        if not acquired:
            print(json.dumps({"status": "already_running"}))
            return 0
        database_result = _run_once()
        try:
            desktop = maintain_desktop_logs()
            print(json.dumps(desktop, ensure_ascii=True))
            return database_result or (3 if desktop["status"] == "busy" else 0)
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            print(json.dumps({"component": "desktop_logs", "status": "failed", "error_type": type(error).__name__}))
            return database_result or 3


if __name__ == "__main__":
    sys.exit(main())
