# Lessons from established agent tools

Checked through the public GitHub API on 2026-11-01 UTC. This review read issue
bodies, human and automated comments, linked PR discussions, merge metadata and
the relevant changed-file patches. It did not run the full Cline or Aider apps.
The separate [measured comparison](COMPARISON.md) describes what was executed.

## Context, not a popularity ranking

| Repository | Stars at review | Status |
| --- | ---: | --- |
| [Cline](https://github.com/cline/cline) | 71,120 | Active |
| [Aider](https://github.com/Aider-AI/aider) | 49,856 | Active |
| [Roo Code](https://github.com/RooCodeInc/Roo-Code) | 24,254 | Archived/read-only |

These are established coding-agent products with broader integrations than
UndoWeave. UndoWeave has a narrower contract: explicitly recorded text edits,
one-file recovery, separate later work preserved when a conservative merge can
establish that separation. Stars do not establish reliability or superiority.

## 1. Restore must respect commits and concurrent work

[Cline issue #13550](https://github.com/cline/cline/issues/13550) reported that a
checkpoint restore could move the user's branch behind commits created after
the checkpoint. This is a historical bug with a merged fix,
[PR #13626](https://github.com/cline/cline/pull/13626), merge commit
`89c2efa970a115d0815942e4eb69f1a74f9d3b5e`.

The actual merged patch checks the restore base against HEAD and uses Git's
expected-old-value `update-ref` operation before syncing the worktree. An earlier
proposal, [#13551](https://github.com/cline/cline/pull/13551), would have retained
orphaned commits behind recovery refs; it was closed without merging. Those are
different approaches, so a closed proposal must not be described as the shipped
fix.

The [inline review](https://github.com/cline/cline/pull/13626#discussion_r3874087670)
identified a check-then-reset race. The human author acknowledged it and added
the compare-and-swap. A later
[human response](https://github.com/cline/cline/pull/13626#issuecomment-5444165240)
explicitly left a narrower failed-restore/concurrent-commit recovery race as
best-effort behavior. That discussion supports careful boundaries, not a claim
that any tool has an atomic Git-plus-filesystem transaction.

**UndoWeave regression to keep:** record an edit, create/amend a real human Git
commit or switch branches, then preview/apply a supported undo or encounter a
conflict. Assert HEAD, branch refs, staged index and unrelated files remain
unchanged. UndoWeave's core does not run Git commands; this property should be
tested rather than inferred from that architecture alone. Its journal lock
coordinates cooperating UndoWeave processes, not arbitrary outside editors.

## 2. Changing ignore rules must not imply deleting data

[Cline issue #14367](https://github.com/cline/cline/issues/14367) describes an
ordering problem: restoring an older `.gitignore` can expose previously ignored
files to a later cleanup operation. The linked
[PR #14377](https://github.com/cline/cline/pull/14377) was **open and unmerged** at
review. Its proposed patch refuses dirty root/nested ignore rules before either
restore entry point mutates the workspace. It deliberately avoids capturing all
ignored files, which could include credentials, dependency trees and large data.

The [human discussion](https://github.com/cline/cline/issues/14367#issuecomment-6528356426)
also asks whether users sometimes intend to remove agent-created junk along
with an ignore-rule change. This is a product-contract decision, not permission
to assume every ignored file is disposable. The report and proposal were read;
we did not independently reproduce the current full Cline application behavior.

**UndoWeave regression to keep:** undo an explicitly recorded `.gitignore` edit
while an ignored scratch directory, untracked notes and staged files exist.
Only the selected file may change. Selected-file undo must never become a
whole-workspace cleanup.

## 3. Unrelated dirty files need explicit protection

In [Aider issue #513](https://github.com/Aider-AI/aider/issues/513), the reporter
said undo removed manual work in files outside the edited commit. The maintainer
initially requested evidence, then
[confirmed and fixed the bug](https://github.com/Aider-AI/aider/issues/513#issuecomment-2015540088).
The actual [fix commit](https://github.com/Aider-AI/aider/commit/a39965a9ff3fe2fdb43dbceca1dfb69f0b805dc1)
changes a blanket hard reset into selected-file restoration and a soft branch
reset, with a regression asserting unrelated dirty content survives. This is
fixed historical behavior, not evidence that today's Aider still has that bug.

**UndoWeave regression to keep:** selected undo must preserve unrelated staged,
unstaged, ignored and untracked content byte-for-byte, including files created
after the recorded edit. A refusal must preserve the whole project state too.

## 4. Successful undo needs a result the agent can trust

[Aider issue #5874](https://github.com/Aider-AI/aider/issues/5874) reports an undo
acknowledgement being discarded before it reaches the model. The
[proposed fix #5881](https://github.com/Aider-AI/aider/pull/5881) was open at review;
its patch returns the inner undo result and tests that the model-facing reply
is propagated. Its lone conversation comment was an automated CLA notice, not
an independent human confirmation or review approval.

**UndoWeave regression to keep:** successful CLI/MCP undo returns the applied
operation ID and resulting hash, and an immediate read observes those bytes.
Errors must not appear to be successes. Agent instructions should require a
fresh read after undo rather than assuming the pre-undo content remains current.

## 5. Completion and restart must not strand recovery controls

[Cline issue #8866](https://github.com/cline/cline/issues/8866) describes restore
controls staying disabled after one use. A human contributor confirmed the
report. The merged [PR #8873](https://github.com/cline/cline/pull/8873), commit
`c2b87252ac47ec4e911167539e4a93c09748e5c5`, adds `finally` blocks to the three
frontend restore handlers. The issue bot had suggested a backend event change;
the shipped patch instead resets local UI state on both success and failure.
This illustrates why we read the diff instead of treating a bot diagnosis as
the implementation.

**UndoWeave regression to keep:** after successful undo, a refused stale plan,
and an injected pre-write failure, a new Engine/server instance can still list
history and perform a fresh valid operation. Exercise repeated undo/redo through
fresh inverse operation IDs; reusing an expiring token must remain forbidden.
UndoWeave does not have Cline's restore-button UI, so that specific frontend bug
does not apply directly.

## 6. Stable operation identity beats reconstructed conversation counters

[Cline issue #12388](https://github.com/cline/cline/issues/12388) and
[PR #12387](https://github.com/cline/cline/pull/12387) discuss runtime retries,
synthetic messages and compaction diverging from user-visible turn counts. The
PR remained open. Its
[human review](https://github.com/cline/cline/pull/12387#issuecomment-5287077770)
states that the primary counting fix had already landed through
[merged #12691](https://github.com/cline/cline/pull/12691), commit
`b47851791c934606cff3ef081eaf34258163468f`, while metadata merging and resumed
history preservation still needed attention. The comments therefore do not
support describing every part of the original issue as unfixed today.

**UndoWeave regression to keep:** retain an edit ID across server restarts,
interleaved actor edits and failed operations. Preview must select that exact
record, not reconstruct it from message numbers, caller labels or list position.
Conversation rewinding and compaction are outside UndoWeave's current contract.

## 7. Performance fixes must preserve snapshot semantics

[Cline #13131](https://github.com/cline/cline/issues/13131) includes a reporter's
measurement of a large untracked data file being hashed every turn. The human
discussion later narrowed the fix: [merged #13199](https://github.com/cline/cline/pull/13199),
commit `5a3b870d8573a2a6f41695a638c0fdf92e879112`, reuses a persistent snapshot
index and removes stale entries. Its final scope explicitly excludes proposed
size caps, timeouts and restore-semantic changes. Earlier comment drafts must
not be mistaken for the final shipped behavior. We did not reproduce the
reported timings on the reporter's machine.

**UndoWeave regression to keep:** selected-file operations must not enumerate
or read unrelated large/binary files. Measure our selected-file costs with a
controlled fixture; do not compare our timings to somebody else's anecdote.

Human comments in [Cline #4389](https://github.com/cline/cline/issues/4389#issuecomment-3315048855)
also ask for partial reads, and
[another human response](https://github.com/cline/cline/issues/4389#issuecomment-3693430743)
corrects a bot summary that focused too narrowly on a single file-size limit.
This suggests optional metadata/range reads and exact bounded edits are worth
evaluating for UndoWeave's MCP payload size. Lossy compression would be a poor
substitute for byte-exact recovery. Several later comments promote their own
products; those were not counted as independent demand.

## Existing demand, with disagreement preserved

Human participants in [Aider #1018](https://github.com/Aider-AI/aider/issues/1018)
ask for undo while retaining manual control of Git commits, including editing
standalone scripts without a repository. Other participants prefer ordinary
Git history instead. [Aider #1427](https://github.com/Aider-AI/aider/issues/1427)
contains requests for redo and fine-grained control alongside support for its
existing auto-commit workflow. UndoWeave fits one subset of these preferences;
it does not replace an integrated coding agent, its conversation history, or
every user's Git workflow. Recent tool-owner promotion in these threads was
not counted as proof of a market for UndoWeave.
