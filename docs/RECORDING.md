# Keep evidence after the chat is gone

The recorder watches selected project paths while it is running. It captures
stable, supported text snapshots when an editor or agent saves outside
UndoWeave's tools. The journal keeps the observed differences after a restart.
It does not need a model account, API key, hook or running test suite.

## Try it without touching your project

```sh
undoweave demo --recording
```

This creates disposable files, makes an ordinary outside save, and verifies
that a new engine can inspect the saved diff. It also checks that the initial
baseline invents no earlier history and that inspecting evidence changes no
source. The output labels the author unknown and test evidence not captured.

## Record a chosen project

For a suggested project and code paths, run `undoweave setup --client claude`
or `--client codex` from the project. It prints a plan and connection settings;
recording begins only after the configured server starts. The optional companion
skill helps an authorized agent handle that setup. [Installation](INSTALL.md).

Print an MCP configuration with recording enabled for your chosen paths:

```sh
undoweave --root /absolute/project config --watch src --watch tests
```

Use existing relative files or directories. `--watch` can be repeated for up to
16 scopes. Review and apply the printed configuration, then reconnect the MCP
client. Recording starts with that server and stops when it disconnects.
No tool call can turn it on or widen its scope.

Alternatively, keep this foreground command running in another terminal:

```sh
undoweave --root /absolute/project watch src tests
```

Ctrl+C stops it. Run one recorder per journal. Other MCP clients can connect
without `--watch` and use the shared evidence. Two recorders are refused rather
than silently duplicating histories. The command does not launch an agent,
execute the project, run tests, change Git history or install global hooks.

The default interval is one second. `--interval 0.5` changes the poll interval;
the supported range is 0.25 to 60 seconds. A change must be stable across scans,
so the interval is not a promise of exact capture latency or action timing.

## Ask about the evidence

> Check workspace_status and list_changes, then inspect_change for relevant
> IDs. Explain what the saved diffs show. Distinguish tool-recorded edits from
> observed net changes, cite the IDs, and mention coverage gaps. Do not guess
> an outside author or treat a suspicious edit as a proven cause. If no test
> evidence was captured, say that.

Without an agent:

```sh
undoweave --root /absolute/project history
undoweave --root /absolute/project inspect CHANGE_ID
undoweave --root /absolute/project history --format html > timeline.html
```

The HTML timeline contains selected metadata, not source or diffs. `inspect`
and `inspect_change` explicitly return a bounded escaped diff, with a
truncation flag if it is incomplete. Review before sharing either output.
Diff display normalizes newlines and does not provide a complete standalone
byte/BOM profile. Different missing/content hashes can describe an empty-file
creation or deletion even when its text diff is empty.

## What the record establishes

| Entry | What it establishes | Undo support |
| --- | --- | --- |
| Tool-recorded edit | UndoWeave's operation, its saved before/after bytes and a caller-supplied actor label | Existing preview and selective undo, with user approval |
| Observed net change | Two supported snapshots differed within the reported observation interval; author unknown | Read-only evidence; no selective undo token |

Observations can combine multiple saves or multiple people. A quick save and
revert between scans can be invisible. Initial baselines and restarts do not
backfill changes made before recording or during downtime. Files in an
incomplete or failed scan are not treated as deleted. Gaps and limits need
review; an empty list is not proof that nothing happened.
Workspace status exposes the latest session report. Earlier no-change sessions
and exact downtime are not available through the current MCP inspection API.

This is retained evidence, not a transcript of model reasoning. It does not
capture shell output, test outcomes, conversations, browser actions or network
requests. A model can use the evidence to investigate a failure, but the
recorder does not prove causality. Git and independent backups still matter.
Files are sampled separately. Journal insertion order and overlapping reported
intervals do not establish actual save order, an atomic multi-file state, or a
semantic rename command. Ask the agent to separate recorded facts from possible
explanations.

## Privacy, coverage and limits

Source snapshots remain plaintext in the private local journal, outside the
project by default. They are not encrypted or automatically expired. An
ordinary source file can contain a secret even if its name is harmless. Choose
scopes carefully and do not upload a journal as a bug report. MCP inspection
discloses selected evidence to your client and its normal model/data policy.

Protected credential, version-control, dependency, generated/artifact, client
configuration and hidden descendant paths are excluded. Links, reparse points,
hardlinks, binaries and unsupported text are not captured. Bounded project and
nested `.gitignore` positive exclusions are conservative: negations never
reinclude a path. This is not complete Git ignore semantics, and does not claim
global ignore or `.git/info/exclude` support. Unknown/oversized ignore policy
fails visibly instead of silently broadening collection.

Recording is bounded to 256 supported files, 256 KiB per file and a 16 MiB
aggregate baseline, within the existing 128 MiB journal cap. Traversal and
application scan work are bounded; OS filesystem calls are not an OS sandbox
or a hard wall-clock guarantee. Limit errors stop visibly instead of silently
dropping old evidence or claiming full coverage. The persisted last report is
not proof that a recorder is currently alive.

## Why this can help more than asking from memory

The same final file can follow `original → broken → fixed` or just
`original → fixed`. Looking only at the final file cannot distinguish those
histories. A running recorder can retain the intermediate stable difference,
and a fresh agent can inspect it after its conversation context is gone.

If the agent already has complete, accurate history in context, a journal may
add little to that particular question. This feature does not claim superior
reasoning, universal capture, or a general accuracy win over Claude or Codex.

[Reproduce the 20-case recording regression](../benchmarks/recording/README.md)
or read the separate [with/without-evidence reader comparison](RECORDING_COMPARISON.md).
