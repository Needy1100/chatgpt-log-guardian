import contextlib
import io
import json
import os
import pathlib
import shutil
import time
import unittest
import uuid
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch

from test_chatgpt_log_guardian import guardian


class DesktopLogTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(__file__).parent.resolve() / (".guardian-desktop-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.logs = self.root / "Logs"
        self.backup = self.root / "desktop-log-backup-latest.zip"
        settings = patch.multiple(guardian, DESKTOP_LOG_ROOTS=(self.logs,), DESKTOP_BACKUP=self.backup, create=True)
        settings.start()
        self.addCleanup(settings.stop)

    def log(self, name, age):
        stamp = time.time() - age * 86400
        date = datetime.fromtimestamp(stamp, timezone.utc)
        path = self.logs / date.strftime("%Y/%m/%d") / ("codex-desktop-" + name + ".log")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"diagnostic")
        os.utime(path, (stamp, stamp))
        return path

    def test_old_log_is_backed_up_and_recent_and_browser_data_are_kept(self):
        old = self.log("old", 9)
        recent = self.log("recent", 1)
        browser = self.logs / "Local Storage" / "leveldb" / "000003.log"
        browser.parent.mkdir(parents=True)
        browser.write_bytes(b"keep")
        summary = guardian.maintain_desktop_logs()
        self.assertEqual(summary["deleted_files"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(browser.read_bytes(), b"keep")
        with zipfile.ZipFile(self.backup) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read(archive.namelist()[0]), b"diagnostic")

    def test_budget_removes_oldest_first(self):
        oldest = self.log("oldest", 3)
        middle = self.log("middle", 2)
        newest = self.log("newest", 1)
        with patch.object(guardian, "DESKTOP_TARGET_BYTES", 15, create=True):
            summary = guardian.maintain_desktop_logs()
        self.assertEqual(summary["deleted_files"], 2)
        self.assertFalse(oldest.exists())
        self.assertFalse(middle.exists())
        self.assertTrue(newest.exists())

    def test_today_and_open_writer_are_skipped(self):
        today = self.log("today", 0)
        old = self.log("writer", 9)
        with old.open("ab") as writer, patch.object(guardian, "DESKTOP_TARGET_BYTES", 0, create=True):
            summary = guardian.maintain_desktop_logs()
            self.assertEqual(summary["deleted_files"], 0)
            self.assertGreaterEqual(summary["skipped_files"], 1)
            writer.write(b"still writable")
        self.assertTrue(old.exists())
        self.assertTrue(today.exists())

    def test_archive_failure_keeps_source_and_existing_backup(self):
        old = self.log("old", 9)
        with zipfile.ZipFile(self.backup, "w") as archive:
            archive.writestr("previous.log", b"previous")
        previous = self.backup.read_bytes()
        with patch.object(guardian.zipfile.ZipFile, "testzip", return_value="bad-entry"):
            with self.assertRaises(RuntimeError):
                guardian.maintain_desktop_logs()
        self.assertTrue(old.exists())
        self.assertEqual(self.backup.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_shared_root_is_not_counted_twice(self):
        self.log("recent", 1)
        with patch.object(guardian, "DESKTOP_LOG_ROOTS", (self.logs, self.logs)):
            summary = guardian.maintain_desktop_logs()
        self.assertEqual(summary["before_bytes"], 10)

    def test_log_changed_after_backup_is_not_deleted(self):
        old = self.log("changed", 9)
        replace = os.replace

        def publish_then_change(source, destination):
            replace(source, destination)
            old.write_bytes(b"new diagnostic data")

        with patch.object(guardian.os, "replace", side_effect=publish_then_change):
            summary = guardian.maintain_desktop_logs()
        self.assertEqual(summary["deleted_files"], 0)
        self.assertEqual(summary["skipped_files"], 1)
        self.assertEqual(old.read_bytes(), b"new diagnostic data")

    def test_orphaned_temp_archive_is_removed(self):
        orphan = self.root / "desktop-log-backup-latest.previous.zip.tmp"
        orphan.write_bytes(b"partial")
        guardian.maintain_desktop_logs()
        self.assertFalse(orphan.exists())

    def test_main_still_maintains_desktop_logs_when_sqlite_is_missing(self):
        old = self.log("old", 9)
        output = io.StringIO()
        with patch.multiple(guardian, DB=self.root / "missing.sqlite", BACKUP=self.root / "backup.sqlite"), contextlib.redirect_stdout(output):
            result = guardian.main()
        self.assertEqual(result, 0)
        self.assertFalse(old.exists())
        summaries = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(any(item.get("component") == "desktop_logs" for item in summaries))
