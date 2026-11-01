# Measured comparison

UndoWeave aims to reverse one recorded text edit while preserving separate later
work. Existing tools may intentionally restore an entire checkpoint or require a
clean Git state. These are different behaviors, not a complete-product ranking.

Eight exposed synthetic fixtures were run on Windows with Python 3.13.11,
Node 24.13.0, and Git 2.52.0. No models or paid APIs were used.

| Fixture | UndoWeave | pi-rewind core | Aider undo method |
| --- | --- | --- | --- |
| Simple edit, no later changes | Undid the edit | Restored checkpoint | Undid commit |
| Later human edit on another line in the same file | Undid A, kept human line | Restored whole file to checkpoint | Safely refused, file unchanged |
| Later agent B edit on another line | Undid A, kept B's line | Restored whole file to checkpoint | Safely refused, file unchanged |
| Human overwrote the same line | Refused, file unchanged | Restored checkpoint | Safely refused, file unchanged |
| Human modified a file created by A | Refused, file unchanged | Removed file as part of checkpoint restore | Safely refused, file unchanged |
| Human created an unrelated new file | Undid A, kept human file | Removed new file as part of checkpoint restore | Undid commit, kept human file |
| Human recreated a path A had deleted | Refused, replacement unchanged | Restored checkpoint's original file | Method restored original over replacement |
| Folder without Git | Undid the edit | Unsupported | Unsupported |

All eight UndoWeave cases met the defined objective: five completed undos and
three refusals that left project files unchanged. That is a development check,
not a general accuracy estimate. No latency or reliability ranking is claimed.

## What actually ran

- **pi-rewind:** unmodified [src/core.ts at 91611ad](https://github.com/arpagon/pi-rewind/blob/91611ad87992fb7b635a41ba68f67916ff6e6ae3/src/core.ts),
  loaded directly by Node. Each supported fixture included a recovery checkpoint
  before restoration. The later work remained recoverable from that checkpoint;
  this comparison does **not** claim permanent loss. The Pi UI and event hooks
  were not tested.
- **Aider:** the exact [Commands.raw_cmd_undo method at 5dc9490](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/commands.py),
  extracted unchanged from the verified file's AST. It ran against real GitPython
  repositories with a small I/O and session-metadata adapter. The full Aider CLI,
  agent loop and other commands were not tested. Its dirty-file refusals are
  useful safety behavior, not failures to protect data.
- **UndoWeave:** the public Engine API. Agent A's write was recorded; agent B's
  write used a separate actor; human edits were written outside the engine.
  Preview and token-based apply performed each supported undo. State was outside
  the project. No Git commit is required by UndoWeave.

The deleted-and-recreated-path result is one synthetic method-level observation
at that Aider version. It is not evidence that every Aider version, workflow, or
integration behaves that way. No private project was touched.

## Fair limits

- These fixtures were designed around selective recovery. They do not measure
  the broader features of Pi, Aider, or OrcaReplay.
- UndoWeave requires agents to use its editing tool. Pi's hooks and Aider's own
  agent integration can capture their normal workflows more automatically.
- Aider's fixture records A's change as an Aider-owned Git commit, as its method
  requires. Later edits remain uncommitted. pi-rewind uses checkpoints; UndoWeave
  records writes. No claim is made that setup or integration friction is equal.
- A checkpoint is a useful recovery mechanism even when its restore does not
  satisfy this particular selective-undo objective.
- The suite does not measure multi-file transactions, arbitrary shell writes,
  runtime attribution, adversarial races, binary files, every merge shape, or
  real-user repeat usefulness. Security checks are reported separately.
- [OrcaReplay](RESEARCH.md) was researched but not executed as an undo comparator;
  replaying/forking an agent run addresses a different task.

See [the reproduction instructions](../benchmarks/README.md),
[shared fixtures](../benchmarks/fixtures.json), and
[machine-readable results](../benchmarks/results.json). The harness verifies the
upstream revision/source before execution. Results include hashes of the tested
UndoWeave engine and merge files so later changes are distinguishable.
