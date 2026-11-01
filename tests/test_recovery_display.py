"""Write-failure classification, interrupted-write recovery, physical line
counting, exact replacement bytes, and review-diff line-ending display.

Every case runs in a TemporaryDirectory. Failures are injected only through
real Windows sharing/attribute behaviour or by wrapping ``os.replace``; no test
edits the journal directly. Outcomes are decided by observed file bytes, never
by exception type alone.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from undoweave.engine import Engine, UndoWeaveError

WINDOWS = os.name == "nt"
REAL_REPLACE = os.replace


class Workspace(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="undoweave-recovery-")
        self.addCleanup(self._cleanup)
        self.root = Path(self.directory.name) / "project"
        self.root.mkdir()
        self.state = Path(self.directory.name) / "state"
        self.engine = Engine(self.root, self.state)

    def _cleanup(self):
        for path in Path(self.directory.name).rglob("*"):
            try:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if path.is_dir() else 0))
            except OSError:
                pass
        self.directory.cleanup()

    def put(self, name, data: bytes):
        target = self.root / name
        target.write_bytes(data)
        return target

    def record(self, name, text, actor="agent-a"):
        snapshot = self.engine.read(name)
        return self.engine.edit(name, text, actor=actor, expected_hash=snapshot["sha256"])

    def leftovers(self, directory=None):
        return sorted(p.name for p in (directory or self.root).glob(".undoweave-write-*"))

    def assert_refused_unchanged(self, name, data, call):
        with self.assertRaises(UndoWeaveError) as caught:
            call()
        self.assertEqual(caught.exception.code, "write_refused")
        self.assertEqual((self.root / name).read_bytes(), data)
        status = self.engine.status()
        self.assertEqual(status["pending_count"], 0)
        self.assertIsNone(status["recovery_notice"])
        self.assertEqual(self.engine.history(1)[0]["status"], "aborted")
        self.assertEqual(self.leftovers(), [])


# --------------------------------------------------------------------------- Windows reality

@unittest.skipUnless(WINDOWS, "Windows sharing and read-only attribute semantics")
class WindowsRefusalTests(Workspace):
    def test_open_handle_refusal_is_aborted_and_later_human_edit_does_not_block(self):
        target = self.put("a.txt", b"one\n")
        with open(target, "rb"):  # like an editor, indexer, or antivirus handle
            self.assert_refused_unchanged("a.txt", b"one\n", lambda: self.record("a.txt", "two\n"))
        target.write_bytes(b"human\n")  # previously turned the workspace into recovery_required
        self.assertEqual(Engine(self.root, self.state).status()["pending_count"], 0)
        self.record("a.txt", "agent\n")
        self.assertEqual(target.read_bytes(), b"agent\n")

    def test_read_only_attribute_refusal_keeps_attribute_and_cleans_temporary(self):
        target = self.put("ro.txt", b"one\r\n")
        os.chmod(target, stat.S_IREAD)
        self.assert_refused_unchanged("ro.txt", b"one\r\n", lambda: self.record("ro.txt", "two\r\n"))
        self.assertFalse(target.stat().st_mode & stat.S_IWRITE, "read-only attribute must survive a refusal")

    def test_read_only_delete_refusal_is_aborted(self):
        target = self.put("keep.txt", b"keep\n")
        os.chmod(target, stat.S_IREAD)
        snapshot = self.engine.read("keep.txt")
        self.assert_refused_unchanged("keep.txt", b"keep\n", lambda: self.engine.edit(
            "keep.txt", None, actor="agent-a", expected_hash=snapshot["sha256"]))
        self.assertTrue(target.exists())

    def test_open_handle_refusal_of_apply_undo_keeps_plan_and_history_consistent(self):
        target = self.put("u.txt", b"a\nb\nc\n")
        edit = self.record("u.txt", "a\nB\nc\n")
        preview = self.engine.preview_undo(edit["id"])
        with open(target, "rb"):
            self.assert_refused_unchanged("u.txt", b"a\nB\nc\n", lambda: self.engine.apply_undo(preview["token"]))
        self.assertIsNone(self.engine.history(2)[1]["undone_by"])
        again = self.engine.preview_undo(edit["id"])
        self.engine.apply_undo(again["token"])
        self.assertEqual(target.read_bytes(), b"a\nb\nc\n")


# --------------------------------------------------------------------------- injected failures

class InjectedReplaceFailureTests(Workspace):
    def test_refusal_before_mutation_is_typed_and_not_pending(self):
        self.put("f.txt", b"one\n")
        def refuse(source, destination):
            raise PermissionError(13, "simulated sharing violation")
        with mock.patch.object(os, "replace", side_effect=refuse):
            self.assert_refused_unchanged("f.txt", b"one\n", lambda: self.record("f.txt", "two\n"))

    def test_failure_after_real_replacement_stays_pending_then_recovers_applied(self):
        target = self.put("f.txt", b"one\n")
        def replace_then_fail(source, destination):
            REAL_REPLACE(source, destination)
            raise OSError(5, "simulated failure after replacement")
        with mock.patch.object(os, "replace", side_effect=replace_then_fail):
            with self.assertRaises(OSError):
                self.record("f.txt", "two\n")
        self.assertEqual(target.read_bytes(), b"two\n")
        self.assertEqual(self.engine.status()["pending_count"], 1)
        self.assertEqual(self.engine.history(1)[0]["status"], "pending")
        restarted = Engine(self.root, self.state)  # startup classifies by bytes
        self.assertEqual(restarted.status()["pending_count"], 0)
        row = restarted.history(1)[0]
        self.assertEqual(row["status"], "applied")
        preview = restarted.preview_undo(row["id"])  # the recovered edit is undoable
        restarted.apply_undo(preview["token"])
        self.assertEqual(target.read_bytes(), b"one\n")
        self.assertEqual(self.leftovers(), [])

    def test_third_content_stays_pending_reads_work_and_mutations_refuse(self):
        target = self.put("f.txt", b"one\n")
        other = self.put("other.txt", b"x\n")
        earlier = self.record("other.txt", "y\n")
        def replace_with_third(source, destination):
            REAL_REPLACE(source, destination)
            Path(destination).write_bytes(b"third\n")
            raise OSError(5, "simulated failure with unknown result")
        with mock.patch.object(os, "replace", side_effect=replace_with_third):
            with self.assertRaises(OSError):
                self.record("f.txt", "two\n")
        restarted = Engine(self.root, self.state)  # diagnostic startup, no repair
        status = restarted.status()
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual(status["pending_paths"], ["f.txt"])
        self.assertIsNotNone(status["recovery_notice"])
        self.assertEqual(restarted.history(1)[0]["status"], "pending")
        self.assertEqual(restarted.read("f.txt")["content"], "third\n")
        snapshot = restarted.read("other.txt")
        for mutation in (
            lambda: restarted.edit("other.txt", "z\n", actor="agent-a", expected_hash=snapshot["sha256"]),
            lambda: restarted.replace_text("other.txt", "y", "z", actor="agent-a", expected_hash=snapshot["sha256"]),
            lambda: restarted.preview_undo(earlier["id"]),
            lambda: restarted.apply_undo("0" * 64),
        ):
            with self.assertRaises(UndoWeaveError) as caught:
                mutation()
            self.assertEqual(caught.exception.code, "recovery_required")
        self.assertEqual(target.read_bytes(), b"third\n")
        self.assertEqual(other.read_bytes(), b"y\n")
        self.assertEqual(restarted.status()["pending_count"], 1)  # still not auto-repaired

    def test_removed_parent_of_pending_creation_resolves_aborted(self):
        parent = self.root / "newdir"
        parent.mkdir()
        with mock.patch.object(os, "replace", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                self.record("newdir/n.txt", "hello\n")
        self.assertEqual(self.engine.status()["pending_count"], 1)
        self.assertEqual(self.leftovers(parent), [])
        parent.rmdir()
        restarted = Engine(self.root, self.state)
        self.assertEqual(restarted.status()["pending_count"], 0)
        self.assertEqual(restarted.history(1)[0]["status"], "aborted")
        self.put("next.txt", b"n\n")
        restarted.edit("next.txt", "m\n", actor="agent-a", expected_hash=restarted.read("next.txt")["sha256"])

    def test_removed_parent_of_pending_existing_file_edit_stays_pending(self):
        parent = self.root / "dir"
        parent.mkdir()
        self.put("dir/f.txt", b"old\n")
        with mock.patch.object(os, "replace", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                self.record("dir/f.txt", "new\n")
        (parent / "f.txt").unlink()
        parent.rmdir()
        restarted = Engine(self.root, self.state)
        self.assertEqual(restarted.status()["pending_count"], 1)
        self.assertEqual(restarted.history(1)[0]["status"], "pending")
        self.put("next.txt", b"n\n")
        with self.assertRaises(UndoWeaveError) as caught:
            restarted.edit("next.txt", "m\n", actor="agent-a", expected_hash=restarted.read("next.txt")["sha256"])
        self.assertEqual(caught.exception.code, "recovery_required")


# --------------------------------------------------------------------------- exact replacement bytes

class ExactReplacementBytesTests(Workspace):
    def test_bom_crlf_and_missing_final_newline_are_preserved(self):
        original = b"\xef\xbb\xbfline1\r\nline2\r\nline3"
        target = self.put("w.txt", original)
        snapshot = self.engine.read("w.txt")
        edit = self.engine.replace_text("w.txt", "line2", "LINE2", actor="agent-a", expected_hash=snapshot["sha256"])
        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbfline1\r\nLINE2\r\nline3")
        snapshot = self.engine.read("w.txt")
        self.engine.replace_text("w.txt", "1\r\nLINE2\r\nl", "1\r\nL2\r\nl", actor="agent-a", expected_hash=snapshot["sha256"])
        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbfline1\r\nL2\r\nline3")
        preview = self.engine.preview_undo(edit["id"])
        self.assertEqual(preview["status"], "conflict")  # same line changed again; never forced
        latest = self.engine.history(1)[0]
        self.engine.apply_undo(self.engine.preview_undo(latest["id"])["token"])
        self.engine.apply_undo(self.engine.preview_undo(edit["id"])["token"])
        self.assertEqual(target.read_bytes(), original)

    def test_lf_old_text_against_crlf_file_is_not_found_with_hint_and_unchanged(self):
        target = self.put("c.txt", b"x = 1\r\ny = 2\r\n")
        snapshot = self.engine.read("c.txt")
        with self.assertRaises(UndoWeaveError) as caught:
            self.engine.replace_text("c.txt", "x = 1\ny = 2", "x = 9\ny = 2", actor="agent-a", expected_hash=snapshot["sha256"])
        self.assertEqual(caught.exception.code, "match_not_found")
        self.assertIn("Line endings differ", str(caught.exception))
        self.assertEqual(target.read_bytes(), b"x = 1\r\ny = 2\r\n")
        self.assertEqual(self.engine.history(5), [])

    def test_absent_and_repeated_old_text_are_distinct(self):
        self.put("r.txt", b"aa\naa\n")
        snapshot = self.engine.read("r.txt")
        for old, code in (("zz", "match_not_found"), ("aa", "ambiguous_match"), ("a", "ambiguous_match")):
            with self.subTest(old=old):
                with self.assertRaises(UndoWeaveError) as caught:
                    self.engine.replace_text("r.txt", old, "q", actor="agent-a", expected_hash=snapshot["sha256"])
                self.assertEqual(caught.exception.code, code)

    def test_bare_cr_file_bytes_survive_replacement_and_undo(self):
        original = b"one\rtwo\rthree\r"
        target = self.put("mac.txt", original)
        snapshot = self.engine.read("mac.txt")
        edit = self.engine.replace_text("mac.txt", "two\r", "TWO\r", actor="agent-a", expected_hash=snapshot["sha256"])
        self.assertEqual(target.read_bytes(), b"one\rTWO\rthree\r")
        self.engine.apply_undo(self.engine.preview_undo(edit["id"])["token"])
        self.assertEqual(target.read_bytes(), original)


# --------------------------------------------------------------------------- physical lines

class PhysicalLineTests(Workspace):
    MIXED = "a\rb\r\nc d\x0ce\x0bf\x85g\ntail"

    def test_counting_and_ranges_use_lf_crlf_and_bare_cr_only(self):
        self.put("m.txt", self.MIXED.encode("utf-8"))
        full = self.engine.read("m.txt")
        self.assertEqual(full["line_count"], 4)
        expected = ["a\r", "b\r\n", "c d\x0ce\x0bf\x85g\n", "tail"]
        for number, line in enumerate(expected, start=1):
            with self.subTest(line=number):
                self.assertEqual(self.engine.read("m.txt", number, number)["content"], line)
        self.assertEqual(self.engine.read("m.txt", 2, 4)["content"], "".join(expected[1:]))
        with self.assertRaises(UndoWeaveError) as caught:
            self.engine.read("m.txt", 5, 5)
        self.assertEqual(caught.exception.code, "invalid_range")

    def test_line_limit_counts_physical_lines(self):
        self.put("many.txt", b"seed\n")
        separators_only = "x " * 5000 + "\x0c" * 100 + "\n"  # one physical line
        self.record("many.txt", separators_only)
        self.assertEqual(self.engine.read("many.txt")["line_count"], 1)
        snapshot = self.engine.read("many.txt")
        with self.assertRaises(UndoWeaveError) as caught:
            self.engine.edit("many.txt", "x\r" * 4001, actor="agent-a", expected_hash=snapshot["sha256"])
        self.assertEqual(caught.exception.code, "content_limit")
        self.engine.edit("many.txt", "x\r" * 4000, actor="agent-a", expected_hash=snapshot["sha256"])

    def test_merge_keeps_unicode_separators_inside_one_line(self):
        target = self.put("u.txt", "top\nmid dle\nbottom\n".encode())
        edit = self.record("u.txt", "TOP\nmid dle\nbottom\n")
        target.write_bytes("TOP\nmid dle\nBOTTOM\n".encode())  # separated human edit
        preview = self.engine.preview_undo(edit["id"])
        self.assertEqual(preview["status"], "ready")
        self.assertIn("mid\\u2028dle", preview["diff"])
        self.assertTrue(preview["display_escaped"])
        self.engine.apply_undo(preview["token"])
        self.assertEqual(target.read_bytes(), "top\nmid dle\nBOTTOM\n".encode())


# --------------------------------------------------------------------------- review display

class ReviewDisplayTests(Workspace):
    def preview_of(self, name, before: bytes, after: str):
        self.put(name, before)
        return self.engine.preview_undo(self.record(name, after)["id"])

    def body(self, diff):
        return diff.split("@@", 2)[2].split("\n", 1)[1]

    def test_uniform_crlf_shows_no_escapes_and_reports_profile(self):
        preview = self.preview_of("w.txt", b"l1\r\nl2\r\nl3\r\n", "l1\r\nX\r\nl3\r\n")
        self.assertNotIn("\\u000d", preview["diff"])
        self.assertNotIn("[CRLF]", preview["diff"])
        self.assertFalse(preview["display_escaped"])
        self.assertEqual(self.body(preview["diff"]), " l1\n-X\n+l2\n l3\n")
        self.assertEqual(preview["line_endings"]["current"], {"crlf": 3, "lf": 0, "cr": 0, "final_newline": True})
        self.assertEqual(preview["line_endings"]["result"], {"crlf": 3, "lf": 0, "cr": 0, "final_newline": True})

    def test_line_ending_only_change_is_visible(self):
        preview = self.preview_of("e.txt", b"a\r\nb\r\n", "a\nb\r\n")
        self.assertEqual(self.body(preview["diff"]), "-a [LF]\n+a [CRLF]\n b [CRLF]\n")
        self.assertEqual(preview["line_endings"]["current"]["lf"], 1)
        self.assertEqual(preview["line_endings"]["result"]["crlf"], 2)

    def test_bare_cr_lines_are_separate_and_get_no_eof_marker(self):
        preview = self.preview_of("mac.txt", b"x\ry\rz\r", "x\rY\rz\r")
        self.assertEqual(self.body(preview["diff"]), " x\n-Y\n+y\n z\n")
        self.assertNotIn("No newline", preview["diff"])
        self.assertEqual(preview["line_endings"]["current"], {"crlf": 0, "lf": 0, "cr": 3, "final_newline": True})

    def test_eof_marker_only_on_the_true_final_line(self):
        preview = self.preview_of("p.txt", b"p\nq", "p\nq\n")
        self.assertEqual(self.body(preview["diff"]), " p\n-q\n+q\n\\ No newline at end of file\n")
        self.assertEqual(preview["diff"].count("No newline"), 1)
        self.assertFalse(preview["line_endings"]["result"]["final_newline"])

    def test_missing_newline_on_changed_last_line_never_joins_lines(self):
        preview = self.preview_of("z.txt", b"x\ny", "x\nz")
        self.assertEqual(self.body(preview["diff"]), " x\n-z\n\\ No newline at end of file\n+y\n\\ No newline at end of file\n")

    def test_invisible_characters_are_escaped_and_flagged(self):
        preview = self.preview_of("b.txt", b"safe\n", "sa‮fe​\n")
        self.assertIn("\\u202e", preview["diff"])
        self.assertIn("\\u200b", preview["diff"])
        self.assertTrue(preview["display_escaped"])


if __name__ == "__main__":
    unittest.main()
