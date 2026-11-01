# Implementation contract

UndoWeave is a local, stdio-only MCP and CLI for explicitly recorded UTF-8 text edits and opt-in observation of selected project paths. No command execution, model calls or network service. One tool edit changes one file. Root, state directory and optional recording scopes are pinned at startup, never supplied by a tool call.

`undoweave.engine.Engine(root: Path | str, state_dir: Path | str | None = None)` exposes:

- `read(path: str, start_line: int | None = None, end_line: int | None = None, metadata_only: bool = False) -> dict`: relative path, exists, selected content (or null), full-file sha256 (`missing` for absent), byte/physical-line counts and content scope. Ranges are 1-based inclusive; LF, CRLF and bare CR delimit physical lines. Unicode separators remain data. Selection reduces output, not local hashing work.
- `edit(path: str, content: str | None, actor: str, expected_hash: str, label: str = "") -> dict`: null deletes; return id, path, actor, before_hash, after_hash, status. Requires exact expected hash. Content and metadata bounded. Never executes content.
- `replace_text(path, old_text, new_text, actor, expected_hash, label="") -> dict`: replace one exact unique occurrence, including overlap checks. No regex or line-ending normalization. Uses the whole-file expected hash and the same recording/write checks as `edit`.
- `history(limit: int = 50) -> list[dict]`: newest first, summaries only, no source text.
- `inspect_change(change_id: str) -> dict`: persisted origin, attribution, hashes, a bounded escaped before/after diff and a truncation flag. Observations include their interval and unknown author. Check evidence is `not_captured`; no undo token is returned.
- `preview_undo(edit_id: str) -> dict`: status (`ready`, `conflict`, `already_undone`), path, reason, operation, bounded escaped diff, exact line-ending profiles and token only if ready. Display newlines are normalized; applied bytes retain exact endings. Read-only for project files; stores an expiring hash-bound local plan, with no duplicate result snapshot.
- `apply_undo(token: str) -> dict`: exact reviewed plan, fresh file hash and history checks, one-use token, return status and operation IDs. A changed file requires another preview. Record the undo as an operation, enabling preview/undo of that inverse (redo).
- `status() -> dict`: version, root, state directory, recorded count, pending journal health and limits. No file contents.

`UndoWeaveError(Exception)` has `code` and a human-readable message. Tool errors expose code and safe message, not tracebacks or stored source. Core has no third-party runtime dependencies.

The merge is deliberately conservative. Shared lines occurring exactly once in all three snapshots, in unchanged order, partition the file. A region changed by both the recorded edit and later work refuses the entire undo. Separate edits need a stable separator: adjacent changes, repeated braces and blank lines can cause a safe refusal. No force switch. Exact UTF-8 bytes and line endings are preserved. Binary files, protected paths, symlinks/reparse points, hardlinks, oversized files, and paths outside the pinned root are rejected.

Initialization binds the private schema-2 journal to the canonical root, directory identity and a source-free `.undoweave-id` marker. Preserve and Git-ignore that marker; startup can create it. Nonempty v0.1 journals require the local operator's explicit reviewed `adopt-legacy --confirm-root`; no MCP tool migrates or rebinds history. Stop all clients before migration. A changed filesystem identity requires a separate journal, even if the path looks the same.

Optional recording uses additive session/observation metadata and entries with
status `observed` in the same ordered journal. They are never applied/pending
write intents. `preview_undo` refuses them with `observation_not_undoable`.
Baseline creation does not create a change entry. Scope, resource, ignore,
partial-scan, concurrency and attribution limits are in [RECORDING.md](RECORDING.md).

Durable journal intent precedes each project write. All cooperating processes sharing that journal serialize across commits and replacement. Unknown interrupted states retain their intent and block mutations; status/history remain available for diagnosis. External editors are not locked. Filesystem metadata beyond existing mode bits is not preserved; all new/recreated files use owner-only creation mode (Windows ACLs still inherit).
