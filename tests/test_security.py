"""Independent adversarial checks of the public Engine contract.

All test files and journals live under a disposable temporary directory. These
tests never inspect the user's files, credentials, or real MCP configuration.
"""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from undoweave.engine import Engine, UndoWeaveError


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="undoweave-security-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "private-state"
        self.engine = Engine(self.root, self.state)

    def put(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        return target

    def record(self, path="file.txt", before="one\ntwo\n", after="ONE\ntwo\n"):
        self.put(path, before)
        info = self.engine.read(path)
        return self.engine.edit(path, after, "agent-a", info["sha256"])

    def rejected_read_and_write(self, path):
        with self.assertRaises(UndoWeaveError, msg=f"read accepted {path!r}"):
            self.engine.read(path)
        with self.assertRaises(UndoWeaveError, msg=f"write accepted {path!r}"):
            self.engine.edit(path, "replacement", "agent-a", "missing")

    def test_escape_absolute_and_windows_special_paths_are_rejected(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside stays", encoding="utf-8")
        for path in (
            "../outside.txt", "nested/../../outside.txt", "..\\outside.txt",
            str(outside), "/outside.txt", "C:/outside.txt", "C:\\outside.txt",
            "\\\\server\\share\\outside.txt", "\\\\?\\C:\\outside.txt",
            "file.txt:stream", "file.txt::$DATA", "CON", "NUL.txt", "COM1",
            "LPT9.txt", "sub/PRN.txt", "CONIN$", "CONOUT$", "COM¹.txt",
            "LPT²", "COM³", "trailing.", "trailing ",
        ):
            with self.subTest(path=path):
                self.rejected_read_and_write(path)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside stays")

    def test_secret_and_repository_metadata_paths_are_rejected(self):
        for path in (
            ".env", ".env.local", "nested/.env.production", ".git/config",
            ".git/hooks/pre-commit", ".ssh/id_rsa", "id_rsa", "secret.pem",
        ):
            with self.subTest(path=path):
                self.rejected_read_and_write(path)

    def test_invalid_path_types_and_controls_are_rejected(self):
        for path in ("", ".", "./", "bad\x00name", "bad\nname", "bad\rname"):
            with self.subTest(path=path):
                self.rejected_read_and_write(path)

    def test_symlink_file_cannot_read_or_modify_external_file(self):
        outside = self.base / "outside.txt"
        outside.write_text("external original", encoding="utf-8")
        link = self.root / "alias.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")
        self.rejected_read_and_write("alias.txt")
        self.assertEqual(outside.read_text(encoding="utf-8"), "external original")

    def test_symlink_parent_cannot_escape_root(self):
        outside = self.base / "external"
        outside.mkdir()
        (outside / "file.txt").write_text("external original", encoding="utf-8")
        try:
            (self.root / "alias").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")
        self.rejected_read_and_write("alias/file.txt")
        self.assertEqual((outside / "file.txt").read_text(encoding="utf-8"), "external original")

    @unittest.skipUnless(os.name == "nt", "NTFS junction check is Windows-specific")
    def test_windows_junction_parent_cannot_escape_root(self):
        outside = self.base / "junction-external"
        outside.mkdir()
        (outside / "file.txt").write_text("external original", encoding="utf-8")
        junction = self.root / "junction"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True, text=True, timeout=10,
        )
        if made.returncode:
            self.skipTest("NTFS junction creation unavailable in this environment")
        self.rejected_read_and_write("junction/file.txt")
        self.assertEqual((outside / "file.txt").read_text(encoding="utf-8"), "external original")

    def test_hardlinked_file_is_rejected(self):
        outside = self.base / "outside.txt"
        outside.write_text("external original", encoding="utf-8")
        try:
            os.link(outside, self.root / "alias.txt")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlinks unavailable: {type(exc).__name__}")
        self.rejected_read_and_write("alias.txt")
        self.assertEqual(outside.read_text(encoding="utf-8"), "external original")

    def test_binary_or_non_utf8_input_is_rejected(self):
        for name, content in (("binary.txt", b"one\x00two"), ("invalid.txt", b"\xff\xfe")):
            with self.subTest(name=name):
                target = self.root / name
                target.write_bytes(content)
                with self.assertRaises(UndoWeaveError):
                    self.engine.read(name)
                with self.assertRaises(UndoWeaveError):
                    self.engine.edit(name, "replacement", "agent-a", "missing")
                self.assertEqual(target.read_bytes(), content)

    def test_nul_content_and_oversized_content_are_rejected(self):
        for content in ("one\x00two", "x" * (4 * 1024 * 1024)):
            with self.subTest(size=len(content)):
                with self.assertRaises(UndoWeaveError):
                    self.engine.edit("new.txt", content, "agent-a", "missing")
                self.assertFalse((self.root / "new.txt").exists())

    def test_oversized_existing_file_is_never_overwritten(self):
        content = b"x" * (4 * 1024 * 1024)
        target = self.root / "large.txt"
        target.write_bytes(content)
        with self.assertRaises(UndoWeaveError):
            self.engine.read("large.txt")
        with self.assertRaises(UndoWeaveError):
            self.engine.edit("large.txt", "replacement", "agent-a", "missing")
        self.assertEqual(target.read_bytes(), content)

    def test_excessive_line_count_is_rejected_without_writes(self):
        content = b"x\n" * 5000
        target = self.root / "many-lines.txt"
        target.write_bytes(content)
        with self.assertRaises(UndoWeaveError):
            self.engine.read("many-lines.txt")
        with self.assertRaises(UndoWeaveError):
            self.engine.edit("new.txt", content.decode(), "agent-a", "missing")
        self.assertEqual(target.read_bytes(), content)
        self.assertFalse((self.root / "new.txt").exists())

    def test_unbounded_actor_or_label_is_rejected(self):
        for actor, label in (("", ""), ("a" * 10000, ""), ("agent-a", "x" * 10000)):
            with self.subTest(actor_length=len(actor), label_length=len(label)):
                with self.assertRaises(UndoWeaveError):
                    self.engine.edit("new.txt", "new", actor, "missing", label)
                self.assertFalse((self.root / "new.txt").exists())

    def test_expected_hash_required_for_creation_and_replacement(self):
        with self.assertRaises(UndoWeaveError):
            self.engine.edit("new.txt", "new", "agent-a", "not-a-valid-hash")
        self.assertFalse((self.root / "new.txt").exists())
        target = self.put("file.txt", "human original")
        with self.assertRaises(UndoWeaveError):
            self.engine.edit("file.txt", "agent replacement", "agent-a", "missing")
        self.assertEqual(target.read_text(encoding="utf-8"), "human original")

    def test_external_change_invalidates_preview_without_writes(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "ready")
        target = self.put("file.txt", "ONE\ntwo\nhuman addition\n")
        before = target.read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertEqual(target.read_bytes(), before)

    def test_undo_token_cannot_be_replayed(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        self.engine.apply_undo(preview["token"])
        before = (self.root / "file.txt").read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertEqual((self.root / "file.txt").read_bytes(), before)

    def test_undo_token_cannot_cross_workspaces(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        other = self.base / "other-project"
        other.mkdir()
        target = other / "file.txt"
        target.write_text("other workspace", encoding="utf-8")
        engine = Engine(other, self.base / "other-state")
        with self.assertRaises(UndoWeaveError):
            engine.apply_undo(preview["token"])
        self.assertEqual(target.read_text(encoding="utf-8"), "other workspace")

    def test_state_directory_is_bound_to_its_original_workspace(self):
        self.record()
        other = self.base / "other-project"
        other.mkdir()
        with self.assertRaises(UndoWeaveError):
            Engine(other, self.state)

    def test_dotdot_state_alias_inside_project_is_refused_before_creation(self):
        outside = self.base / "outside"
        outside.mkdir()
        alias = outside / ".." / self.root.name / "leaked-state"
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, alias)
        self.assertFalse((self.root / "leaked-state").exists())

    @unittest.skipUnless(os.name == "nt", "NTFS short-name aliases are Windows-specific")
    def test_short_name_state_alias_inside_project_is_refused_before_creation(self):
        import ctypes
        from ctypes import wintypes

        project = self.base / "project-with-a-long-name"
        project.mkdir()
        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        get_short.restype = wintypes.DWORD
        size = get_short(str(project), None, 0)
        if not size:
            self.skipTest("NTFS short-name lookup unavailable")
        buffer = ctypes.create_unicode_buffer(size)
        if not get_short(str(project), buffer, size):
            self.skipTest("NTFS short-name lookup unavailable")
        name = Path(buffer.value).name
        if name.casefold() == project.name.casefold():
            self.skipTest("8.3 aliases are disabled on the test volume")
        alias = self.base / name / "leaked-state"
        with self.assertRaises(UndoWeaveError):
            Engine(project, alias)
        self.assertFalse((project / "leaked-state").exists())

    def test_symlinked_state_directory_is_rejected(self):
        destination = self.base / "other-state"
        destination.mkdir()
        alias = self.base / "state-alias"
        try:
            alias.symlink_to(destination, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, alias)
        self.assertEqual(list(destination.iterdir()), [])

    def test_symlink_substitution_after_preview_is_rejected(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        outside = self.base / "outside.txt"
        outside.write_bytes(b"ONE\ntwo\n")
        target = self.root / "file.txt"
        target.unlink()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertTrue(target.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "ONE\ntwo\n")

    def test_hardlink_substitution_after_preview_is_rejected(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        outside = self.base / "outside.txt"
        outside.write_bytes(b"ONE\ntwo\n")
        target = self.root / "file.txt"
        target.unlink()
        try:
            os.link(outside, target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlinks unavailable: {type(exc).__name__}")
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertEqual(target.read_bytes(), b"ONE\ntwo\n")
        self.assertEqual(outside.read_bytes(), b"ONE\ntwo\n")

    def test_overlap_is_a_conflict_and_preserves_human_bytes(self):
        change = self.record()
        target = self.put("file.txt", "human replaced the first line\ntwo\n")
        before = target.read_bytes()
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "conflict")
        self.assertFalse(preview.get("token"))
        self.assertEqual(target.read_bytes(), before)

    def test_preview_does_not_modify_project(self):
        change = self.record()
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        self.engine.preview_undo(change["id"])
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_history_status_and_errors_exclude_source_contents(self):
        sentinel = "SYNTHETIC_PRIVATE_SOURCE_f7bc1c928103"
        self.record(before=sentinel + "\n", after="replacement\n")
        summaries = json.dumps({"history": self.engine.history(), "status": self.engine.status()})
        self.assertNotIn(sentinel, summaries)
        with self.assertRaises(UndoWeaveError) as caught:
            self.engine.edit("file.txt", "new", "agent-a", "missing")
        self.assertNotIn(sentinel, str(caught.exception))
        self.assertNotIn("Traceback", str(caught.exception))

    def update_journal(self, sql, parameters=()):
        with closing(sqlite3.connect(self.state / "journal.sqlite3")) as db:
            with db:
                db.execute(sql, parameters)

    def test_corrupt_snapshot_is_rejected_before_undo(self):
        change = self.record()
        self.update_journal("UPDATE changes SET before_text=? WHERE id=?", ("tampered", change["id"]))
        before = (self.root / "file.txt").read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.preview_undo(change["id"])
        self.assertEqual((self.root / "file.txt").read_bytes(), before)

    def test_tampered_plan_result_is_not_trusted(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        self.update_journal("UPDATE plans SET result_text=? WHERE token=?", ("tampered", preview["token"]))
        before = (self.root / "file.txt").read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertEqual((self.root / "file.txt").read_bytes(), before)

    def test_expired_plan_cannot_write(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        self.update_journal("UPDATE plans SET created=0 WHERE token=?", (preview["token"],))
        before = (self.root / "file.txt").read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertEqual((self.root / "file.txt").read_bytes(), before)

    def test_unsupported_journal_schema_is_rejected(self):
        target = self.put("file.txt", "original\n")
        self.update_journal("UPDATE metadata SET value='999' WHERE key='schema'")
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, self.state)
        self.assertEqual(target.read_bytes(), b"original\n")

    @unittest.skipUnless(os.name == "nt", "case aliases address the same file on Windows")
    def test_recorded_case_alias_aba_invalidates_preview(self):
        change = self.record()
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "ready")
        digest = self.engine.read("FILE.TXT")["sha256"]
        self.engine.edit("FILE.TXT", "agent-b contents\n", "agent-b", digest)
        digest = self.engine.read("FILE.TXT")["sha256"]
        self.engine.edit("FILE.TXT", "ONE\ntwo\n", "agent-b", digest)
        before = (self.root / "file.txt").read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.apply_undo(preview["token"])
        self.assertEqual((self.root / "file.txt").read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "NTFS short-name aliases are Windows-specific")
    def test_short_name_alias_cannot_bypass_credential_path_protection(self):
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        get_short.restype = wintypes.DWORD
        exercised = 0
        for name in ("credentials.json", ".env.production"):
            target = self.put(name, "synthetic sensitive fixture\n")
            size = get_short(str(target), None, 0)
            if not size:
                continue
            buffer = ctypes.create_unicode_buffer(size)
            if not get_short(str(target), buffer, size):
                continue
            alias = Path(buffer.value).name
            if alias.casefold() == name.casefold():
                continue
            exercised += 1
            self.rejected_read_and_write(alias)
            self.assertEqual(target.read_bytes(), b"synthetic sensitive fixture\n")
        if not exercised:
            self.skipTest("8.3 aliases are disabled on the test volume")

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_private_state_permissions_and_existing_mode_are_preserved(self):
        import stat

        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / "journal.sqlite3").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.state / "session.lock").stat().st_mode), 0o600)
        target = self.put("executable.txt", "original\n")
        target.chmod(0o751)
        change = self.engine.edit("executable.txt", "agent\n", "agent-a", self.engine.read("executable.txt")["sha256"])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o751)
        preview = self.engine.preview_undo(change["id"])
        self.engine.apply_undo(preview["token"])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o751)
        self.assertEqual(target.read_bytes(), b"original\n")

    def test_interrupted_before_write_recovers_as_aborted(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        with patch.object(self.engine, "_replace", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.engine.edit("file.txt", "agent\n", "agent-a", digest)
        self.assertEqual(target.read_bytes(), b"original\n")
        resumed = Engine(self.root, self.state)
        self.assertEqual(resumed.status()["pending_count"], 0)
        self.assertEqual(resumed.history()[0]["status"], "aborted")
        self.assertEqual(target.read_bytes(), b"original\n")

    def test_known_stale_refusal_aborts_intent_and_keeps_workspace_usable(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        original_replace = self.engine._replace

        def human_changed_before_replace(*args, **kwargs):
            target.write_bytes(b"human changed before replacement\n")
            return original_replace(*args, **kwargs)

        with patch.object(self.engine, "_replace", side_effect=human_changed_before_replace):
            with self.assertRaises(UndoWeaveError) as caught:
                self.engine.edit("file.txt", "agent\n", "agent-a", digest)
        self.assertEqual(caught.exception.code, "stale_file")
        self.assertEqual(target.read_bytes(), b"human changed before replacement\n")
        resumed = Engine(self.root, self.state)
        self.assertEqual(resumed.status()["pending_count"], 0)
        self.assertEqual(resumed.history()[0]["status"], "aborted")
        change = resumed.edit("file.txt", "later approved edit\n", "agent-b", resumed.read("file.txt")["sha256"])
        self.assertEqual(change["status"], "applied")
        self.assertEqual(target.read_bytes(), b"later approved edit\n")

    def test_interrupted_after_write_recovers_as_applied(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        original_replace = self.engine._replace

        def interrupted_after_replace(*args, **kwargs):
            original_replace(*args, **kwargs)
            raise OSError("synthetic interruption after file replacement")

        with patch.object(self.engine, "_replace", side_effect=interrupted_after_replace):
            with self.assertRaises(OSError):
                self.engine.edit("file.txt", "agent\n", "agent-a", digest)
        self.assertEqual(target.read_bytes(), b"agent\n")
        resumed = Engine(self.root, self.state)
        self.assertEqual(resumed.status()["pending_count"], 0)
        self.assertEqual(resumed.history()[0]["status"], "applied")
        self.assertEqual(target.read_bytes(), b"agent\n")

    def test_unknown_interrupted_result_blocks_recovery_without_mutation(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        with patch.object(self.engine, "_replace", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.engine.edit("file.txt", "agent\n", "agent-a", digest)
        target.write_bytes(b"later human work\n")
        diagnostic = Engine(self.root, self.state)
        self.assertEqual(diagnostic.status()["pending_count"], 1)
        self.assertEqual(diagnostic.history()[0]["status"], "pending")
        with self.assertRaises(UndoWeaveError) as caught:
            diagnostic.edit("file.txt", "retry\n", "agent-a", diagnostic.read("file.txt")["sha256"])
        self.assertEqual(caught.exception.code, "recovery_required")
        self.assertEqual(target.read_bytes(), b"later human work\n")

    def test_unknown_exception_after_actual_write_and_human_change_blocks_recovery(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        original_replace = self.engine._replace

        def interrupted_after_write_and_human_change(*args, **kwargs):
            original_replace(*args, **kwargs)
            target.write_bytes(b"human work after the actual replacement\n")
            raise OSError("synthetic interruption after replacement and human edit")

        with patch.object(self.engine, "_replace", side_effect=interrupted_after_write_and_human_change):
            with self.assertRaises(OSError):
                self.engine.edit("file.txt", "agent\n", "agent-a", digest)
        diagnostic = Engine(self.root, self.state)
        self.assertEqual(diagnostic.status()["pending_count"], 1)
        self.assertEqual(diagnostic.history()[0]["status"], "pending")
        with self.assertRaises(UndoWeaveError) as caught:
            diagnostic.edit("file.txt", "retry\n", "agent-a", diagnostic.read("file.txt")["sha256"])
        self.assertEqual(caught.exception.code, "recovery_required")
        self.assertEqual(target.read_bytes(), b"human work after the actual replacement\n")

    def test_typed_error_after_actual_write_is_not_misclassified_as_aborted(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        original_replace = self.engine._replace

        def typed_error_after_replace(*args, **kwargs):
            original_replace(*args, **kwargs)
            raise UndoWeaveError("synthetic_error", "Synthetic error after actual replacement")

        with patch.object(self.engine, "_replace", side_effect=typed_error_after_replace):
            with self.assertRaises(UndoWeaveError):
                self.engine.edit("file.txt", "agent\n", "agent-a", digest)
        self.assertEqual(target.read_bytes(), b"agent\n")
        resumed = Engine(self.root, self.state)
        self.assertEqual(resumed.status()["pending_count"], 0)
        self.assertEqual(resumed.history()[0]["status"], "applied")
        self.assertEqual(target.read_bytes(), b"agent\n")

    def test_non_sqlite_state_file_is_rejected_without_project_changes(self):
        target = self.put("file.txt", "original\n")
        (self.state / "journal.sqlite3").write_bytes(b"this is not a SQLite database")
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, self.state)
        self.assertEqual(target.read_bytes(), b"original\n")

    def test_hardlinked_journal_is_rejected(self):
        journal = self.state / "journal.sqlite3"
        before = journal.read_bytes()
        try:
            os.link(journal, self.base / "journal-alias")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlinks unavailable: {type(exc).__name__}")
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, self.state)
        self.assertEqual(journal.read_bytes(), before)

    def test_hardlinked_lock_is_rejected(self):
        lock = self.state / "session.lock"
        before = lock.read_bytes()
        try:
            os.link(lock, self.base / "lock-alias")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlinks unavailable: {type(exc).__name__}")
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, self.state)
        self.assertEqual(lock.read_bytes(), before)

    def test_process_lock_spans_durable_intent_and_project_replacement(self):
        target = self.put("file.txt", "original\n")
        digest = self.engine.read("file.txt")["sha256"]
        intent = self.base / "writer-a-intent"
        release = self.base / "release-writer-a"
        entering = self.base / "writer-b-entering"
        constructed = self.base / "writer-b-constructed"
        script_a = r'''
import json, sys, time
from pathlib import Path
from undoweave.engine import Engine
root, state, digest, intent, release = sys.argv[1:]
engine = Engine(root, state)
original = engine._replace
def paused_replace(*args, **kwargs):
    Path(intent).write_bytes(b"intent durable")
    end = time.monotonic() + 45
    while not Path(release).exists():
        if time.monotonic() > end:
            raise RuntimeError("test release timeout")
        time.sleep(0.01)
    return original(*args, **kwargs)
engine._replace = paused_replace
print(json.dumps(engine.edit("file.txt", "writer-a\n", "agent-a", digest)))
'''
        script_b = r'''
import json, sys
from pathlib import Path
from undoweave.engine import Engine, UndoWeaveError
root, state, digest, entering, constructed = sys.argv[1:]
Path(entering).write_bytes(b"entering")
engine = Engine(root, state)
Path(constructed).write_bytes(b"constructor finished")
try:
    print(json.dumps(engine.edit("file.txt", "writer-b\n", "agent-b", digest)))
except UndoWeaveError as exc:
    print(json.dumps({"error": exc.code}))
'''

        def wait_for(path, process):
            deadline = time.monotonic() + 30
            while not path.exists():
                if process.poll() is not None:
                    output, error = process.communicate()
                    self.fail(f"child exited before {path.name}: {output!r} {error!r}")
                if time.monotonic() > deadline:
                    self.fail(f"child process did not reach {path.name}")
                time.sleep(0.01)

        children = []
        try:
            process_a = subprocess.Popen(
                [sys.executable, "-c", script_a, str(self.root), str(self.state), digest, str(intent), str(release)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            children.append(process_a)
            wait_for(intent, process_a)
            process_b = subprocess.Popen(
                [sys.executable, "-c", script_b, str(self.root), str(self.state), digest, str(entering), str(constructed)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            children.append(process_b)
            wait_for(entering, process_b)
            # B has imported Engine and is immediately about to construct it.
            # It must stay behind A's lock even though A committed its intent.
            time.sleep(0.15)
            self.assertFalse(constructed.exists(), "a second process entered during a pending file replacement")
            release.write_bytes(b"continue")
            out_a, err_a = process_a.communicate(timeout=15)
            out_b, err_b = process_b.communicate(timeout=15)
            self.assertEqual(process_a.returncode, 0, err_a)
            self.assertEqual(process_b.returncode, 0, err_b)
            self.assertEqual(json.loads(out_a)["status"], "applied")
            self.assertEqual(json.loads(out_b)["error"], "stale_file")
            self.assertEqual(target.read_bytes(), b"writer-a\n")
            self.assertEqual(len(self.engine.history()), 1)
            self.assertEqual(self.engine.status()["pending_count"], 0)
        finally:
            release.write_bytes(b"continue")
            for process in children:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=15)


if __name__ == "__main__":
    unittest.main()
