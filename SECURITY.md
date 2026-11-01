# Security boundaries

UndoWeave is an early local file-editing utility. Keep a separate backup or Git commit. Its checks reduce particular risks; they do not prove absence of vulnerabilities.

## Intended environment

One trusted local user, an explicitly chosen project directory, and a private state directory shared by cooperating UndoWeave processes. The MCP server only uses stdio. There is no HTTP transport, command runner, plugin loader, telemetry or model API. Optional startup-pinned recording observes selected paths while its process is running; it is off by default.

The MCP client and its agent are trusted with file-editing access to the selected root. Tool annotations and agent instructions are advisory. The server cannot enforce that a human approved a tool call; anyone with access to the MCP connection can call write/apply. A preview token binds an operation to a recent reviewable state; it is not human authentication. Do not give an untrusted remote client access to this server.

## Protection implemented

- Project-relative forward-slash paths, with no traversal, absolute paths, Windows streams, reserved device names, trailing dots/spaces or control characters.
- Generated server commands use Python isolated mode (`-I`), keeping the project working directory, `PYTHONPATH` and user site packages out of server imports. Keep this flag in manual configurations: project files named `undoweave.py`, `mcp.py` or `sitecustomize.py` must not impersonate installed code.
- Credential-like names, dependency directories, journal files and version-control directories are denied. This list cannot identify every secret in an otherwise ordinary file.
- All existing path components are inspected for links and Windows reparse points. Selected files must be regular and single-linked. Open descriptors are checked against their initial identity.
- Writes require an expected content hash. Undo additionally requires a fresh, one-use preview token and unchanged same-file recorded history. Preview result text is recomputed at apply time.
- Only shared lines occurring exactly once in all three snapshots, in unchanged order, separate edits for selective undo. If both versions changed a region between those anchors, the entire undo refuses. Adjacent edits, repeated braces and blank lines can require manual review even when changes look separate. Comparison/review limits also refuse without changing project files.
- Same-journal operations hold an OS file lock across journal commits and file replacement. Temporary file creation is exclusive; content is flushed before atomic replacement. Existing file permission bits are preserved on replacement.
- SQLite uses full synchronization. A durable pending intent is committed before any project write. Interrupted operations whose current bytes match the recorded before/after state can be classified on restart. An unknown result blocks future mutations for manual investigation.
- Known preflight failures are marked aborted while holding the lock. A filesystem replacement refusal is also marked aborted only when a locked re-read verifies the exact before bytes. Otherwise the intent remains for recovery; an exception type alone never establishes whether a write happened. Status and history remain available when pending content cannot be classified; mutations remain blocked.
- The state directory is resolved and checked by path and directory identity before creation, so spelling aliases cannot place snapshots inside the project. Preview control/direction characters are shown as visible escapes.
- Journal bindings include the canonical root, directory identity and a protected random `.undoweave-id` marker. Directory and marker identity are checked per operation; each database connection also verifies its binding. Ordinary directory replacement and foreign-backup swaps refuse even if selected file bytes match an old snapshot.
- The marker is source-free local metadata created on first initialization, including server startup. Keep it private to the workspace and exclude it from Git. It is not an authentication credential; deliberately copying metadata or hostile same-user filesystem changes remain outside sandbox guarantees. A nonempty legacy journal requires [explicit reviewed adoption](docs/MIGRATION.md), never a tool-triggered automatic migration.
- Preview records contain result hashes rather than duplicate source snapshots. Used/expired plans are collected before capacity checks; free database pages are reusable. Writes reserve headroom for SQLite's status/link updates before mutating a project file.

## Explicit exclusions

UndoWeave is not a sandbox against malware or a hostile process running as the same OS user. Such a process can change files, directory components or the journal between checks. Outside editors do not honor its lock. Final hash/path checks narrow but do not eliminate the race immediately before replace/unlink. Using multiple different journals for the same project also defeats shared coordination.

Privileged filesystem mounts can make an otherwise external state directory reachable inside a project. Do not mount the state tree into the project. Filesystems with unreliable directory identities can cause conservative false refusals. Symlinked root/home paths are unsupported; choose their actual resolved location.

