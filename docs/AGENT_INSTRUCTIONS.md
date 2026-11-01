# Connecting an agent to UndoWeave

UndoWeave runs as a local **stdio** MCP server. There is no network listener.
The project root (and optional state directory) are fixed on the command line
when the server starts; no tool call can change them.

Install first: [INSTALL.md](INSTALL.md).

## Easiest: let UndoWeave print your configuration

Run `config` with the interpreter you installed into. It fills in that
interpreter's absolute path and your project root. **It only prints; it never
edits any client configuration.** You review and apply the output yourself.

```sh
ABS_PYTHON -I -m undoweave --root ABS_PROJECT config                    # generic {"mcpServers": ...} JSON
ABS_PYTHON -I -m undoweave --root ABS_PROJECT config --client claude    # a `claude mcp add ...` command line
ABS_PYTHON -I -m undoweave --root ABS_PROJECT config --client codex     # a `codex mcp add ...` command and a config.toml table
```

Add `--state-dir ABS_STATE_DIR` before `config` to pin the journal location too.
If that interpreter cannot import `mcp` 2.x yet, `config` warns on stderr.

The generated launch includes Python's `-I` isolated mode. Keep it: files in
the selected project and its `PYTHONPATH` must not replace the installed server
or SDK during startup. Install the package into the chosen interpreter first.

### Which shell the printed commands are for

The `claude` and `codex` command lines are quoted for **one specific shell**.
Paste them only into that shell:

- **Windows: PowerShell** (Windows PowerShell 5.1 or PowerShell 7+). Every
  argument is a single-quoted literal (`'...'`, with apostrophes doubled), so
  characters such as `$`, `$()`, backticks, `;`, `&`, `@` and `'` in a path are
  passed through verbatim and never expanded or executed. The output is **not**
  valid for `cmd.exe`.
- **macOS / Linux: POSIX shells** (sh, bash, zsh), quoted with Python's
  `shlex.join`.

A few argument shapes cannot reach a program unchanged in every PowerShell
version, even when quoted: an argument that is exactly `--%`, an empty
argument, one containing `"`, or one that contains whitespace and ends in `\`
(for example a UNC share root like `\\server\share name\`). For those,
`config --client claude|codex` refuses with `unsafe_shell_argument`. Use the
generic JSON or the Codex `config.toml` table instead; neither goes through a
shell.

## Writing the configuration by hand

In the commands below, replace:

- `ABS_PYTHON`: absolute path to the Python interpreter where you installed
  `undoweave[mcp]` (for example `/home/me/.undoweave-venv/bin/python` or
  `C:\Users\me\.undoweave-venv\Scripts\python.exe`).
- `ABS_PROJECT`: absolute path to the project you want the agent to edit.

Both must be absolute. Relative paths are rejected. If you type commands by
hand, quote every path: single quotes in PowerShell (`'C:\My Code\app'`; write
an apostrophe inside as `''`) and single quotes in POSIX shells
(`'/home/me/my code'`).

## Claude Code

Run this from inside the project. `--scope project` writes the entry to the
project's `.mcp.json`, which you can review and commit. It does not touch your
user-level configuration.

macOS / Linux:

```sh
claude mcp add --transport stdio --scope project undoweave -- '/home/me/.undoweave-venv/bin/python' -I -m undoweave --root '/home/me/code/my project' serve
```

Windows (PowerShell):

```powershell
claude 'mcp' 'add' '--transport' 'stdio' '--scope' 'project' 'undoweave' '--' 'C:\Users\me\.undoweave-venv\Scripts\python.exe' '-I' '-m' 'undoweave' '--root' 'C:\Users\me\code\my project' 'serve'
```

Check it with `claude mcp list`. Claude Code asks you to approve project-scoped
servers the first time it starts them.

## Codex

Verified against the official Codex MCP documentation
(<https://learn.chatgpt.com/docs/extend/mcp?surface=cli>) and the local
`codex mcp add --help`: Codex supports `codex mcp add NAME -- COMMAND...`, and
reads stdio servers from `~/.codex/config.toml` tables named
`[mcp_servers.NAME]` with `command` and `args`.

Either add a table to `~/.codex/config.toml` (backslashes in Windows paths
must be doubled inside TOML double-quoted strings):

```toml
[mcp_servers.undoweave]
command = "C:\\Users\\me\\.undoweave-venv\\Scripts\\python.exe"
args = ["-I", "-m", "undoweave", "--root", "C:\\Users\\me\\code\\my project", "serve"]
```

or run the command (macOS / Linux):

```sh
codex mcp add undoweave -- '/home/me/.undoweave-venv/bin/python' -I -m undoweave --root '/home/me/code/my project' serve
```

Windows (PowerShell):

```powershell
codex 'mcp' 'add' 'undoweave' '--' 'C:\Users\me\.undoweave-venv\Scripts\python.exe' '-I' '-m' 'undoweave' '--root' 'C:\Users\me\code\my project' 'serve'
```

## Any other MCP client (generic config)

Most clients accept a `command` plus `args` entry. In JSON:

```json
{
  "mcpServers": {
    "undoweave": {
      "command": "ABS_PYTHON",
      "args": ["-I", "-m", "undoweave", "--root", "ABS_PROJECT", "serve"]
    }
  }
}
```

To keep the journal somewhere specific, add `"--state-dir", "ABS_STATE_DIR"`
before `"serve"`. It must be outside the project.

Do not put the server behind a shell wrapper. `serve` writes only MCP JSON-RPC
to stdout; diagnostics go to stderr.

## What the agent gets

Tools:

| Tool | What it does | Annotation hints |
| --- | --- | --- |
| `read_file` | Read a UTF-8 file; returns `content` and `sha256` (`missing` if absent) | read-only |
| `write_file` | Replace a whole file (or `delete=true`) and record the edit; needs `expected_hash` and `actor` | destructive |
| `list_changes` | Recorded edits and undos, newest first; no file contents | read-only |
| `preview_undo` | Plan undoing one edit; returns `ready` + diff + token, `conflict`, or `already_undone`. Doesn't change project files, but stores a short-lived plan in the state directory | not read-only, not destructive |
| `apply_undo` | Apply a previewed plan by its one-use token | destructive |
| `workspace_status` | Version, root, state dir, counts, journal health, limits | read-only |

Prompt: `safe_editing`. Resource: `undoweave://instructions` (same text).

