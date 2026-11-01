# Reproduce retained recording evidence

This is a **20-case development regression benchmark**, using synthetic text files only. Its ten pairs share exact initial and final source bytes but follow different observation histories. Nine pairs should yield distinguishable retained evidence; one stopped-recorder save/revert control should remain indistinguishable.

The corpus was originally authored before the recorder implementation was inspected, then replayed after an implementation freeze. It is now a public development fixture set, not an unseen external benchmark. `cases.json` is the byte-identical original producer corpus. `expected-transitions.json` derives file transitions from its original stable checkpoints; the hash manifest records provenance and data/source hashes. No private work receipts, raw machine status, model answers or personal paths are included here.

## Run from a normal clone

Use Python 3.11+ with **`undoweave==0.4.0` installed** in that interpreter. The runner uses the standard library only; it does not install dependencies. MCP extras and a model account are unnecessary. Isolated child interpreters import the installed distribution, without `PYTHONPATH`, checkout source insertion or fixture imports.

From the clone root:

```sh
python -I benchmarks/recording/run.py --output work/recording-regression-001
```

The explicit output must be a **new directory under this clone's ignored `work/` tree**. Existing targets, including empty directories, are refused. The runner validates the `work/` ignore rule and refuses linked/reparse-point output ancestry. It creates owner-only directories on POSIX. On Windows the directory inherits NTFS permissions; choose a private user-owned checkout. A refusal never clears or recursively deletes existing data.

The runner creates only small synthetic projects in that output. Each recorder is pinned to its generated project, uses real 0.25-second background polling, and reaches each checkpoint after two completed scans. Tool writes use expected hashes. Restarts use new processes and preserved private stores. A separate fresh process retrieves evidence after shutdown. Selected fixture text is never executed, and there are no model/network calls. Windows children are hidden. Per-operation and child deadlines are bounded; stalled owned children are terminated without searching for or stopping other processes. OS scheduling can still make a mutation-group timing boundary inconclusive.

Expect roughly a minute, depending on filesystem and process startup speed. Source, prompts or model grading files are not modified. The runner leaves its own synthetic projects and journals for review; it performs no automatic recursive filesystem cleanup. Use a different new output name for another run.

## Outputs and interpretation

`public-summary.json` and stdout contain a sanitized mechanical summary with no local absolute paths. All raw statuses, process responses, fixture sources and SQLite journals stay in the explicitly chosen ignored output. **Do not publish the raw output tree.** If sharing a result, review and share only the sanitized summary.

A matching run retrieves **29 stable file transitions: 27 unknown-author observations and two tool-recorded transitions with self-reported labels**, with no missing or extra events. It distinguishes the nine informative pairs and preserves the one unknown blind-history control. It checks per-file hash-chain order, exact final bytes, inspection without source changes, attribution/undo/check-evidence limits and clean lifecycle. Failure and timing-inconclusive cases remain in the denominator and return a nonzero exit status. Interrupted runs retain their owned artifacts and must not reuse that output directory.

Coverage includes temporary restoration, deletion/recreation, independent edit order, tool/outside interleaving, intermediate config omissions, BOM/Windows newline bytes, create/delete capture windows, process restarts, observation gaps and invisible stopped-recorder save/revert. No initial baseline change is invented in the journal.

The data also contains 100 authored factual/uncertainty questions for a separate reader exercise. **This runner does not call a model or grade those questions.** Mechanical capture counts are not model accuracy, real-user validation, proof that source-only uncertainty is wrong, or general superiority over Claude/Codex. Source-only readers can correctly say that intervening history is unknown.

## Limits

- These are small, deliberate stable-checkpoint cases, not a capture-rate, large-project performance or exhaustive security test.
- A record establishes captured net file differences. It cannot prove every save, verified outside authorship, exact save time, private reasoning, passed tests or failure causality.
- Same versus separated capture windows do not prove semantic rename commands or an atomic global multi-file state. Event insertion order is not necessarily actual save order.
- A fast save/revert while recording is stopped is intentionally unobservable in both evidence conditions. No hidden activity may be invented from it.
- Public status returns the latest session; earlier no-change sessions and exact downtime are not exposed by this API. Inspection diffs normalize display newlines. Exact hashes preserve byte distinctions, but inspection does not return explicit BOM/newline profiles or full raw snapshots.
- Source snapshots and journals are plaintext. The generated data is synthetic, but local paths in raw reports may still identify your machine. Keep the chosen output private.

`manifest.json` verifies this benchmark's five source/data files before execution. The runner also records installed-package source hashes and refuses a package that changes during the run. This detects accidental drift, not malicious manifest rewriting by a trusted local user. The benchmark neither edits the installed package nor imports source from this clone.
