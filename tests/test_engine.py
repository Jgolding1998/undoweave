from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import tempfile
import unittest

from undoweave.engine import Engine, UndoWeaveError
from undoweave.merge import inverse_merge, MergeConflict


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.engine = Engine(self.root, self.base / "state")

    def put(self, text, name="app.txt"):
        (self.root / name).write_bytes(text.encode())

    def edit(self, text, actor="agent-a", name="app.txt"):
        return self.engine.edit(name, text, actor, self.engine.read(name)["sha256"])

    def test_two_agents_and_human_interleaved(self):
        self.put("a=0\nseparator-a\nb=0\nseparator-b\nhuman=0\n")
        a = self.edit("a=1\nseparator-a\nb=0\nseparator-b\nhuman=0\n")
        b = self.edit("a=1\nseparator-a\nb=1\nseparator-b\nhuman=0\n", actor="agent-b")
        self.put("a=1\nseparator-a\nb=1\nseparator-b\nhuman=1\n")
        self.engine.apply_undo(self.engine.preview_undo(a["id"])["token"])
        self.assertEqual(self.engine.read("app.txt")["content"], "a=0\nseparator-a\nb=1\nseparator-b\nhuman=1\n")
        self.engine.apply_undo(self.engine.preview_undo(b["id"])["token"])
        self.assertEqual(self.engine.read("app.txt")["content"], "a=0\nseparator-a\nb=0\nseparator-b\nhuman=1\n")

    def test_create_delete_empty_and_redo(self):
        created = self.edit("", name="empty.txt")
        undone = self.engine.apply_undo(self.engine.preview_undo(created["id"])["token"])
        self.assertFalse((self.root / "empty.txt").exists())
        self.engine.apply_undo(self.engine.preview_undo(undone["id"])["token"])
        self.assertEqual((self.root / "empty.txt").read_bytes(), b"")

    def test_deleted_file_recreated_by_human_conflicts(self):
        self.put("original\n")
        change = self.edit(None)
        self.put("human replacement\n")
        self.assertEqual(self.engine.preview_undo(change["id"])["status"], "conflict")
        self.assertEqual((self.root / "app.txt").read_bytes(), b"human replacement\n")

    def test_no_newline_and_bom_roundtrip(self):
        original = "\ufefftitle\r\none\r\nlast"
        self.put(original)
        change = self.edit(original.replace("one", "two"))
        self.engine.apply_undo(self.engine.preview_undo(change["id"])["token"])
        self.assertEqual((self.root / "app.txt").read_bytes(), original.encode())

    def test_cooperating_writers_share_lock_and_only_one_hash_wins(self):
        self.put("start\n")
        expected = self.engine.read("app.txt")["sha256"]
        def attempt(index):
            engine = Engine(self.root, self.base / "state")
            try:
                return engine.edit("app.txt", f"writer-{index}\n", f"agent-{index}", expected)["status"]
            except UndoWeaveError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(attempt, range(4)))
        self.assertEqual(outcomes.count("applied"), 1)
        self.assertEqual(outcomes.count("stale_file"), 3)
        self.assertEqual(len(self.engine.history()), 1)
        self.assertEqual(self.engine.status()["pending_count"], 0)

    def test_large_diff_is_not_an_applyable_truncated_preview(self):
        before = "a" * 20000
        self.put(before)
        change = self.edit("b" * 20000)
        result = self.engine.preview_undo(change["id"])
        self.assertEqual(result["status"], "conflict")
        self.assertNotIn("token", result)

    def test_no_whole_tree_scan(self):
        # Unselected files may be binary, oversized, symlinked, or unreadable.
        # They must never be inspected for an explicitly selected edit.
        (self.root / "unselected.bin").write_bytes(b"\0" * 1_100_000)
        self.put("old\n")
        change = self.edit("new\n")
        self.engine.apply_undo(self.engine.preview_undo(change["id"])["token"])
        self.assertEqual((self.root / "unselected.bin").stat().st_size, 1_100_000)

    def test_control_and_direction_characters_are_visible_in_preview(self):
        original = "old\u202e\x1b[31m\r\n"
        self.put(original)
        change = self.edit("new\r\n")
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "ready")
        self.assertTrue(preview["display_escaped"])
        self.assertNotIn("\u202e", preview["diff"])
        self.assertNotIn("\x1b", preview["diff"])
        self.assertNotIn("\r", preview["diff"])
        self.engine.apply_undo(preview["token"])
        self.assertEqual((self.root / "app.txt").read_bytes(), original.encode())

    def test_control_and_direction_metadata_refuses_without_mutation(self):
        self.put("old\n")
        for value in ("hidden\u202e.txt", "hidden\x85.txt"):
            with self.assertRaises(UndoWeaveError):
                self.engine.read(value)
        for value in ("label\u202e", "label\x85"):
            with self.assertRaises(UndoWeaveError):
                self.engine.edit("app.txt", "new\n", "agent", self.engine.read("app.txt")["sha256"], label=value)
        self.assertEqual((self.root / "app.txt").read_bytes(), b"old\n")


class MergeOracleTests(unittest.TestCase):
    def test_1000_generated_separated_replacements_preserve_exact_oracle(self):
        rng = random.Random(20261031)
        for case in range(1000):
            n = rng.randrange(5, 80)
            ending = rng.choice(["\n", "\r\n"])
            original = [f"line_{case}_{i}_original{ending}" for i in range(n)]
            agent_index = rng.randrange(n)
            human_index = rng.choice([index for index in range(n) if abs(index - agent_index) > 1])
            after = original.copy()
            after[agent_index] = f"agent_changed_{case}{ending}"
            current = after.copy()
            current[human_index] = f"human_changed_{case}{ending}"
            expected = original.copy()
            expected[human_index] = current[human_index]
            self.assertEqual(inverse_merge("".join(original), "".join(after), "".join(current)), "".join(expected), case)

    def test_disjoint_insert_delete_and_index_shift(self):
        self.assertEqual(inverse_merge("a\nb\nc\nd\ne\n", "a\nAGENT\nb\nc\nd\ne\n", "HUMAN\na\nAGENT\nb\nc\nd\ne\n"), "HUMAN\na\nb\nc\nd\ne\n")
        self.assertEqual(inverse_merge("a\nb\nc\nd\ne\n", "a\nb\nd\ne\n", "HUMAN\na\nb\nd\ne\n"), "HUMAN\na\nb\nc\nd\ne\n")

    def test_overlap_is_refused(self):
        with self.assertRaises(MergeConflict):
            inverse_merge("a\nb\nc\n", "a\nAGENT\nc\n", "a\nHUMAN\nc\n")

    def test_adjacent_edits_without_shared_separator_are_refused(self):
        with self.assertRaises(MergeConflict):
            inverse_merge("agent=0\nhuman=0\n", "agent=1\nhuman=0\n", "agent=1\nhuman=1\n")

    def test_ambiguous_repeated_blocks_are_refused(self):
        with self.assertRaises(MergeConflict):
            inverse_merge("old\n" + "x\n" * 30, "x\n" * 31, "x\n" * 31 + "human\n")

    def test_repeated_text_budget_refuses_without_unbounded_comparison(self):
        with self.assertRaises(MergeConflict):
            inverse_merge("a\n" * 2000, "b\n" + "a\n" * 1999, "b\n" + "a\n" * 1998 + "human\n")


if __name__ == "__main__":
    unittest.main()
