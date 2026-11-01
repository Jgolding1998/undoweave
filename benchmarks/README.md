# Reproduce the comparison

These eight deliberately small, public fixtures compare a **selective-undo
objective**. They do not rank complete products. Safe refusals are distinguished
from successful merges and checkpoint restores.

Requirements: Python 3.11+, an installed UndoWeave checkout, Git, and Node 24+.
No model, agent subscription, npm packages, or paid API is used. The comparator
creates temporary projects, disables commit hooks and signing there, and removes
only those generated temporary directories.

## pi-rewind

Inspect [the pinned upstream core](https://github.com/arpagon/pi-rewind/blob/91611ad87992fb7b635a41ba68f67916ff6e6ae3/src/core.ts)
before executing external code. Then, from this repository:

```console
git clone --no-checkout https://github.com/arpagon/pi-rewind.git work/compare/pi-rewind
git -C work/compare/pi-rewind checkout --detach 91611ad87992fb7b635a41ba68f67916ff6e6ae3
python benchmarks/compare.py --pi-rewind work/compare/pi-rewind --output work/compare/results.json
```

The harness rejects a different commit or modified tracked files. It directly
imports unchanged `src/core.ts`; it does not load the Pi extension or install Pi.
A recovery checkpoint is taken before each restore, matching the integration's
safety pattern. The temporary Git repositories have no remotes.

## Optional Aider method comparison

This executes the exact `Commands.raw_cmd_undo` method from the pinned source,
using real GitPython repositories plus a small adapter for I/O and session
metadata. The full Aider CLI, model, and other commands are **not** tested.

1. Save the raw bytes of [this pinned commands.py](https://raw.githubusercontent.com/Aider-AI/aider/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/commands.py)
   at `work/compare/aider/commands.py`. Review its `raw_cmd_undo` method.
2. Create an isolated comparison environment:

```console
python -m venv work/compare/aider-env
```

3. Using that environment's Python (`work/compare/aider-env/Scripts/python.exe`
   on Windows, `work/compare/aider-env/bin/python` on macOS/Linux), install:

```console
python -m pip install GitPython==3.1.46 gitdb==4.0.12 smmap==5.0.3
```

Here `python` means the isolated comparison environment's interpreter, not your
system Python. Add the two arguments to the comparison command:

```console
python benchmarks/compare.py --pi-rewind work/compare/pi-rewind --aider-source work/compare/aider/commands.py --aider-python work/compare/aider-env/Scripts/python.exe --output work/compare/results.json
```

Use the macOS/Linux interpreter path on those platforms. The runner checks the
source's exact Git blob hash before compiling only that one method from its AST;
other module-level code is never executed. Both adapters perform destructive
restores only inside their generated disposable projects. Do not point either
upstream implementation at your working project for this benchmark.

The published [results](results.json) were produced on Windows with Python
3.13.11, Node 24.13.0 and Git 2.52.0. No latency or complete-product ranking is
claimed. Re-run to see your own environment's result.
