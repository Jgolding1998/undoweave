"""Exact-byte and bounded-payload checks for optional narrow reads/edits."""
import json
from pathlib import Path
import subprocess
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest

from undoweave.engine import Engine, UndoWeaveError


class ExactEditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state"
        self.engine = Engine(self.root, self.state)
        self.target = self.root / "source.txt"
        self.original = "first\r\nsecond\r\nthird\r\nlast"
        self.target.write_bytes(self.original.encode())

    def test_range_and_metadata_keep_full_file_hash(self):
        whole = self.engine.read("source.txt")
        metadata = self.engine.read("source.txt", metadata_only=True)
        partial = self.engine.read("source.txt", 2, 3)
        self.assertEqual(partial["content"], "second\r\nthird\r\n")
        self.assertIsNone(metadata["content"])
        self.assertEqual(metadata["byte_length"], len(self.original.encode()))
        self.assertEqual(metadata["line_count"], 4)
        self.assertEqual(whole["sha256"], partial["sha256"])
        self.assertEqual(whole["sha256"], metadata["sha256"])

    def test_exact_replacement_undo_and_acknowledgement(self):
        digest = self.engine.read("source.txt", metadata_only=True)["sha256"]
        changed = self.engine.replace_text("source.txt", "second\r\n", "CHANGED\r\n", "agent", digest)
        self.assertEqual(self.target.read_bytes(), self.original.replace("second", "CHANGED").encode())
        self.assertEqual(changed["after_hash"], self.engine.read("source.txt")["sha256"])
        self.assertTrue(changed["id"])
        inverse = self.engine.apply_undo(self.engine.preview_undo(changed["id"])["token"])
        self.assertEqual(inverse["after_hash"], digest)
        self.assertEqual(self.target.read_bytes(), self.original.encode())

    def test_duplicate_overlapping_and_absent_matches_refuse(self):
        self.target.write_bytes(b"aaa\n")
        digest = self.engine.read("source.txt")["sha256"]
        for old in ("aa", "not present", ""):
            with self.subTest(old=old), self.assertRaises(UndoWeaveError):
                self.engine.replace_text("source.txt", old, "new", "agent", digest)
            self.assertEqual(self.target.read_bytes(), b"aaa\n")
        self.assertEqual(self.engine.history(), [])

    def test_stale_range_hash_cannot_replace_later_human_work(self):
        digest = self.engine.read("source.txt", 2, 2)["sha256"]
        self.target.write_bytes(b"human changed the file\n")
        with self.assertRaises(UndoWeaveError):
            self.engine.replace_text("source.txt", "second", "CHANGED", "agent", digest)
        self.assertEqual(self.target.read_bytes(), b"human changed the file\n")

    def test_invalid_read_options_refuse(self):
        for kwargs in ({"start_line": 0}, {"start_line": 3, "end_line": 2}, {"start_line": 999}, {"start_line": True}, {"metadata_only": True, "start_line": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UndoWeaveError):
                self.engine.read("source.txt", **kwargs)

    def test_cli_range_and_exact_replace(self):
        def call(*arguments):
            response = subprocess.run([sys.executable, "-I", "-m", "undoweave", "--root", str(self.root), "--state-dir", str(self.state), *arguments], capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(response.returncode, 0, response.stderr)
            return json.loads(response.stdout)
        selected = call("read", "source.txt", "--start-line", "2", "--end-line", "2")
        self.assertEqual(selected["content"], "second\r\n")
        old, new = self.base / "old.txt", self.base / "new.txt"
        old.write_bytes(b"second\r\n")
        new.write_bytes(b"NEW\r\n")
        edited = call("replace", "source.txt", "--old-file", str(old), "--new-file", str(new), "--actor", "agent", "--expected-hash", selected["sha256"])
        self.assertEqual(edited["after_hash"], self.engine.read("source.txt")["sha256"])
        self.assertEqual(self.target.read_bytes(), self.original.replace("second", "NEW").encode())

    def test_cli_legacy_adoption_requires_explicit_matching_root(self):
        changed = self.engine.edit("source.txt", "new\n", "agent", self.engine.read("source.txt")["sha256"])
        with closing(sqlite3.connect(self.state / "journal.sqlite3")) as db:
            with db:
                db.execute("UPDATE metadata SET value='1' WHERE key='schema'")
                db.execute("DELETE FROM metadata WHERE key IN ('workspace_id','root_identity')")
        def call(*arguments):
            return subprocess.run([sys.executable, "-I", "-m", "undoweave", "--root", str(self.root), "--state-dir", str(self.state), *arguments], capture_output=True, encoding="utf-8", timeout=30)
        refused = call("status")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(json.loads(refused.stderr)["error"]["code"], "legacy_journal")
        wrong = call("adopt-legacy", "--confirm-root", str(self.base))
        self.assertNotEqual(wrong.returncode, 0)
        adopted = call("adopt-legacy", "--confirm-root", str(self.root))
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertEqual(json.loads(adopted.stdout)["status"], "adopted")
        self.assertEqual(self.target.read_bytes(), b"new\n")
        self.assertIn(changed["id"], {row["id"] for row in self.engine.history()})


if __name__ == "__main__":
    unittest.main()
