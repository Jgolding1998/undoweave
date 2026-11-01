# Upgrading a v0.1 journal

Version 0.2 binds a journal to the chosen directory's physical identity and a
local `.undoweave-id` nonce marker. This prevents recorded edits from one
directory instance being applied to a replacement project at the same path.
Every connection also verifies the journal binding, including when a running
client's database was replaced by a backup from another project.

## Fresh project or disposable demo

No migration is needed. First initialization creates a 33-byte, source-free
`.undoweave-id` file in the project and stores the matching binding in the
private journal. Preserve the marker and exclude it from version control. It
contains no credentials or snapshots. Removing or changing it stops operations;
it is not silently regenerated for an existing journal.

## Existing v0.1 history

A nonempty v0.1 journal has no trustworthy recorded directory identity. Version
0.2 therefore reports `legacy_journal` instead of guessing. It does not delete
or rewrite the history to bypass this check.

1. Stop **every old and new UndoWeave process** connected to this workspace. Do not run v0.1 and v0.2 against one journal concurrently. Preserve private copies of the journal and current project.
2. Verify locally that this history belongs to this exact project. Inspect
   paths, operation times and before/after snapshots with a trusted local SQLite
   viewer if necessary. Never upload the journal or its source snapshots.
3. Only after that review, explicitly adopt the old journal:

```sh
python -I -m undoweave --root /absolute/project --state-dir /private/old-state adopt-legacy --confirm-root /absolute/project
```

Use the same installed interpreter and absolute paths as your client. Windows
users can use the equivalent quoted paths in PowerShell. Omit `--state-dir`
only if the client uses the default state location. `--confirm-root` must name
the resolved chosen root; it confirms your decision, not an authenticated
proof that old history belongs there.

Adoption adds the identity binding and marker, preserves recorded history and
invalidates old preview tokens. Generate a fresh preview afterward. Pending
legacy operations require private manual recovery before adoption. A known
version-0.2 workspace mismatch cannot be overridden with this command.

There is deliberately no MCP adoption tool or force option. Agents must stop
on an identity error and let the operator review it.

## A genuinely different or moved project

Choose a new private state directory and keep the old journal intact. Do not
adopt unrelated history, copy a marker to bypass a refusal, or delete an old
journal just to clear an error. Moving or restoring projects may require a
fresh journal; this release does not automatically rebind their history.

Filesystems or Python upgrades that report a different directory identifier can
also trigger this conservative refusal. Preserve the old journal for private
examination; a new journal does not expose its undo history. Marker creation is
exclusive but an interruption during first initialization can leave an invalid
marker. Startup refuses it rather than silently replacing it. Stop clients and
preserve that file and state for manual investigation.
