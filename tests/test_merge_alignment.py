"""Selective-undo alignment regressions with repeated lines.

History is generated as recorded events on lines with stable identities:
``before --A--> after --B--> current``, where A is the recorded edit to undo and B
is a later human or agent edit, each a single block replacement. The oracle
never calls difflib or the merge implementation.

Contract checked for every case: ``inverse_merge`` either raises MergeConflict
or returns EXACTLY the intended bytes, i.e. ``current`` with A's block restored
and B kept. Refusal is always acceptable; wrong accepted bytes never are.

When identical text makes several histories indistinguishable (SECURITY.md),
an accepted result is also allowed if it is the exact intended result of some
other single-block history that produces the same three texts. That fallback is
only consulted after the stable-ID answer did not match, and it never allows an
output for overlapping edits.

UNDOWEAVE_MERGE_CASES scales the seeded random scenarios (default 60000; CI may
use e.g. 1000000). The exhaustive small-alphabet sweep always runs.
"""

from __future__ import annotations

from itertools import product
import os
from pathlib import Path
import random
import tempfile
import unittest

from undoweave import merge as merge_module
from undoweave.engine import Engine

MergeConflict = merge_module.MergeConflict
RANDOM_CASES = int(os.environ.get("UNDOWEAVE_MERGE_CASES", "60000"))
FAILURES_SHOWN = 5


# --------------------------------------------------------------------------- oracle

def replace_block(lines, start, end, texts, actor, counter):
    """Apply one recorded block event to (identity, text) lines."""
    new = [(f"{actor}-{next(counter)}", text) for text in texts]
    return lines[:start] + new + lines[end:]


def text_of(lines):
    return "".join(text for _identity, text in lines)


def _allowed_outputs(before_block, after_lines, a_start, a_end, current_lines, b_start, b_end):
    """Intended undo results for one interpretation, or None if A and B overlap.

    Coordinates: A replaced ``before_block`` with after[a_start:a_end]; B replaced
    after[b_start:b_end]. Lines are (identity, text) pairs.
    """
    if a_start < a_end:
        if b_start < a_end and a_start < b_end:
            return None  # B rewrote or removed lines that A wrote.
    elif b_start < a_start < b_end:
        return None  # B replaced a span strictly containing A's deletion point.
    current_ids = [identity for identity, _ in current_lines]
    if a_start < a_end:
        written = [identity for identity, _ in after_lines[a_start:a_end]]
        first = current_ids.index(written[0])
        if current_ids[first:first + len(written)] != written:
            return None
        cuts = {(first, first + len(written))}
    else:
        cuts = set()
        if a_start > 0 and after_lines[a_start - 1][0] in current_ids:
            point = current_ids.index(after_lines[a_start - 1][0]) + 1
            cuts.add((point, point))
        if a_start < len(after_lines) and after_lines[a_start][0] in current_ids:
            point = current_ids.index(after_lines[a_start][0])
            cuts.add((point, point))
        if not cuts:  # Empty file sides: only possible at BOF/EOF.
            cuts.add((0, 0) if a_start == 0 else (len(current_ids), len(current_ids)))
    restored = "".join(before_block)
    texts = [text for _identity, text in current_lines]
    return {"".join(texts[:start]) + restored + "".join(texts[end:]) for start, end in cuts}


def _blocks(old, new):
    """Every single-block replacement (start, end, new_end) turning old into new."""
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    found = []
    for start in range(prefix + 1):
        for tail in range(suffix + 1):
            if start + tail <= min(len(old), len(new)):
                found.append((start, len(old) - tail, len(new) - tail))
    return found


def indistinguishable_outputs(before, after, current):
    """Union of intended results over all single-block histories with these texts."""
    outputs = set()
    for a_start, a_old_end, a_end in _blocks(before, after):
        after_lines = [(f"t{i}", text) for i, text in enumerate(after)]
        for b_start, b_end, b_new_end in _blocks(after, current):
            current_lines = (after_lines[:b_start]
                             + [(f"h{i}", text) for i, text in enumerate(current[b_start:b_new_end])]
                             + after_lines[b_end:])
            allowed = _allowed_outputs(before[a_start:a_old_end], after_lines, a_start, a_end,
                                       current_lines, b_start, b_end)
            if allowed:
                outputs |= allowed
    return outputs


