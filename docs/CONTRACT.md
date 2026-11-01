# Implementation contract

UndoWeave is a local, stdio-only MCP and CLI for explicitly recorded UTF-8 text edits. No command execution, watching, model calls, network service, or automatic interception. One edit changes one file. Root and state directory are pinned at server startup, never supplied by a tool call.

`undoweave.engine.Engine(root: Path | str, state_dir: Path | str | None = None)` exposes:

- `read(path: str) -> dict`: relative path, exists, content (or null), sha256 (`missing` for absent).
- `edit(path: str, content: str | None, actor: str, expected_hash: str, label: str = "") -> dict`: null deletes; return id, path, actor, before_hash, after_hash, status. Requires exact expected hash. Content and metadata bounded. Never executes content.
- `history(limit: int = 50) -> list[dict]`: newest first, summaries only, no source text.
- `preview_undo(edit_id: str) -> dict`: status (`ready`, `conflict`, `already_undone`), path, reason, bounded diff and token only if ready. Read-only for project files; stores expiring local plan.
- `apply_undo(token: str) -> dict`: exact reviewed plan, fresh file hash and history checks, one-use token, return status and operation IDs. A changed file requires another preview. Record the undo as an operation, enabling preview/undo of that inverse (redo).
- `status() -> dict`: version, root, state directory, recorded count, pending journal health and limits. No file contents.

`UndoWeaveError(Exception)` has `code` and a human-readable message. Tool errors expose code and safe message, not tracebacks or stored source. Core has no third-party runtime dependencies.

The merge is deliberately conservative. Disjoint later edits survive; overlapping or ambiguous alignment refuses without mutation. No force switch. Preserve exact UTF-8 bytes and line endings. Binary files, protected paths, symlinks/reparse points, hardlinks, oversized files, and paths outside the pinned root are rejected. Durable journal records intent before changing a project file. All cooperating UndoWeave processes serialize via the journal. External editors are not locked; document the residual race honestly.
