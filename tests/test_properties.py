"""Randomized selective-undo oracles, independent of the merge algorithm.

The oracle gives each original line an identity and replays *specified* edits.
It never calls difflib, reconstructs the merge implementation, or assumes that
an implementation must accept an ambiguous merge. Refusal is safe; accepting a
merge must produce the exact expected bytes, including everyone else's edits.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from undoweave.engine import Engine, UndoWeaveError

try:
    from hypothesis import given, settings, strategies as st
except ImportError:
    st = None


def render(lines):
    return "".join(value for _identity, value in lines)


def apply_event(lines, actor, kind, target, value):
    """Apply a specified event to stable line IDs, not byte/line offsets."""
    result = list(lines)
    index = next(index for index, (identity, _text) in enumerate(result)
                 if identity == f"original-{target}")
    if kind == "replace":
        result[index] = (f"original-{target}", value)
    elif kind == "delete":
        result.pop(index)
    elif kind == "insert":
        result.insert(index, (f"inserted-{actor}", value))
    else:
        raise AssertionError(f"invalid oracle event {kind!r}")
    return result


if st is None:
    class PropertyTests(unittest.TestCase):
        @unittest.skip("install the 'dev' extra for Hypothesis property checks")
        def test_hypothesis_optional_dependency(self):
            pass
else:
    PAYLOAD = st.text(alphabet="abcXYZ012 é中🧵", min_size=1, max_size=16)
    ENDING = st.sampled_from(["\n", "\r\n"])
    KIND = st.sampled_from(["replace", "delete", "insert"])

    @st.composite
    def separated_events(draw):
        count = draw(st.integers(min_value=12, max_value=30))
        # These three regions leave intact context around each selected line.
        slots = [draw(st.integers(1, 3)), draw(st.integers(6, 8)), draw(st.integers(10, count - 1))]
        targets = draw(st.permutations(slots))
        kinds = draw(st.tuples(KIND, KIND, KIND))
        payload = draw(PAYLOAD)
        ending = draw(ENDING)
        return count, targets, kinds, payload, ending

    @st.composite
    def repeated_events(draw):
        count = draw(st.integers(min_value=3, max_value=16))
        text = draw(st.lists(st.sampled_from(["red", "blue", "", "same"]), min_size=count, max_size=count))
        targets = draw(st.lists(st.integers(0, count - 1), min_size=3, max_size=3, unique=True))
        kinds = draw(st.tuples(KIND, KIND, KIND))
        ending = draw(ENDING)
        return text, targets, kinds, ending

    class PropertyTests(unittest.TestCase):
        def workspace(self):
            directory = tempfile.TemporaryDirectory(prefix="undoweave-property-")
            root = Path(directory.name) / "project"
            root.mkdir()
            engine = Engine(root, Path(directory.name) / "state")
            return directory, root, engine

        def record(self, engine, content, actor):
            read = engine.read("file.txt")
            return engine.edit("file.txt", content, actor, read["sha256"])

        @settings(max_examples=80, deadline=None, derandomize=True)
        @given(case=separated_events())
        def test_three_actor_disjoint_events_match_exact_oracle(self, case):
            count, targets, kinds, payload, ending = case
            base = [(f"original-{i}", f"line-{i}:{payload}{ending}") for i in range(count)]
            a, b, human = [
                (actor, kind, target, f"{actor}:{payload}{ending}")
                for actor, kind, target in zip(("agent-a", "agent-b", "human"), kinds, targets)
            ]
            with self.workspace()[0] as temporary:
                root = Path(temporary) / "project"
                engine = Engine(root, Path(temporary) / "state")
                target = root / "file.txt"
                target.write_bytes(render(base).encode("utf-8"))
                after_a = apply_event(base, *a)
                change_a = self.record(engine, render(after_a), "agent-a")
                after_b = apply_event(after_a, *b)
                self.record(engine, render(after_b), "agent-b")
                live = apply_event(after_b, *human)
                target.write_bytes(render(live).encode("utf-8"))
                expected = render(apply_event(apply_event(base, *b), *human)).encode("utf-8")
                preview = engine.preview_undo(change_a["id"])
                self.assertEqual(preview["status"], "ready", preview)
                engine.apply_undo(preview["token"])
                self.assertEqual(target.read_bytes(), expected)

        @settings(max_examples=250, deadline=None, derandomize=True)
        @given(case=repeated_events())
        def test_repeated_context_never_accepts_an_incorrect_oracle(self, case):
            text, targets, kinds, ending = case
            base = [(f"original-{i}", value + ending) for i, value in enumerate(text)]
            a, b, human = [
                (actor, kind, target, f"{actor}-unique{ending}")
                for actor, kind, target in zip(("agent-a", "agent-b", "human"), kinds, targets)
            ]
            with self.workspace()[0] as temporary:
                root = Path(temporary) / "project"
                engine = Engine(root, Path(temporary) / "state")
                target = root / "file.txt"
                target.write_bytes(render(base).encode("utf-8"))
                after_a = apply_event(base, *a)
                change_a = self.record(engine, render(after_a), "agent-a")
                after_b = apply_event(after_a, *b)
                self.record(engine, render(after_b), "agent-b")
                live = apply_event(after_b, *human)
                current = render(live).encode("utf-8")
                target.write_bytes(current)
                expected = render(apply_event(apply_event(base, *b), *human)).encode("utf-8")
                preview = engine.preview_undo(change_a["id"])
                self.assertEqual(target.read_bytes(), current, "preview modified the project")
                if preview["status"] == "ready":
                    engine.apply_undo(preview["token"])
                    self.assertEqual(target.read_bytes(), expected)
                else:
                    self.assertEqual(preview["status"], "conflict", preview)
                    self.assertFalse(preview.get("token"))
                    self.assertEqual(target.read_bytes(), current)

        @settings(max_examples=50, deadline=None, derandomize=True)
        @given(payload=PAYLOAD, ending=ENDING, position=st.integers(0, 7))
        def test_same_line_human_override_is_always_refused(self, payload, ending, position):
            base = [(f"original-{i}", f"line-{i}{ending}") for i in range(8)]
            after_a = apply_event(base, "agent-a", "replace", position, f"agent:{payload}{ending}")
            human = apply_event(after_a, "human", "replace", position, f"human:{payload}{ending}")
            with self.workspace()[0] as temporary:
                root = Path(temporary) / "project"
                engine = Engine(root, Path(temporary) / "state")
                target = root / "file.txt"
                target.write_bytes(render(base).encode())
                change = self.record(engine, render(after_a), "agent-a")
                before = render(human).encode()
                target.write_bytes(before)
                preview = engine.preview_undo(change["id"])
                self.assertEqual(preview["status"], "conflict")
                self.assertFalse(preview.get("token"))
                self.assertEqual(target.read_bytes(), before)

        @settings(max_examples=40, deadline=None, derandomize=True)
        @given(payload=PAYLOAD, ending=ENDING, creation=st.booleans())
        def test_creation_or_deletion_with_human_reappearance_refuses(self, payload, ending, creation):
            with self.workspace()[0] as temporary:
                root = Path(temporary) / "project"
                engine = Engine(root, Path(temporary) / "state")
                target = root / "file.txt"
                if creation:
                    change = self.record(engine, f"agent:{payload}{ending}", "agent-a")
                else:
                    target.write_bytes(f"original:{payload}{ending}".encode())
                    change = self.record(engine, None, "agent-a")
                current = f"human:{payload}{ending}".encode()
                target.write_bytes(current)
                preview = engine.preview_undo(change["id"])
                self.assertEqual(preview["status"], "conflict")
                self.assertFalse(preview.get("token"))
                self.assertEqual(target.read_bytes(), current)

        @settings(max_examples=40, deadline=None, derandomize=True)
        @given(payload=PAYLOAD, ending=ENDING)
        def test_recorded_aba_changes_invalidate_an_old_preview(self, payload, ending):
            with self.workspace()[0] as temporary:
                root = Path(temporary) / "project"
                engine = Engine(root, Path(temporary) / "state")
                target = root / "file.txt"
                target.write_bytes(f"original:{payload}{ending}".encode())
                current = f"agent-a:{payload}{ending}"
                change = self.record(engine, current, "agent-a")
                preview = engine.preview_undo(change["id"])
                self.assertEqual(preview["status"], "ready")
                self.record(engine, f"agent-b:{payload}{ending}", "agent-b")
                self.record(engine, current, "agent-b")
                before = target.read_bytes()
                with self.assertRaises(UndoWeaveError):
                    engine.apply_undo(preview["token"])
                self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