class Case:
    __slots__ = ("before", "after", "current", "allowed", "label", "valid")

    def __init__(self, base_texts, a_event, b_event, label=""):
        counter = iter(range(10**9))
        base = [(f"o-{i}", text) for i, text in enumerate(base_texts)]
        a_start, a_end, a_texts = a_event
        after_lines = replace_block(base, a_start, a_end, a_texts, "agent-a", counter)
        b_start, b_end, b_texts = b_event
        current_lines = replace_block(after_lines, b_start, b_end, b_texts, "later", counter)
        self.before, self.after, self.current = text_of(base), text_of(after_lines), text_of(current_lines)
        self.label = label
        # A final line without a newline must stay final, or texts would merge lines.
        self.valid = all(text.endswith("\n") for seq in (base, after_lines, current_lines) for _i, text in seq[:-1])
        if self.current == self.after:
            self.allowed = {self.before}
        else:
            self.allowed = _allowed_outputs(
                [text for _i, text in base[a_start:a_end]], after_lines, a_start, a_start + len(a_texts),
                current_lines, b_start, b_end) or set()

    def check(self):
        """Return None if the merge honoured the contract, else a failure description."""
        if self.before == self.after:
            return None
        try:
            result = merge_module.inverse_merge(self.before, self.after, self.current)
        except MergeConflict:
            if self.current == self.after:
                return f"refused the unchanged-file undo {self!r}"
            return None
        if result in self.allowed:
            return None
        if self.current != self.after:
            fallback = indistinguishable_outputs(
                self.before.splitlines(keepends=True), self.after.splitlines(keepends=True),
                self.current.splitlines(keepends=True))
            if result in fallback:
                return None
        return f"accepted wrong bytes {result!r}; allowed {sorted(self.allowed)!r}; {self!r}"

    def __repr__(self):
        return f"Case({self.label} before={self.before!r} after={self.after!r} current={self.current!r})"


def run_cases(test, cases):
    failures, count = [], 0
    for case in cases:
        count += 1
        problem = case.check()
        if problem:
            failures.append(problem)
    if failures:
        test.fail(f"{len(failures)} of {count} cases violated the merge contract:\n  "
                  + "\n  ".join(failures[:FAILURES_SHOWN]))
    return count


# --------------------------------------------------------------------------- generators

def single_line_events(lines, alphabet):
    """Every one-line replace, insert, and delete event on ``lines``."""
    for position in range(len(lines)):
        for text in alphabet:
            if text != lines[position]:
                yield (position, position + 1, [text])
        yield (position, position + 1, [])
    for position in range(len(lines) + 1):
        for text in alphabet:
            yield (position, position, [text])


def exhaustive_cases(alphabet, max_length):
    for length in range(1, max_length + 1):
        for base in product(alphabet, repeat=length):
            base = list(base)
            for a_event in single_line_events(base, alphabet):
                after = base[:a_event[0]] + a_event[2] + base[a_event[1]:]
                for b_event in single_line_events(after, alphabet):
                    case = Case(base, a_event, b_event, "exhaustive")
                    if case.valid:
                        yield case


REPEATED = ["}\n", "\n", "{\n", "    return x\n", "a\n", "b\n", "end\n"]


