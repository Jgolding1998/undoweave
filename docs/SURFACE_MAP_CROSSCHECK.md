# Surface Map cross-check on upstream source

We called Justin's other tool, [Surface Map](https://github.com/Jgolding1998/surface-map), through a real local stdio MCP connection. It worked as a bounded source inventory. It did not discover the checkpoint safety change that manual review found. That distinction matters: Surface Map explicitly describes these tools as inventories, not vulnerability scanners.

## What actually ran

Recorded on 2026-11-01 UTC:

- Surface Map 1.5.0, checkout [`c087bb4`](https://github.com/Jgolding1998/surface-map/commit/c087bb4463729c0c77f40d1eeba6739667e57557).
- Official Python MCP SDK 2.3.0, using `Client` and its stdio transport. The server was launched by its absolute `run_mcp.py` path with Python isolated mode. No persistent client configuration changed.
- Six successful calls: `limits`, `demo`, one `inspect_project`, and three `inspect_sources` calls. Tool discovery returned all eight advertised tools.
- Six exact public Git blobs, 128,861 UTF-8 bytes in total. Their Git blob IDs were verified before analysis. No private projects, captures, model calls, source execution or URL-analysis tool were used.
- `javascript_engine: "basic"` was explicit. The JavaScript/TypeScript inventory used its narrower lexical analysis; Python used Surface Map's static Python parser. Nothing installed a parser or dependency during analysis.

## Selected files

These are deliberate selections, not whole-repository scans or a claim about the newest application release.

| Snapshot | Supplied files |
| --- | --- |
| Cline checkpoint fix [`89c2efa`](https://github.com/cline/cline/commit/89c2efa970a115d0815942e4eb69f1a74f9d3b5e) | [`checkpoint-restore.ts`](https://github.com/cline/cline/blob/89c2efa970a115d0815942e4eb69f1a74f9d3b5e/sdk/packages/core/src/session/checkpoint-restore.ts), [`checkpoint-hooks.ts`](https://github.com/cline/cline/blob/89c2efa970a115d0815942e4eb69f1a74f9d3b5e/sdk/packages/core/src/hooks/checkpoint-hooks.ts), [`core/package.json`](https://github.com/cline/cline/blob/89c2efa970a115d0815942e4eb69f1a74f9d3b5e/sdk/packages/core/package.json) |
| Its immediate parent [`f753a01`](https://github.com/cline/cline/commit/f753a01d85ea0514c7fd29eb348201b58046fac1) | [`checkpoint-restore.ts`](https://github.com/cline/cline/blob/f753a01d85ea0514c7fd29eb348201b58046fac1/sdk/packages/core/src/session/checkpoint-restore.ts) |
| Aider comparison pin [`5dc9490`](https://github.com/Aider-AI/aider/commit/5dc9490bb35f9729ef2c95d00a19ccd30c26339c) | [`commands.py`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/commands.py), [`repo.py`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repo.py) |

The three Cline files from the fixed snapshot went through `inspect_project` with their real relative logical paths. The two Aider files went through `inspect_sources`. The before and after Cline restore files were each inspected separately, so they did not masquerade as one project snapshot.

## Results and omissions

| Check | Actual result | Interpretation |
| --- | --- | --- |
| Cline project inventory | All three selected files parsed; TypeScript/JavaScript labels; declared `zod` and its data-validation role; package-script hint | Useful evidence about selected source and one curated dependency, not an explanation of checkpoint behavior |
| Cline coverage | Workspace-inherited dependency warning; 24 unknown manifest dependencies; graph omitted five references whose targets were not selected and ten external/package references | The returned graph had no edges. That does not mean Cline has no module relationships. Its exact-link rules and selected-file boundary limit the graph. |
| Aider source inventory | Both files parsed; Python labels; inferred Pillow import at `commands.py` line 12 and image-processing role | The input also imports `subprocess`, GitPython, `pyperclip` and `prompt_toolkit`; those did not appear as curated dependency/capability labels. |
| Cline before/after safety fix | After removing each result's source byte count, the two inventory responses were exactly equal | The inventory did not identify the new HEAD guard or explain the safety improvement. |

Manual inspection of the actual Cline patch shows the added comparison of current HEAD with the restore base and a conditional `git update-ref` before resetting workspace files. That addresses the scenario discussed in [Cline #13550](https://github.com/cline/cline/issues/13550) and [merged PR #13626](https://github.com/cline/cline/pull/13626). This is a historical fixed issue, not a claim that the current Cline release still has the reported bug.

Surface Map produced no finding about that control flow. Its `parsed` status means its supported inventory parser accepted the input. `coverage.partial: false` means the selected files were inspected, not that all repository behavior or security concerns were understood. Likewise, `unknown_dependencies: 0` on the Python sources does not mean every import is represented: unrecognized Python import roots are outside the curated inventory and are not all counted as unknown manifest dependencies.

## What this establishes

Surface Map is useful for a small evidence-first inventory and explicit selection/omission reporting. Its minimized output cannot replace a code review of Git operations, journal identity, concurrency, undo state transitions or preservation of user work. No vulnerability finding from this tool is expected or claimed.

For UndoWeave, workspace replacement and journal reuse need executable two-project regressions; an inventory of dependencies cannot establish their safety. The [upstream lessons](UPSTREAM_LESSONS.md) and independent UndoWeave regressions provide that separate analysis.

A useful future Surface Map improvement would be clearer accounting for intentionally unreported import roots and optional fixed source-activity labels, such as process execution or version-control activity. Such labels should remain source evidence with locations and limits. They must not automatically become vulnerability verdicts or claims that commands executed.

## Repeating the check

Connect the pinned Surface Map checkout according to its [MCP guide](https://github.com/Jgolding1998/surface-map/blob/c087bb4463729c0c77f40d1eeba6739667e57557/docs/MCP.md). Call `limits` and `demo` first. Fetch only the pinned public files linked above, then supply their UTF-8 contents:

- `inspect_project`: `files` containing each Cline file's `kind`, `content` and original relative `logical_path`; use `javascript_engine: "basic"`.
- `inspect_sources`: `artifacts` containing `kind` and `content` for the two Aider Python files.
- Repeat `inspect_sources` separately for the before and after Cline restore file, with `kind: "typescript"` and basic mode. Compare the inventories after excluding source byte counts.

This procedure makes no repository code execute. A tool call accepting its input is not a security score, and this six-file selection is not a general accuracy benchmark.
