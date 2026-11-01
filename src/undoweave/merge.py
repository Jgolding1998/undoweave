"""Small, conservative line-based inverse merge. No fuzzy patch application."""

from collections import Counter
from difflib import SequenceMatcher


class MergeConflict(ValueError):
    """The inverse cannot be located without guessing."""


def _occurrences(haystack: list[str], needle: list[str]) -> list[int]:
    if not needle:
        return []
    return [i for i in range(len(haystack) - len(needle) + 1)
            if haystack[i:i + len(needle)] == needle]


def _overlap(a: tuple, b: tuple) -> bool:
    _, start, end, _, _ = a
    _, other_start, other_end, _, _ = b
    # Insertions on either boundary are deliberately treated as conflicts.
    if start == end:
        return other_start <= start <= other_end
    if other_start == other_end:
        return start <= other_start <= end
    return max(start, other_start) < min(end, other_end)


def inverse_merge(before: str, after: str, current: str) -> str:
    """Reverse before->after, preserving provably separate line edits.

    Refuses overlaps, ambiguous repeated context, and adjacent insertions. It
    cannot infer authorship of outside edits or the intent behind moved text.
    """
    if current == after:
        return before
    if before == after:
        raise MergeConflict("The recorded edit has no changes to reverse.")
    base = after.splitlines(keepends=True)
    wanted = before.splitlines(keepends=True)
    live = current.splitlines(keepends=True)
    if max(len(base), len(wanted), len(live)) > 4000:
        raise MergeConflict("This merge exceeds the 4000-line comparison limit.")
    # Bound repeated-line worst cases without a fuzzy alignment heuristic.
    for left, right in ((base, wanted), (base, live)):
        frequencies = Counter(left)
        if sum(frequencies[line] for line in right) > 1_000_000:
            raise MergeConflict("Repeated text exceeds the comparison budget; review this undo manually.")
    reverse = [op for op in SequenceMatcher(None, base, wanted, autojunk=False).get_opcodes()
               if op[0] != "equal"]
    later = [op for op in SequenceMatcher(None, base, live, autojunk=False).get_opcodes()
             if op[0] != "equal"]
    plans: list[tuple[int, int, list[str]]] = []
    for operation in reverse:
        _, start, end, target_start, target_end = operation
        if any(_overlap(operation, other) for other in later):
            raise MergeConflict("Later edits overlap this change; the file was left untouched.")
        shift = sum((d - c) - (b - a) for _, a, b, c, d in later if b < start or (b == start and a != b))
        live_start, live_end = start + shift, end + shift
        if live[live_start:live_end] != base[start:end]:
            raise MergeConflict("The original text no longer matches at its expected location.")
        # Require unique, intact surrounding context. This intentionally rejects
        # many valid merges rather than selecting the wrong copy of repeated text.
        located = False
        for context in range(0 if end > start else 1, 9):
            left, right = max(0, start - context), min(len(base), end + context)
            anchor = base[left:right]
            source_hits = _occurrences(base, anchor)
            current_hits = _occurrences(live, anchor)
            if source_hits == [left] and current_hits == [live_start - (start - left)]:
                located = True
                break
        if not located:
            raise MergeConflict("Repeated or changed context makes this undo ambiguous.")
        plans.append((live_start, live_end, wanted[target_start:target_end]))
    for start, end, replacement in reversed(plans):
        live[start:end] = replacement
    return "".join(live)
