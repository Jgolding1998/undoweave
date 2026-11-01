# Validation record

This is an early implementation, not a formal verification or a claim that no data loss is possible. All test edits and external comparisons used disposable projects. Mechanical tests and benchmarks call no model or API. The separate reader exercise used the existing signed-in Claude Code installation.

## Version 0.3 recording checks on 2026-11-01 UTC

Local Windows testing ran 251 tests with eight expected platform/privilege skips.
Actual MCP subprocess checks include startup-pinned outside saves, persisted
inspection after reconnecting, unknown attribution, source-free HTML and refusal
to selectively undo an observed entry. Existing selective undo checks remain in
the same suite. Linux and other Python versions are separate hosted CI checks;
use the completed Actions results for the exact release commit.

The [public recording regression](../benchmarks/recording/README.md) replayed
20 synthetic cases through real background polling and fresh-process inspection.
It retrieved all 29 expected stable transitions (27 observations and two tool
operations), with no missing or extra entries. Nine informative history pairs
were distinguishable despite identical initial/final source; one stopped-recorder
save/revert control stayed indistinguishable. These are stable-checkpoint cases,
not a guarantee of capturing every save or a model accuracy score.

An independent review reproduced and corrected Windows empty-lock contention,
atomic-save sample identity, conservative ignore matching, and a scan-bound
timestamp that could exclude the actual save. Maintained regressions cover those
failures. A current-tree credential scan and pinned dependency audit found no
credentials or known dependency advisories in their checked scope. Those checks
do not establish universal security, encryption, or safe handling of every secret
embedded in ordinary source.

A separate [reader comparison](RECORDING_COMPARISON.md) gives the actual
with/without-evidence results, including correct source-only uncertainty and
unsupported claims from the evidence reader. It does not establish general
superiority over Claude or Codex.

## Final 0.2.0 release checks on 2026-11-01 UTC

The published `v0.2.0` commit is
[`368b8b230c2bc1262175b438cda2478d7fc79831`](https://github.com/Jgolding1998/undoweave/commit/368b8b230c2bc1262175b438cda2478d7fc79831).
Its [main workflow run](https://github.com/Jgolding1998/undoweave/actions/runs/41262585865)
completed all five jobs successfully. The recorded unit-test results were:

| Hosted runner | Python | Tests run | Expected skips |
| --- | --- | ---: | ---: |
| Ubuntu | 3.11 | 197 | 11 |
| Ubuntu | 3.13 | 197 | 11 |
| Windows | 3.11 | 197 | 2 |
| Windows | 3.13 | 197 | 2 |

The skips are platform-specific checks: Windows behavior is exercised on
Windows, and POSIX permission/FIFO behavior on Linux. Each test job also passed
the disposable CLI demo, package build, and fresh-wheel CLI/MCP verification.
The separate dependency-audit job passed. The preceding
[pull-request workflow](https://github.com/Jgolding1998/undoweave/actions/runs/41262208087)
also passed.

This release receipt is separate from the earlier 192-test local snapshot
below. It does not establish that every future branch or platform will pass;
use the current Actions results when reviewing a later change.

## Version 0.2 local checks on 2026-10-31

The frozen upgrade suite ran **192 tests**, passing with seven expected local
Windows privilege/platform skips. Its five actual MCP subprocess tests include
range/metadata reads, exact edits, post-undo hashes and the HTML timeline resource.
The independent repeated-line oracle covers 80,496 exhaustive small-alphabet
events, 13,227 missing-final-newline cases and 60,000 seeded block events by
default. Accepted results must match an exact permitted event-history outcome;
safe refusals remain allowed. These generated cases are not independent real
projects or a general accuracy estimate.

Claude Code independently reproduced v0.1's crosswise repeated-line corruption,
then added executable regressions and tested the stable-anchor replacement.
The old release's passing tests had missed that defect. Use v0.2, and retain
independent backups. The new policy deliberately refuses more adjacent/repeated
context cases. [Merge policy](CONTRACT.md), [migration](MIGRATION.md).

Additional checks cover actual Windows open handles/read-only attributes,
interruption before/after replacement, binary/oversized pending targets,
simultaneous fresh startup, the cross-Python Windows volume serial form, exact
BOM/CRLF/bare-CR bytes and review formatting. Seven real-Git tests check HEAD,
refs, the raw index, staged work and stash invariants through commits, amendments,
branch switches and ignored files. Timeline tests verify HTML escaping, output
privacy and a real CLI export. All edits used disposable projects.

The [current comparison](COMPARISON.md) and [efficiency receipts](PERFORMANCE.md)
record current source hashes and exact-result oracles. Their exposed fixtures
demonstrate specific behaviors, not universal superiority. Hosted CI and fresh
wheel installation are separate release checks; inspect Actions for completed
results.

## Historical v0.1 checks on 2026-10-31

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
