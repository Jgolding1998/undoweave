# Security boundaries

UndoWeave is an early local file-editing utility. Keep a separate backup or Git commit. Its checks reduce particular risks; they do not prove absence of vulnerabilities.

## Intended environment

One trusted local user, an explicitly chosen project directory, and a private state directory shared by cooperating UndoWeave processes. The MCP server only uses stdio. There is no HTTP transport, command runner, plugin loader, filesystem watcher, telemetry or model API.

The MCP client and its agent are trusted with file-editing access to the selected root. Tool annotations and agent instructions are advisory. The server cannot enforce that a human approved a tool call; anyone with access to the MCP connection can call write/apply. A preview token binds an operation to a recent reviewable state; it is not human authentication. Do not give an untrusted remote client access to this server.

## Protection implemented

- Project-relative forward-slash paths, with no traversal, absolute paths, Windows streams, reserved device names, trailing dots/spaces or control characters.
- Generated server commands use Python isolated mode (`-I`), keeping the project working directory, `PYTHONPATH` and user site packages out of server imports. Keep this flag in manual configurations: project files named `undoweave.py`, `mcp.py` or `sitecustomize.py` must not impersonate installed code.
- Credential-like names, dependency directories, journal files and version-control directories are denied. This list cannot identify every secret in an otherwise ordinary file.
- All existing path components are inspected for links and Windows reparse points. Selected files must be regular and single-linked. Open descriptors are checked against their initial identity.
- Writes require an expected content hash. Undo additionally requires a fresh, one-use preview token and unchanged same-file recorded history. Preview result text is recomputed at apply time.
- Overlapping changes, ambiguous repeated context, adjacent insertions and comparison/review limits refuse without changing project files. Independent edits on the same line may therefore conflict.
- Same-journal operations hold an OS file lock across journal commits and file replacement. Temporary file creation is exclusive; content is flushed before atomic replacement. Existing file permission bits are preserved on replacement.
- SQLite uses full synchronization. A durable pending intent is committed before any project write. Interrupted operations whose current bytes match the recorded before/after state can be classified on restart. An unknown result blocks future mutations for manual investigation.
- Known preflight failures are marked aborted while holding the lock. Failures during/after an attempted filesystem mutation retain their intent for recovery; error type alone never determines whether a write happened.
- The state directory is resolved and checked by path and directory identity before creation, so spelling aliases cannot place snapshots inside the project. Preview control/direction characters are shown as visible escapes.

## Explicit exclusions

UndoWeave is not a sandbox against malware or a hostile process running as the same OS user. Such a process can change files, directory components or the journal between checks. Outside editors do not honor its lock. Final hash/path checks narrow but do not eliminate the race immediately before replace/unlink. Using multiple different journals for the same project also defeats shared coordination.

Privileged filesystem mounts can make an otherwise external state directory reachable inside a project. Do not mount the state tree into the project. Filesystems with unreliable directory identities can cause conservative false refusals. Symlinked root/home paths are unsupported; choose their actual resolved location.

Hashes detect mismatches, not malicious journal rewriting by the same user. Actor labels are self-reported. UndoWeave cannot infer authorship, intent, or history for edits made elsewhere; identical resulting text is indistinguishable. Text movement or human recreation of identical content may also be indistinguishable.

There is no atomic transaction spanning several files. This release tracks text, not complete filesystem metadata. Recreating a deleted file uses owner-only mode rather than restoring its old executable bits. Atomic replacement does not preserve custom Windows ACLs, ownership, extended attributes or alternate streams. Existing POSIX mode bits are preserved for replacements; review permissions after recovery when they matter.

Filesystem and SQLite durability depend on the OS/filesystem and storage hardware. A process crash or failed temporary-file cleanup can leave an orphan `.undoweave-write-*` file containing source beside the target. Inspect and remove only confirmed orphan files when UndoWeave is stopped.

## Local source retention

Before/after snapshots and preview results are plaintext in `journal.sqlite3`. State is outside the project by default under `%LOCALAPPDATA%/UndoWeave/workspaces/` on Windows, or `~/.local/state/UndoWeave/workspaces/` elsewhere. `workspace_status` reports the exact directory. A supplied state directory must be outside the project.

POSIX state directories/files require owner-only permissions. On Windows the state directory inherits its parent's ACL; Python mode bits do not configure an owner-only NTFS ACL. Choose a private user-owned location and review the ACL for your environment. Snapshots are not encrypted, automatically expired or securely erased. Preview tokens expire after five minutes, but undo history persists until you deliberately archive or remove the journal.

Do not share journal files, source diffs, tokens, credentials or raw private captures in public issues. `read_file` and preview diffs are returned to the MCP client, which may send them to its model provider. No source is uploaded by the core itself.

## Interrupted write recovery

1. Stop every client connected to the workspace. Preserve a copy of the current project and state directory.
2. Restart using the same root and state directory. A pending operation matching its before bytes is marked aborted; one matching its after bytes is marked applied.
3. If `recovery_required` is returned, the file matches neither snapshot. Do not delete the journal, reset files, or force a write to clear the error. Compare the saved before/after snapshots and current file locally with a trusted developer. The journal is an SQLite database; keep all examination private.
4. If the database itself is corrupt, preserve it and the project for manual recovery. There is no automatic repair command.

At the storage limit, stop clients and archive the state directory securely. Starting a fresh journal intentionally loses access to old undo history. This is a manual retention decision, never an automatic action.

## Reporting a vulnerability

Use this repository's private GitHub security reporting if enabled. If unavailable, open an issue asking for a private contact without posting exploit details or private files. Include the version, platform, and a synthetic reproduction when sharing details privately.