def random_cases(seed, count):
    rng = random.Random(seed)
    for _ in range(count):
        alphabet = rng.sample(REPEATED, rng.randint(2, 6))
        ending = rng.choice(["\n", "\n", "\r\n"])
        alphabet = [text.replace("\n", ending) for text in alphabet]
        base = [rng.choice(alphabet) for _ in range(rng.randint(1, 18))]
        if rng.random() < 0.2:
            base[-1] = base[-1].rstrip("\r\n") or "tail"  # final line without newline
        a_start = rng.randint(0, len(base))
        a_end = min(len(base), a_start + rng.randint(0, 3))
        a_texts = [rng.choice(alphabet) for _ in range(rng.randint(0, 3))]
        after_length = len(base) - (a_end - a_start) + len(a_texts)
        a_stop = a_start + len(a_texts)
        mode = rng.random()
        if mode < 0.35:  # later insert at a moving position near A's block
            b_start = max(0, min(after_length, rng.choice([a_start - 2, a_start - 1, a_start, a_stop, a_stop + 1, a_stop + 2])))
            b_end = b_start
        elif mode < 0.7:  # anywhere
            b_start = rng.randint(0, after_length)
            b_end = min(after_length, b_start + rng.randint(0, 3))
        else:  # separated by at least one line, on either side
            if rng.random() < 0.5 and a_start >= 1:
                b_start = rng.randint(0, a_start - 1)
                b_end = min(a_start - 1, b_start + rng.randint(0, 2))
            elif a_stop + 1 <= after_length:
                b_start = rng.randint(a_stop + 1, after_length)
                b_end = min(after_length, b_start + rng.randint(0, 2))
            else:
                b_start = b_end = 0
        # Later edits often reuse the very text A wrote (the original v0.1 failure).
        pool = alphabet + a_texts
        b_texts = [rng.choice(pool) for _ in range(rng.randint(0, 3))]
        case = Case(base, (a_start, a_end, a_texts), (b_start, b_end, b_texts), f"seed={seed}")
        if case.valid:
            yield case


# Each was accepted with wrong bytes by v0.1.0's SequenceMatcher inverse merge.
COUNTEREXAMPLES = [
    # before, A=(start, end, texts), B=(start, end, texts) in after coordinates
    (["b\n", "}\n", "b\n", "}\n"], (1, 2, ["a\n"]), (3, 3, ["a\n"])),
    (["\n", "}\n", "b\n", "\n", "a\n", "b\n", "\n"], (1, 5, []), (2, 2, ["b\n"])),
    (["b\n", "c\n", "{\n", "a\n", "b\n", "}\n", "c\n", "\n", "b\n"], (8, 8, ["\n"]), (5, 7, ["c\n", "\n", "\n", "b\n"])),
    (["{\n", "}\n", "{\n", "}\n", "a\n", "a\n", "\n"], (2, 5, ["a\n", "c\n", "c\n", "a\n", "\n", "a\n", "a\n", "a\n", "a\n", "\n", "c\n", "\n", "b\n", "\n"]),
     (1, 1, ["a\n", "c\n", "c\n", "a\n", "\n", "a\n", "a\n", "a\n", "a\n", "\n", "c\n", "\n", "b\n", "\n"])),
    (["}\n", "a\n", "b\n", "a\n", "\n", "}\n", "{\n", "c\n", "}\n", "}\n", "b\n", "c\n", "}\n"],
     (6, 12, ["c\n", "a\n", "c\n"]), (9, 9, ["a\n", "\n", "\n", "a\n", "c\n"])),
]


# --------------------------------------------------------------------------- tests

class OracleSelfTests(unittest.TestCase):
    """The oracle itself, on hand-checked histories (no merge involved)."""

    def test_separated_insert_after_replacement(self):
        case = Case(["b\n", "}\n", "b\n", "}\n"], (1, 2, ["a\n"]), (3, 3, ["a\n"]))
        self.assertEqual(case.after, "b\na\nb\n}\n")
        self.assertEqual(case.current, "b\na\nb\na\n}\n")
        self.assertEqual(case.allowed, {"b\n}\nb\na\n}\n"})

    def test_overlap_allows_nothing(self):
        case = Case(["x\n", "y\n", "z\n"], (1, 2, ["Y\n"]), (1, 2, ["H\n"]))
        self.assertEqual(case.allowed, set())

    def test_deletion_point_with_insert_allows_both_orders(self):
        case = Case(["p\n", "d\n", "q\n"], (1, 2, []), (1, 1, ["h\n"]))
        self.assertEqual(case.allowed, {"p\nd\nh\nq\n", "p\nh\nd\nq\n"})

    def test_unchanged_file_requires_exact_before(self):
        case = Case(["p\n", "q\n"], (1, 2, ["Q\n"]), (0, 1, ["p\n"]))  # textual no-op later edit
        self.assertEqual(case.allowed, {"p\nq\n"})