Hashes detect mismatches, not malicious journal rewriting by the same user. Actor labels are self-reported. Optional observations retain net outside changes with unknown authors, not complete actions or intent. Fast save/revert sequences can be indistinguishable from no change. Text movement or human recreation of identical content may also be indistinguishable.

## Optional recording

The operator enables recording with explicit relative scopes in `watch` or
`serve --watch`. MCP tools cannot enable or widen it. Only one recorder holds
the journal's separate observation lease. It does not lock outside editors.
Scanning and source retention are bounded and exclude protected paths, links,
artifacts and conservative positive project ignore rules. These exclusions
cannot identify every secret inside ordinary source. See [recording limits](docs/RECORDING.md).

Observed entries are inserted as `observed`, never pending write intents or
applied edits. Recovery does not pretend the recorder wrote them, and undo
explicitly refuses them. They retain before/after snapshots for inspection,
including intervals and unknown attribution. Inspection returns an escaped,
bounded diff; the standalone timeline remains source-free. No check result,
command output or private model reasoning is collected. Persisted session
state is a last report, not independently verified process liveness.

An incomplete scan is not evidence of deletion. An interrupted recorder or
restart leaves coverage limits; a new baseline never fabricates missing
history. Atomic-save identity checks reduce accidental stale captures, but
same-user hostile races and filesystem blocking remain outside a sandbox
guarantee. Recording adds to plaintext source retention; keep scopes and the
state directory private.

There is no atomic transaction spanning several files. This release tracks text, not complete filesystem metadata. Creating or recreating a file uses owner-only mode rather than restoring its old executable bits. Atomic replacement does not preserve custom Windows ACLs, hidden/system attributes, ownership, extended attributes or alternate streams. Existing POSIX mode bits are preserved for replacements; review permissions after recovery when they matter.

Filesystem and SQLite durability depend on the OS/filesystem and storage hardware. A process crash or failed temporary-file cleanup can leave an orphan `.undoweave-write-*` file containing source beside the target. Inspect and remove only confirmed orphan files when UndoWeave is stopped.

## Local source retention

Before/after snapshots are plaintext in `journal.sqlite3`; preview records store hashes rather than additional source copies. State is outside the project by default under `%LOCALAPPDATA%/UndoWeave/workspaces/` on Windows, or `~/.local/state/UndoWeave/workspaces/` elsewhere. `workspace_status` reports the exact directory. A supplied state directory must be outside the project.

POSIX state directories/files require owner-only permissions. On Windows the state directory inherits its parent's ACL; Python mode bits do not configure an owner-only NTFS ACL. Choose a private user-owned location and review the ACL for your environment. Snapshots are not encrypted, automatically expired or securely erased. Preview tokens expire after five minutes, but undo history persists until you deliberately archive or remove the journal.

Do not share journal files, source diffs, tokens, credentials or raw private captures in public issues. `read_file`, `inspect_change` and preview diffs are returned to the MCP client, which may send them to its model provider. No source is uploaded by the core itself.

## Interrupted write recovery

1. Stop every client connected to the workspace. Preserve a copy of the current project and state directory.
2. Restart using the same root and state directory. A pending operation matching its before bytes is marked aborted; one matching its after bytes is marked applied.
3. If `recovery_required` is returned, the file matches neither snapshot. Do not delete the journal, reset files, or force a write to clear the error. Compare the saved before/after snapshots and current file locally with a trusted developer. The journal is an SQLite database; keep all examination private.
4. If the database itself is corrupt, preserve it and the project for manual recovery. There is no automatic repair command.

At the storage limit, stop clients and archive the state directory securely. Starting a fresh journal intentionally loses access to old undo history. This is a manual retention decision, never an automatic action.

## Reporting a vulnerability

Use this repository's private GitHub security reporting if enabled. If unavailable, open an issue asking for a private contact without posting exploit details or private files. Include the version, platform, and a synthetic reproduction when sharing details privately.
