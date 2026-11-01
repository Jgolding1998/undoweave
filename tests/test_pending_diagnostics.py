"""Pending-target diagnostics without weakening workspace/journal admission.

Each intent is produced through the real writer and real os.replace, followed
by a synthetic post-replacement failure. Only permission denial is mocked at
read time. No recorded snapshots or pending rows are fabricated.
"""

from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from undoweave.engine import Engine, MAX_BYTES, UndoWeaveError, WORKSPACE_MARKER


REAL_REPLACE = os.replace
REAL_READ = Engine._read


class PendingDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="undoweave-pending-diagnostics-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state"
        self.engine = Engine(self.root, self.state)
        self.target = self.root / "pending.txt"
        self.target.write_bytes(b"before\n")
        self.other = self.root / "other.txt"
        self.other.write_bytes(b"unrelated\n")
        self.other_hash = self.engine.read("other.txt")["sha256"]

    def leave_pending(self, observed=None):
        before_hash = self.engine.read("pending.txt")["sha256"]

        def replace_then_fail(source, destination):
            REAL_REPLACE(source, destination)
            if observed is not None:
                Path(destination).write_bytes(observed)
            raise OSError(5, "synthetic interruption after real replacement")

        with patch.object(os, "replace", side_effect=replace_then_fail):
            with self.assertRaises(OSError):
                self.engine.edit("pending.txt", "after\n", "agent", before_hash)
        self.assertEqual(self.engine.status()["pending_count"], 1)
        row = self.engine.history(1)[0]
        self.assertEqual(row["status"], "pending")
        self.pending_id = row["id"]
        self.assertEqual(self.target.read_bytes(), b"after\n" if observed is None else observed)
        self.assertEqual(list(self.root.glob(".undoweave-write-*")), [])

    def assert_diagnostic_only(self, restarted, observed):
        status = restarted.status()
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual(status["pending_paths"], ["pending.txt"])
        self.assertIsNotNone(status["recovery_notice"])
        row = restarted.history(1)[0]
        self.assertEqual((row["id"], row["status"]), (self.pending_id, "pending"))
        with self.assertRaises(UndoWeaveError) as caught:
            restarted.edit("other.txt", "do not apply\n", "agent", self.other_hash)
        self.assertEqual(caught.exception.code, "recovery_required")
        self.assertEqual(self.target.read_bytes(), observed)
        self.assertEqual(self.other.read_bytes(), b"unrelated\n")
        self.assertEqual(restarted.status()["pending_count"], 1)
        self.assertEqual(restarted.history(1)[0]["status"], "pending")

    def test_non_utf8_pending_target_keeps_diagnostics_and_blocks_edits(self):
        observed = b"\xff\x00outside bytes"
        self.leave_pending(observed)
        self.assert_diagnostic_only(Engine(self.root, self.state), observed)

    def test_oversized_pending_target_keeps_diagnostics_and_blocks_edits(self):
        observed = b"x" * (MAX_BYTES + 1)
        self.leave_pending(observed)
        self.assert_diagnostic_only(Engine(self.root, self.state), observed)

    def test_permission_denied_pending_target_keeps_diagnostics_and_blocks_edits(self):
        self.leave_pending()

        def deny_pending(instance, path):
            if path == "pending.txt":
                raise PermissionError(13, "synthetic target permission refusal")
            return REAL_READ(instance, path)

        with patch.object(Engine, "_read", deny_pending):
            self.assert_diagnostic_only(Engine(self.root, self.state), b"after\n")

    def test_replaced_root_still_refuses_with_pending_unsupported_target(self):
        observed = b"\xff\x00pending"
        self.leave_pending(observed)
        retained = self.base / "retained-project"
        self.root.rename(retained)
        self.root.mkdir()
        self.target.write_bytes(b"different project\n")
        with self.assertRaises(UndoWeaveError) as caught:
            Engine(self.root, self.state)
        self.assertEqual(caught.exception.code, "workspace_replaced")
        self.assertEqual(self.target.read_bytes(), b"different project\n")
        self.assertEqual((retained / "pending.txt").read_bytes(), observed)

    def test_invalid_marker_still_refuses_with_pending_unsupported_target(self):
        observed = b"\xff\x00pending"
        self.leave_pending(observed)
        marker = self.root / WORKSPACE_MARKER
        marker.write_bytes(b"invalid marker\n")
        with self.assertRaises(UndoWeaveError) as caught:
            Engine(self.root, self.state)
        self.assertEqual(caught.exception.code, "invalid_workspace_marker")
        self.assertEqual(marker.read_bytes(), b"invalid marker\n")
        self.assertEqual(self.target.read_bytes(), observed)

    def test_foreign_journal_still_refuses_with_pending_unsupported_target(self):
        observed = b"\xff\x00pending"
        self.leave_pending(observed)
        foreign_root = self.base / "foreign-project"
        foreign_root.mkdir()
        foreign_state = self.base / "foreign-state"
        Engine(foreign_root, foreign_state)
        shutil.copy2(foreign_state / "journal.sqlite3", self.state / "journal.sqlite3")
        with self.assertRaises(UndoWeaveError) as caught:
            Engine(self.root, self.state)
        self.assertEqual(caught.exception.code, "wrong_workspace")
        self.assertEqual(self.target.read_bytes(), observed)


if __name__ == "__main__":
    unittest.main()
