"""Conservative three-way inverse partitioned by stable unique text anchors."""
from collections import Counter
import re


class MergeConflict(ValueError):
    """The inverse cannot be separated from later work without guessing."""


def physical_lines(text: str) -> list[str]:
    """LF, CRLF and bare CR are physical terminators; Unicode separators are data."""
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)


def inverse_merge(before: str, after: str, current: str) -> str:
    """Undo a recorded change only in regions unaffected by later edits.

    A stable anchor appears exactly once in all three snapshots, in the same
    order. If both the agent and later work changed a region between anchors,
    refuse the entire undo. Adjacent edits can therefore require manual review.
    """
    if current == after:
        return before
    if before == after:
        raise MergeConflict("The recorded edit has no changes to reverse.")
    snapshots = [physical_lines(text) for text in (before, after, current)]
    if max(map(len, snapshots)) > 4000:
        raise MergeConflict("This merge exceeds the 4000-line comparison limit.")
    old, base, live = snapshots
    counts = [Counter(lines) for lines in snapshots]
    positions = [{line: index for index, line in enumerate(lines)} for lines in snapshots]
    anchors = [(positions[0][line], index, positions[2][line])
               for index, line in enumerate(base)
               if all(count[line] == 1 for count in counts)]
    previous_old = previous_live = -1
    for old_index, _, live_index in anchors:
        if old_index <= previous_old or live_index <= previous_live:
            raise MergeConflict("Shared context moved or changed order; review this undo manually.")
        previous_old, previous_live = old_index, live_index
    result = []
    starts = (0, 0, 0)
    for ends in [*anchors, (len(old), len(base), len(live))]:
        original_region = old[starts[0]:ends[0]]
        agent_region = base[starts[1]:ends[1]]
        current_region = live[starts[2]:ends[2]]
        if original_region != agent_region:
            if current_region != agent_region:
                raise MergeConflict("Both edits touch a region without a stable unique separator. The file was left untouched.")
            result.extend(original_region)
        else:
            result.extend(current_region)
        if ends[1] < len(base):
            result.append(base[ends[1]])
        starts = tuple(index + 1 for index in ends)
    return "".join(result)
