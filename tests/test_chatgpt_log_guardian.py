import importlib.util
import contextlib
import io
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "chatgpt_log_guardian.py"
SPEC = importlib.util.spec_from_file_location("chatgpt_log_guardian", SCRIPT)
guardian = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guardian)


class GuardianTests(unittest.TestCase):
    def setUp(self):
        isolation = patch.object(guardian, "DESKTOP_LOG_ROOTS", (), create=True)
        isolation.start()
        self.addCleanup(isolation.stop)

    def test_backup_supports_special_characters_in_database_path(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-uri"
        source_directory = tmp_path / "source # folder"
        source_directory.mkdir(parents=True, exist_ok=True)
        database = source_directory / "logs.sqlite"
        backup = tmp_path / "codex-log-backup-latest.sqlite"
        try:
            with contextlib.closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE sample (id INTEGER)")
                connection.execute("INSERT INTO sample VALUES (1)")
                connection.commit()
            with patch.multiple(guardian, DB=database, BACKUP=backup):
                self.assertEqual(guardian.make_consistent_backup(), str(backup))
            with contextlib.closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("SELECT id FROM sample").fetchall(), [(1,)])
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_public_defaults_use_current_user_or_codex_home(self):
        for override in (None, str(SCRIPT.parent / "tests" / "custom-home")):
            with self.subTest(codex_home=override):
                environment = dict(os.environ)
                environment.pop("CODEX_HOME", None)
                if override is not None:
                    environment["CODEX_HOME"] = override
                result = subprocess.run(
                    [sys.executable, "-c", "import json, chatgpt_log_guardian as g; print(json.dumps([str(g.DB), str(g.BACKUP)]))"],
                    cwd=SCRIPT.parent, env=environment, capture_output=True, text=True, check=True,
                )
                home = pathlib.Path(override) if override else pathlib.Path.home() / ".codex"
                self.assertEqual(json.loads(result.stdout), [str(home.resolve() / "logs_2.sqlite"), str(SCRIPT.parent / "codex-log-backup-latest.sqlite")])

    def test_main_backs_up_before_pruning_and_preserves_other_tables(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-maintenance"
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = tmp_path / "logs.sqlite"
        backup = tmp_path / "codex-log-backup-latest.sqlite"
        try:
            with contextlib.closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER, ts_nanos INTEGER, estimated_bytes INTEGER)")
                connection.executemany("INSERT INTO logs VALUES (?, ?, 0, ?)", [(1, 1, 10), (2, 2, 20), (3, 3, 30)])
                connection.execute("CREATE TABLE unrelated (value TEXT)")
                connection.execute("INSERT INTO unrelated VALUES ('keep')")
                connection.commit()
            output = io.StringIO()
            with patch.multiple(guardian, DB=database, BACKUP=backup, TARGET_ESTIMATED_BYTES=30), contextlib.redirect_stdout(output):
                result = guardian.main()
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue().splitlines()[0])["status"], "ok")
            self.assertEqual(json.loads(output.getvalue().splitlines()[0])["deleted_rows"], 2)
            with contextlib.closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT id FROM logs ORDER BY id").fetchall(), [(3,)])
                self.assertEqual(connection.execute("SELECT value FROM unrelated").fetchall(), [("keep",)])
            with contextlib.closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT id FROM logs ORDER BY id").fetchall(), [(1,), (2,), (3,)])
            self.assertEqual(list(tmp_path.glob("*.tmp*")), [])
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_backup_timeout_preserves_existing_backup_and_removes_temp_files(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-backup-timeout"
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = tmp_path / "logs.sqlite"
        backup = tmp_path / "codex-log-backup-latest.sqlite"
        try:
            with contextlib.closing(sqlite3.connect(backup)) as connection:
                connection.execute("CREATE TABLE original (id INTEGER)")
                connection.commit()
            original = backup.read_bytes()
            with contextlib.closing(sqlite3.connect(database, check_same_thread=False)) as locker:
                locker.execute("CREATE TABLE sample (id INTEGER)")
                locker.commit()
                locker.execute("BEGIN EXCLUSIVE")
                # Release even with the broken implementation, so the test cannot hang.
                release = threading.Timer(0.8, locker.rollback)
                release.start()
                started = time.monotonic()
                try:
                    with patch.multiple(guardian, DB=database, BACKUP=backup, BACKUP_TIMEOUT_SECONDS=0.1, create=True):
                        with self.assertRaises(TimeoutError):
                            guardian.make_consistent_backup()
                    self.assertLess(time.monotonic() - started, 0.7)
                finally:
                    release.join()
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(guardian.backup_integrity(backup), "ok")
            self.assertEqual(list(tmp_path.glob("*.tmp*")), [])
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_main_reports_busy_checkpoint_instead_of_success(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-checkpoint"
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = tmp_path / "logs.sqlite"
        try:
            with contextlib.closing(sqlite3.connect(database)) as writer, contextlib.closing(sqlite3.connect(database)) as reader:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE logs (id INTEGER, estimated_bytes INTEGER)")
                writer.execute("INSERT INTO logs VALUES (1, 10)")
                writer.commit()
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM logs").fetchall()
                writer.execute("INSERT INTO logs VALUES (2, 10)")
                writer.commit()
                output = io.StringIO()
                with patch.multiple(guardian, DB=database, BACKUP=tmp_path / "backup.sqlite"), contextlib.redirect_stdout(output):
                    result = guardian.main()
                summary = json.loads(output.getvalue().splitlines()[0])
                self.assertEqual(summary["status"], "checkpoint_busy")
                self.assertEqual(summary["checkpoint_status"], "busy")
                self.assertEqual(result, 3)
                self.assertEqual(writer.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 2)
                reader.rollback()
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_main_timeout_reports_failure_without_pruning(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-timeout-no-pruning"
        tmp_path.mkdir(parents=True, exist_ok=True)
        database = tmp_path / "logs.sqlite"
        backup = tmp_path / "codex-log-backup-latest.sqlite"
        try:
            with contextlib.closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE logs (id INTEGER, ts INTEGER, ts_nanos INTEGER, estimated_bytes INTEGER)")
                connection.executemany("INSERT INTO logs VALUES (?, ?, 0, 20)", [(1, 1), (2, 2)])
                connection.commit()
                with contextlib.closing(sqlite3.connect(backup)) as destination:
                    connection.backup(destination)
            original = backup.read_bytes()
            output = io.StringIO()
            with patch.multiple(guardian, DB=database, BACKUP=backup, TARGET_ESTIMATED_BYTES=20, BACKUP_TIMEOUT_SECONDS=0), contextlib.redirect_stdout(output):
                result = guardian.main()
            self.assertEqual(result, 3)
            self.assertEqual(json.loads(output.getvalue().splitlines()[0])["status"], "backup_timeout")
            self.assertEqual(backup.read_bytes(), original)
            with contextlib.closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 2)
            self.assertEqual(list(tmp_path.glob("*.tmp*")), [])
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_cleanup_orphaned_backup_artifacts_keeps_verified_backup(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-cleanup"
        shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True)
        try:
            backup = tmp_path / "codex-log-backup-latest.sqlite"
            backup.write_bytes(b"verified")
            stale = tmp_path / "codex-log-backup-latest.abc.sqlite.tmp"
            stale_journal = tmp_path / "codex-log-backup-latest.abc.sqlite.tmp-journal"
            stale.write_bytes(b"partial")
            stale_journal.write_bytes(b"partial")

            removed = guardian.cleanup_backup_temps(tmp_path, backup)

            self.assertEqual(removed, 2)
            self.assertTrue(backup.exists())
            self.assertFalse(stale.exists())
            self.assertFalse(stale_journal.exists())
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_run_lock_blocks_overlapping_guardian_runs(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-lock"
        shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True)
        try:
            with guardian.run_lock(tmp_path) as first:
                self.assertTrue(first)
                with guardian.run_lock(tmp_path) as second:
                    self.assertFalse(second)
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_repeated_runs_shrink_existing_lock_and_keep_one_byte(self):
        tmp_path = SCRIPT.parent / "tests" / ".guardian-test-lock-size"
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            lock_path = tmp_path / "codex-log-guardian.lock"
            lock_path.write_bytes(b"0" * 622)
            for _ in range(5):
                with guardian.run_lock(tmp_path) as acquired:
                    self.assertTrue(acquired)
                self.assertEqual(lock_path.stat().st_size, 1)
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