class AlignmentRegressionTests(unittest.TestCase):
    def test_v010_counterexamples_refuse_or_restore_exactly(self):
        cases = [Case(base, a, b, f"counterexample-{index}") for index, (base, a, b) in enumerate(COUNTEREXAMPLES)]
        for case in cases:
            self.assertNotEqual(case.allowed, set(), case)  # all are separated histories
        run_cases(self, cases)

    def test_minimal_crosswise_case_is_never_misapplied(self):
        before, after, current = "b\n}\nb\n}\n", "b\na\nb\n}\n", "b\na\nb\na\n}\n"
        try:
            result = merge_module.inverse_merge(before, after, current)
        except MergeConflict:
            return
        self.assertEqual(result, "b\n}\nb\na\n}\n")

    def test_exhaustive_three_symbol_single_line_events(self):
        count = run_cases(self, exhaustive_cases(["a\n", "b\n", "}\n"], 4))
        self.assertGreater(count, 50_000)

    def test_exhaustive_two_symbols_with_missing_final_newline(self):
        cases = (case for case in exhaustive_cases(["a\n", "b\n", "a"], 4) if case.valid)
        self.assertGreater(run_cases(self, cases), 5_000)

    def test_seeded_repetitive_block_events(self):
        run_cases(self, random_cases(20261031, RANDOM_CASES))

    def test_unchanged_current_restores_before_exactly(self):
        for base, a, _b in COUNTEREXAMPLES:
            case = Case(base, a, (0, 0, []))
            self.assertEqual(merge_module.inverse_merge(case.before, case.after, case.after), case.before)


class RecordedEngineAlignmentTests(unittest.TestCase):
    """The same contract through recorded Engine events, preview, and apply."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="undoweave-alignment-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "project"
        self.root.mkdir()
        self.engine = Engine(self.root, Path(self.directory.name) / "state")

    def replay(self, name, case, later_is_recorded):
        target = self.root / name
        target.write_bytes(case.before.encode("utf-8"))
        read = self.engine.read(name)
        edit = self.engine.edit(name, case.after, actor="agent-a", expected_hash=read["sha256"])
        if later_is_recorded:
            read = self.engine.read(name)
            self.engine.edit(name, case.current, actor="agent-b", expected_hash=read["sha256"])
        else:
            target.write_bytes(case.current.encode("utf-8"))  # human edit, not recorded
        preview = self.engine.preview_undo(edit["id"])
        if preview["status"] != "ready":
            self.assertEqual(preview["status"], "conflict", case)
            self.assertNotIn("token", preview)
            self.assertEqual(target.read_bytes(), case.current.encode("utf-8"), case)
            return
        self.engine.apply_undo(preview["token"])
        result = target.read_bytes().decode("utf-8")
        if result not in case.allowed:
            fallback = indistinguishable_outputs(
                case.before.splitlines(keepends=True), case.after.splitlines(keepends=True),
                case.current.splitlines(keepends=True))
            self.assertIn(result, fallback, f"wrong bytes written for {case!r}")

    def test_counterexamples_through_preview_and_apply(self):
        for index, (base, a, b) in enumerate(COUNTEREXAMPLES):
            case = Case(base, a, b, f"counterexample-{index}")
            for recorded in (False, True):
                with self.subTest(index=index, recorded=recorded):
                    self.replay(f"c{index}-{int(recorded)}.txt", case, recorded)

    def test_seeded_recorded_and_human_later_edits(self):
        cases = [case for case in random_cases(7, 400) if case.current != case.after and case.before != case.after][:40]
        for index, case in enumerate(cases):
            with self.subTest(index=index):
                self.replay(f"r{index}.txt", case, later_is_recorded=index % 2 == 0)


if __name__ == "__main__":
    unittest.main()
