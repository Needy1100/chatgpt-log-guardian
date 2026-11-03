"""Maintain ChatGPT's local Codex diagnostic logs without printing their contents."""

from __future__ import annotations

import json
import contextlib
import msvcrt
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone


DB = pathlib.Path(os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex").expanduser().resolve() / "logs_2.sqlite"
BACKUP = pathlib.Path(__file__).resolve().with_name("codex-log-backup-latest.sqlite")
TARGET_ESTIMATED_BYTES = 50 * 1024 * 1024
PHYSICAL_CAP_BYTES = 120 * 1024 * 1024
BACKUP_TIMEOUT_SECONDS = 30


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
            with closing(sqlite3.connect(temp_path)) as verify:
                if verify.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
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
                        SELECT id,
                               SUM(estimated_bytes) OVER (
                                   ORDER BY ts DESC, ts_nanos DESC, id DESC
                               ) AS kept_bytes
                        FROM logs
                    )
                    DELETE FROM logs
                    WHERE id IN (SELECT id FROM ranked WHERE kept_bytes > ?)
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
        return _run_once()


if __name__ == "__main__":
    sys.exit(main())
