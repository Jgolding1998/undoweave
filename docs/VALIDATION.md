# Validation record

This is an early implementation, not a formal verification or a claim that no data loss is possible. All test edits and external comparisons used disposable projects. No paid model or API was used by the tests or benchmarks.

## Local checks on 2026-10-31

Windows, Python 3.13.11, official MCP SDK 2.3.0:

- The release suite contains 101 tests. Windows checks passed with seven expected local platform/privilege skips. See CI for execution on Linux and Windows hosted runners.
- Four real MCP subprocess integration tests exercised tool discovery, instructions/prompt/resource, edit/undo with later human and agent edits, stale/reused tokens, path escape rejection, and protocol-only stdout.
- Five Hypothesis properties generated 460 scenarios using an independent stable-line-identity oracle. Accepted results must match exact expected bytes; safe refusals are permitted for ambiguous text.
- A separate deterministic oracle exercised 1000 disjoint replacement scenarios with LF and CRLF.
- The 42 focused security regressions included actual NTFS short-name credential/state aliases, Windows case-alias history invalidation, hardlinks and junctions, malformed/tampered journals and plans, known preflight refusal versus unknown mutation recovery, and a two-process interleaving across the durable-intent/file-write boundary.
- Generated Windows setup commands were parsed and exercised through PowerShell 7.6.5 and Windows PowerShell 5.1 with neutral punctuation markers. Arguments had to reach the test program literally; unsupported argument shapes are refused. POSIX quoting has a separate roundtrip check.
- An installed MCP was launched using its generated isolated configuration from a disposable project containing shadow `undoweave.py`, `mcp.py` and `sitecustomize.py` files, with `PYTHONPATH` pointing there. The real MCP initialized and read its fixture; none of the shadow probes ran.
- Crash injection covered interruption before replacement, after replacement, and unknown outside edits after interruption. Unknown states refused automatic recovery without overwriting them.
- `pip-audit` reported no known vulnerabilities in the versions listed in `constraints.txt` at audit time. This is an advisory-database result, not a complete dependency security review.
- A credential-pattern scan of intended public source files found no matches for the checked GitHub/OpenAI/AWS token and private-key patterns. This does not prove absence of every possible secret.

Security review found and addressed an SQLite handle leak on malformed state, insufficient coordination across a journal commit and file replacement, repeated-text comparison cost, preview storage/review limits, Windows alias-based history/denylist gaps, state-directory containment aliases, unnecessary recovery lockout after a known preflight refusal, and unsafe generated PowerShell argument quoting. Claude's follow-up text review found no blocker in the corrected state-containment/write-phase paths; executable regressions independently verified those fixes. See [SECURITY.md](../SECURITY.md) for remaining boundaries, including plaintext snapshots and residual outside-editor races.

## Installation and cross-platform verification

CI builds distributions and installs the wheel into a fresh virtual environment, with checkout `src/` absent from import paths. The installed CLI demo and a real MCP edit/preview/undo roundtrip must succeed. The same workflow runs on Linux and Windows with Python 3.11 and 3.13. Check the [actual workflow runs](https://github.com/Jgolding1998/undoweave/actions/workflows/checks.yml) for current results; configuration alone is not evidence that a run passed.

Reproduce package verification after `python -m build`:

```sh
python scripts/check_wheel.py
```

## External comparisons

[Eight shared fixtures](COMPARISON.md) were executed against UndoWeave, unchanged pinned pi-rewind core, and the exact pinned Aider undo method with real GitPython repositories. Five UndoWeave undos preserved expected later work and three conflicts preserved all files. These are exposed development fixtures designed around selective undo. They do not establish broad superiority, user satisfaction, or performance across real projects.

Full Pi UI, full Aider CLI, model behavior, and OrcaReplay were not executed. Safe refusals and whole-checkpoint restoration are distinguished from successful selective merges. [Reproduction and source provenance](../benchmarks/README.md).