**The annotations are hints for your MCP client's UI. They are not permissions
and they do not block anything.** What the agent may do is decided by you and
by your client's approval settings.

## Instructions to give the agent

You can paste this into your project instructions (for example `CLAUDE.md`), or
ask the agent to use the `safe_editing` prompt:

> Edit files in this project only with UndoWeave's `write_file` tool, using the
> fixed actor name `claude-code`. Always `read_file` first and pass its
> `sha256` as `expected_hash` (`missing` for a new file). If a write is refused
> as stale, read again and re-check before retrying. To undo, use
> `list_changes`, then `preview_undo`, show me the diff, and wait for my
> explicit confirmation before `apply_undo`. If the preview is a conflict, stop
> and explain it. There is no force option, so don't try to work around it.

## What UndoWeave does not do

- It only knows about edits made through `write_file`. An agent's built-in
  file tools, shell commands, and human editors are not recorded. A later
  undo tries to keep those changes, but they can't be undone through UndoWeave.
- It does not lock files. Someone can change a file between preview and apply.
  `apply_undo` re-checks the file hash and refuses if it changed, but an edit
  that lands after that check is a residual race.
- It refuses binary files, symlinks/reparse points, hard-linked files, files
  over the size limits, protected paths (credentials, VCS metadata, the journal,
  dependency directories), and anything outside the pinned root.
- If later edits overlap the edit you want to undo, or the alignment is
  ambiguous, it refuses rather than guessing.

## CLI equivalents

The same operations are available without MCP, each printing JSON:

Quote paths as described above if they contain spaces or shell characters.

```sh
python -I -m undoweave --root ABS_PROJECT read notes.txt
python -I -m undoweave --root ABS_PROJECT edit notes.txt --content-file ABS_NEW_CONTENT --actor me --expected-hash SHA256
python -I -m undoweave --root ABS_PROJECT edit notes.txt --delete --actor me --expected-hash SHA256
python -I -m undoweave --root ABS_PROJECT history --limit 20
python -I -m undoweave --root ABS_PROJECT preview EDIT_ID
python -I -m undoweave --root ABS_PROJECT undo TOKEN
python -I -m undoweave --root ABS_PROJECT status
python -I -m undoweave --root ABS_PROJECT config [--client generic|claude|codex]
python -I -m undoweave demo
```

`--content-file` must be a regular file (symlinks, reparse points, FIFOs,
devices and directories are refused before they are opened). `--content-file -`
reads the new content from stdin. Either way the CLI reads at most the core
size limit (1 MiB) plus one byte and refuses anything larger with
`content_limit`. The CLI never runs shell commands or executes file content.
Exit codes: 0 ok, 1 refused by UndoWeave, 2 usage or input error,
3 `mcp` 2.x extra missing (for `serve`), 4 demo check failed.
