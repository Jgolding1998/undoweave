"""Independent regressions from the second security/correctness audit.

All projects and journals are disposable. These tests assert workspace identity
and expected bytes instead of assuming that matching file content proves that
two directory instances are the same project.
"""

from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import undoweave.engine as engine_module
from undoweave.engine import Engine, UndoWeaveError
from undoweave.merge import inverse_merge


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="undoweave-second-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state"
        self.engine = Engine(self.root, self.state)
        self.target = self.root / "sample.txt"
        self.target.write_bytes(b"original project text\n")
        self.change = self.engine.edit(
            "sample.txt", "shared after text\n", "agent-a",
            self.engine.read("sample.txt")["sha256"],
        )

    def replace_project_directory(self):
        # Retain the old directory so its identity cannot be immediately reused.
        old_root = self.base / "retained-old-project"
        self.root.rename(old_root)
        self.root.mkdir()
        self.target.write_bytes(b"shared after text\n")
        self.assertNotEqual(self.root.stat().st_ino, old_root.stat().st_ino)
        return old_root

    def test_recreated_project_cannot_reuse_the_old_journal(self):
        old_root = self.replace_project_directory()
        with self.assertRaises(UndoWeaveError):
            Engine(self.root, self.state)
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")
        self.assertEqual((old_root / "sample.txt").read_bytes(), b"shared after text\n")

    def test_running_engine_refuses_read_after_project_directory_replacement(self):
        self.replace_project_directory()
        with self.assertRaises(UndoWeaveError):
            self.engine.read("sample.txt")
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")

    def test_running_engine_refuses_edit_after_project_directory_replacement(self):
        expected = self.engine.read("sample.txt")["sha256"]
        self.replace_project_directory()
        with self.assertRaises(UndoWeaveError):
            self.engine.edit("sample.txt", "an edit intended for the old project\n", "agent-a", expected)
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")

    def test_running_engine_refuses_foreign_journal_after_backup_swap(self):
        other_root = self.base / "other-project"
        other_root.mkdir()
        other_state = self.base / "other-state"
        other = Engine(other_root, other_state)
        (other_root / "sample.txt").write_bytes(b"other project original\n")
        other_change = other.edit(
            "sample.txt", "shared after text\n", "agent-b",
            other.read("sample.txt")["sha256"],
        )
        # Both DB handles are closed. This models restoring the wrong journal
        # backup while a long-lived MCP process still holds its Engine object.
        shutil.copy2(other_state / "journal.sqlite3", self.state / "journal.sqlite3")
        with self.assertRaises(UndoWeaveError):
            self.engine.preview_undo(other_change["id"])
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")

    def test_consumed_previews_do_not_use_the_live_preview_quota(self):
        preview = self.engine.preview_undo(self.change["id"])
        with closing(sqlite3.connect(self.state / "journal.sqlite3")) as db:
            db.row_factory = sqlite3.Row
            old_plan = dict(db.execute("SELECT * FROM plans WHERE token=?", (preview["token"],)).fetchone())
        inverse = self.engine.apply_undo(preview["token"])
        # Seed the exact shape of 200 legitimate, consumed previews. An actual
        # 205-cycle API probe reproduced the original quota failure; seeding
        # keeps the public regression fast without hundreds of fsyncs.
        old_plan["used"] = 1
        columns = list(old_plan)
        sql = "INSERT OR REPLACE INTO plans (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")"
        with closing(sqlite3.connect(self.state / "journal.sqlite3")) as db:
            with db:
                for index in range(200):
                    old_plan["token"] = format(index, "064x")
                    db.execute(sql, [old_plan[column] for column in columns])
        redo = self.engine.preview_undo(inverse["id"])
        self.assertEqual(redo["status"], "ready")
        self.assertEqual(self.target.read_bytes(), b"original project text\n")

    def test_twenty_undo_redo_cycles_preserve_exact_content_and_other_file(self):
        unrelated = self.root / "human-notes.txt"
        unrelated.write_bytes(b"unrecorded human work\r\n")
        current = self.change
        for cycle in range(20):
            preview = self.engine.preview_undo(current["id"])
            self.assertEqual(preview["status"], "ready", cycle)
            current = self.engine.apply_undo(preview["token"])
            expected = b"original project text\n" if cycle % 2 == 0 else b"shared after text\n"
            self.assertEqual(self.target.read_bytes(), expected, cycle)
            self.assertEqual(unrelated.read_bytes(), b"unrecorded human work\r\n", cycle)
        self.assertEqual(self.engine.status()["pending_count"], 0)

    def test_many_disjoint_hunks_keep_the_exact_human_line(self):
        # A large number of independent hunks exercises the unique-context
        # lookup without asserting a machine-dependent timing threshold.
        size = 600
        original = [f"line_{index}_original\n" for index in range(size)]
        agent = [f"line_{index}_agent\n" if index % 2 == 0 else value
                 for index, value in enumerate(original)]
        # The accepted case has a stable unique separator between the final
        # agent hunk and later human work, as required by the merge policy.
        original.extend(["stable_unique_boundary\n", "human_original_tail\n"])
        agent.extend(["stable_unique_boundary\n", "human_original_tail\n"])
        current = agent.copy()
        current[-1] = "human_final_line\n"
        expected = original.copy()
        expected[-1] = current[-1]
        self.assertEqual(inverse_merge("".join(original), "".join(agent), "".join(current)), "".join(expected))

    def test_adjacent_agent_and_human_changes_without_separator_refuse_unchanged(self):
        before = b"agent=old\nhuman=old\nunchanged_tail\n"
        agent_after = "agent=new\nhuman=old\nunchanged_tail\n"
        self.target.write_bytes(before)
        change = self.engine.edit(
            "sample.txt", agent_after, "agent-a", self.engine.read("sample.txt")["sha256"],
        )
        human_current = b"agent=new\nhuman=new\nunchanged_tail\n"
        self.target.write_bytes(human_current)
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "conflict")
        self.assertNotIn("token", preview)
        self.assertEqual(self.target.read_bytes(), human_current)

    def test_unicode_line_separators_cannot_make_multiline_paths(self):
        for separator in ("\u2028", "\u2029"):
            with self.subTest(separator=ascii(separator)):
                with self.assertRaises(UndoWeaveError):
                    self.engine.read("name" + separator + "spoofed-header.txt")

    def test_unicode_line_separators_cannot_make_multiline_labels(self):
        original = self.target.read_bytes()
        for index, separator in enumerate(("\u2028", "\u2029")):
            with self.subTest(separator=ascii(separator)):
                with self.assertRaises(UndoWeaveError):
                    self.engine.edit(
                        "sample.txt", f"candidate-{index}\n", "agent-a",
                        self.engine.read("sample.txt")["sha256"], label="one" + separator + "two",
                    )
        self.assertEqual(self.target.read_bytes(), original)

    def test_changed_identity_marker_blocks_existing_and_new_clients(self):
        preview = self.engine.preview_undo(self.change["id"])
        (self.root / ".undoweave-id").write_bytes((uuid.uuid4().hex + "\n").encode("ascii"))
        actions = (
            lambda: self.engine.read("sample.txt"), self.engine.history, self.engine.status,
            lambda: self.engine.preview_undo(self.change["id"]),
            lambda: self.engine.apply_undo(preview["token"]),
            lambda: Engine(self.root, self.state),
        )
        for action in actions:
            with self.subTest(action=repr(action)):
                with self.assertRaises(UndoWeaveError):
                    action()
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")

    def test_missing_identity_marker_is_not_silently_regenerated(self):
        marker = self.root / ".undoweave-id"
        marker.unlink()
        for action in (lambda: self.engine.read("sample.txt"), lambda: Engine(self.root, self.state)):
            with self.assertRaises(UndoWeaveError):
                action()
        self.assertFalse(marker.exists())
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")

    def test_identity_marker_is_not_an_editable_or_readable_project_file(self):
        marker = self.root / ".undoweave-id"
        original = marker.read_bytes()
        with self.assertRaises(UndoWeaveError):
            self.engine.read(".undoweave-id")
        with self.assertRaises(UndoWeaveError):
            self.engine.edit(".undoweave-id", uuid.uuid4().hex + "\n", "agent-a", sha256(original).hexdigest())
        self.assertEqual(marker.read_bytes(), original)

    def make_legacy_journal(self, *, pending=False):
        """Build the actual v0.1 schema independently of the current Engine."""
        root = self.base / "legacy-project"
        root.mkdir()
        state = self.base / "legacy-state"
        state.mkdir(mode=0o700)
        path = state / "journal.sqlite3"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        before, after = "legacy original\n", "legacy after\n"
        change_id, token = uuid.uuid4().hex, uuid.uuid4().hex + uuid.uuid4().hex
        before_hash, after_hash = sha256(before.encode()).hexdigest(), sha256(after.encode()).hexdigest()
        (root / "legacy.txt").write_bytes(after.encode())
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE changes (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    path TEXT NOT NULL, path_key TEXT NOT NULL, actor TEXT NOT NULL, label TEXT NOT NULL,
                    before_text TEXT, after_text TEXT, before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, undoes TEXT, undone_by TEXT);
                CREATE TABLE plans (
                    token TEXT PRIMARY KEY, edit_id TEXT NOT NULL, current_hash TEXT NOT NULL,
                    result_text TEXT, created REAL NOT NULL, head INTEGER NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0);
            """)
            with db:
                db.executemany("INSERT INTO metadata VALUES (?,?)", [("root", str(root.resolve())), ("schema", "1")])
                db.execute("INSERT INTO changes (id,path,path_key,actor,label,before_text,after_text,before_hash,after_hash,status,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (change_id, "legacy.txt", "legacy.txt", "legacy-agent", "preserved label", before, after,
                            before_hash, after_hash, "pending" if pending else "applied", time.time()))
                db.execute("INSERT INTO plans (token,edit_id,current_hash,result_text,created,head) VALUES (?,?,?,?,?,?)",
                           (token, change_id, after_hash, before, time.time(), 1))
        return root, state, change_id, token

    def test_legacy_nonempty_journal_requires_explicit_local_adoption(self):
        root, state, _change_id, _token = self.make_legacy_journal()
        with self.assertRaises(UndoWeaveError) as caught:
            Engine(root, state)
        self.assertEqual(caught.exception.code, "legacy_journal")
        self.assertFalse((root / ".undoweave-id").exists())
        self.assertEqual((root / "legacy.txt").read_bytes(), b"legacy after\n")
        with closing(sqlite3.connect(state / "journal.sqlite3")) as db:
            self.assertEqual(dict(db.execute("SELECT key,value FROM metadata"))["schema"], "1")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 1)

    def test_explicit_legacy_adoption_preserves_history_and_invalidates_old_tokens(self):
        root, state, change_id, token = self.make_legacy_journal()
        result = Engine.adopt_legacy(root, state, confirm_root=str(root.resolve()))
        self.assertEqual(result["status"], "adopted")
        migrated = Engine(root, state)
        original_history = migrated.history()
        self.assertEqual(len(original_history), 1)
        self.assertEqual(original_history[0]["id"], change_id)
        self.assertEqual(original_history[0]["label"], "preserved label")
        with self.assertRaises(UndoWeaveError):
            migrated.apply_undo(token)
        self.assertEqual((root / "legacy.txt").read_bytes(), b"legacy after\n")
        preview = migrated.preview_undo(change_id)
        self.assertEqual(preview["status"], "ready")
        migrated.apply_undo(preview["token"])
        self.assertEqual((root / "legacy.txt").read_bytes(), b"legacy original\n")

    def test_schema_two_workspace_mismatch_cannot_use_legacy_adoption(self):
        self.replace_project_directory()
        with self.assertRaises(UndoWeaveError):
            Engine.adopt_legacy(self.root, self.state, confirm_root=str(self.root.resolve()))
        self.assertFalse((self.root / ".undoweave-id").exists())
        self.assertEqual(self.target.read_bytes(), b"shared after text\n")

    def test_legacy_pending_mutation_cannot_be_adopted(self):
        root, state, _change_id, _token = self.make_legacy_journal(pending=True)
        with self.assertRaises(UndoWeaveError) as caught:
            Engine.adopt_legacy(root, state, confirm_root=str(root.resolve()))
        self.assertEqual(caught.exception.code, "recovery_required")
        self.assertFalse((root / ".undoweave-id").exists())
        self.assertEqual((root / "legacy.txt").read_bytes(), b"legacy after\n")

    def test_legacy_adoption_requires_exact_resolved_root_confirmation(self):
        root, state, _change_id, _token = self.make_legacy_journal()
        with self.assertRaises(UndoWeaveError) as caught:
            Engine.adopt_legacy(root, state, confirm_root=str(root.resolve()) + "-different")
        self.assertEqual(caught.exception.code, "confirmation_required")
        self.assertFalse((root / ".undoweave-id").exists())

    def test_large_file_previews_do_not_duplicate_source_or_exhaust_small_budget(self):
        # v0.1 stored a full result in every preview: two previews of this file
        # consumed almost a 1 MiB journal, and even expired previews locked it out.
        with patch.object(engine_module, "MAX_STATE_BYTES", 1_048_576):
            root = self.base / "budget-project"
            root.mkdir()
            state = self.base / "budget-state"
            engine = Engine(root, state)
            before = "header=old\n" + "".join(f"padding_{i}\n" for i in range(12)) + "payload=" + "x" * 220000 + "\n"
            after = before.replace("header=old", "header=new")
            target = root / "large.txt"
            target.write_bytes(before.encode())
            change = engine.edit("large.txt", after, "agent-a", engine.read("large.txt")["sha256"])
            previews = [engine.preview_undo(change["id"]) for _ in range(12)]
            self.assertTrue(all(preview["status"] == "ready" for preview in previews))
            self.assertLessEqual((state / "journal.sqlite3").stat().st_size, 1_048_576)
            future = time.time() + 1000
            with patch.object(engine_module.time, "time", return_value=future):
                fresh = engine.preview_undo(change["id"])
                self.assertEqual(fresh["status"], "ready")
                with self.assertRaises(UndoWeaveError):
                    engine.apply_undo(previews[0]["token"])
            self.assertEqual(target.read_bytes(), after.encode())

    def test_storage_pressure_does_not_leave_an_unrecoverable_completed_undo(self):
        # SQLite may need transient overflow pages while updating linkage on an
        # old large record. Refuse before the file write if there is not room,
        # or complete with a journal that can still be reopened consistently.
        with patch.object(engine_module, "MAX_STATE_BYTES", 1_048_576):
            root = self.base / "write-budget-project"
            root.mkdir()
            state = self.base / "write-budget-state"
            engine = Engine(root, state)
            before = "header=old\n" + "".join(f"padding_{i}\n" for i in range(12)) + "payload=" + "x" * 220000 + "\n"
            after = before.replace("header=old", "header=new")
            target = root / "large.txt"
            target.write_bytes(before.encode())
            change = engine.edit("large.txt", after, "agent-a", engine.read("large.txt")["sha256"])
            preview = engine.preview_undo(change["id"])
            try:
                engine.apply_undo(preview["token"])
            except UndoWeaveError:
                self.assertIn(target.read_bytes(), (before.encode(), after.encode()))
                resumed = Engine(root, state)
                self.assertEqual(resumed.status()["pending_count"], 0)
            else:
                self.assertEqual(target.read_bytes(), before.encode())
                self.assertEqual(Engine(root, state).status()["pending_count"], 0)

    def test_no_final_newline_preview_keeps_added_and_removed_lines_separate(self):
        self.target.write_bytes(b"before")
        change = self.engine.edit("sample.txt", "after", "agent-a", self.engine.read("sample.txt")["sha256"])
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "ready")
        self.assertIn("\n-after\n", preview["diff"])
        self.assertIn("\n+before\n", preview["diff"])
        self.assertIn("No newline at end of file", preview["diff"])
        self.engine.apply_undo(preview["token"])
        self.assertEqual(self.target.read_bytes(), b"before")


if __name__ == "__main__":
    unittest.main()
