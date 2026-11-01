# Installing UndoWeave

UndoWeave needs **Python 3.11 or newer**. The core and CLI have no third-party
runtime dependencies. The MCP server needs the optional `mcp` extra, which pins
the official MCP Python SDK at exactly `mcp==2.3.0`.

Install into a dedicated virtual environment so your MCP client can point at
one fixed interpreter.

## 1. Install the pinned release

macOS / Linux:

```sh
python3 -I -m venv ~/.undoweave-venv
~/.undoweave-venv/bin/python -I -m pip install "undoweave[mcp] @ git+https://github.com/Jgolding1998/undoweave@v0.4.0"
```

Windows (PowerShell):

```powershell
py -3 -I -m venv $HOME\.undoweave-venv
& $HOME\.undoweave-venv\Scripts\python.exe -I -m pip install "undoweave[mcp] @ git+https://github.com/Jgolding1998/undoweave@v0.4.0"
```

This installs the `v0.4.0` release tag, the first with `setup`. Pin a release tag or a full commit
SHA (`...undoweave@<40-character-sha>`), never a branch. A SHA is the strictest
pin because a tag can in principle be moved. Leave out `[mcp]` if you only
want the CLI.

`-I` (isolated mode) keeps Python from loading files such as `pip.py` or
`venv.py` from the folder you happen to be in. Keep it in every command here.

The `mcp` extra pins `mcp==2.3.0` exactly. pip resolves that package's own
dependencies; they are not hash-pinned by this command. If you need fully
hash-pinned installs, generate a lock file yourself and install with
`pip install --require-hashes -r requirements.txt`.

## 2. Check it works

```sh
~/.undoweave-venv/bin/python -I -m undoweave demo          # Windows: & $HOME\.undoweave-venv\Scripts\python.exe -I -m undoweave demo
```

`demo` runs only inside a throwaway temporary directory. It records edits by
agent A and agent B, makes an unrecorded "human" edit, undoes agent A, and
checks that the human and agent B work survive byte-for-byte (including CRLF
line endings). It also checks that an overlapping edit is refused as a
conflict and that a stale undo token is refused, both without changing the
file. It prints a JSON report and exits 0 only if every check passed.

## 3. Pick the project and connect your agent

`setup` is new in version 0.4.0 (the `setup` command in
`src/undoweave/cli.py`; see the [release notes](CHANGELOG.md)). Version 0.3
answers it with `invalid choice: 'setup'`; there, use the `config` commands
further down.

Open a terminal in the project you want UndoWeave to cover, then run:

```sh
~/.undoweave-venv/bin/python -I -m undoweave setup --client claude   # or --client codex
```

Windows (PowerShell):

```powershell
& $HOME\.undoweave-venv\Scripts\python.exe -I -m undoweave setup --client claude
```

It first prints what it chose, for example:

```text
Project: /home/me/code/shop (git_marker: .git)
Recording paths: manage.py, shop_core, tests
Connection name: undoweave-shop-537c26f5
Tools can read/edit supported files inside this project; only the listed paths are observed.
Nothing has been connected or recorded yet.
```

Then it prints what to apply:

- **Claude Code:** a `Set-Location` (PowerShell) or `cd` line for the project,
  then a `claude mcp add --scope project ...` command, which writes `.mcp.json`
  in that folder.
- **Codex:** a `[mcp_servers.NAME]` table to merge into the project's own
  `.codex/config.toml`. Codex reads that file only for trusted projects.
- **No `--client`:** one JSON object with the same plan (`root`,
  `root_detection`, `scopes`, `server_name`) and an `mcpServers` entry.

Setup reads only names and file types plus the project's `.gitignore`, never
project source contents. It does not start recording, create history or change any
client settings. Once connected, the tools can read and edit supported files
anywhere in the project; the recording paths only decide which outside saves
are observed.

How it picks:

- **Project:** the nearest folder upward with a project file (such as
  `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`) or a `.git`. In a
  monorepo, starting inside a package selects that package. A linked or
  otherwise unusual project file stops setup instead of being skipped. To
  choose yourself, put `--root ABS_PROJECT` before `setup`.
- **Recording paths:** common top-level source folders (`src`, `tests`, `app`,
  `lib` and similar), Python package folders (those with an `__init__.py`) and
  top-level code files, skipping hidden, ignored, dependency and
  credential-like entries. `--watch PATH` after `setup`
  (repeatable) records exactly the paths you name instead.
- **Connection name:** `undoweave-<folder>-<8 characters>`, the same every time
  for the same folder and different for other projects. `--name NAME` sets
  your own.

If it cannot decide, it stops without printing a configuration:
`setup_project_required` (no project found, or a home, drive or general files
folder was given), `setup_scope_required` (no code paths, or more than 16),
`setup_limit` (very large top-level folder) or `setup_scope_ignored` (a
`--watch` path is ignored by `.gitignore`). Rerun with `--root` or `--watch`.

### Apply it

1. Look at your existing connections first: the project's `.mcp.json` for
   Claude Code; the project's `.codex/config.toml` and your
   `~/.codex/config.toml` for Codex. Keep every unrelated entry. An entry for
   this same project can be updated; if the name points at a different folder,
   decide before changing anything.
2. Claude Code: run the printed `Set-Location`/`cd` line and then the
   `claude mcp add` command, in the shell named above them. Codex: add the
   printed table to the project's `.codex/config.toml`, keeping everything
   else in that file. Setup does not change whether Codex trusts the project.
3. Restart or reconnect the client. Claude Code asks you to approve a new
   project server. Starting it also starts recording the listed paths. Then ask
   the agent for `workspace_status` to see the root and recording paths.

Recording keeps plaintext copies of the selected files privately; its entries
have unknown authors and no selective undo. Read [RECORDING.md](RECORDING.md)
before choosing paths, and run only one recorder per project history.
`python -I -m undoweave demo --recording` shows a disposable example.

### Explicit paths with `config`

`config` prints configuration only for the paths you pass, with the entry name
`undoweave` unless you add `--name`. Its Codex output is a user-level
`codex mcp add` command or `~/.codex/config.toml` table, for one project you
choose by hand:

```sh
~/.undoweave-venv/bin/python -I -m undoweave --root /ABS/PATH/TO/project config --watch src   # generic JSON
~/.undoweave-venv/bin/python -I -m undoweave --root /ABS/PATH/TO/project config --client claude
~/.undoweave-venv/bin/python -I -m undoweave --root /ABS/PATH/TO/project config --client codex
```

See [AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md) for what to tell the agent.

## 4. Optional: add the UndoWeave skill

The skill is a short instruction file that helps an agent set up and use
UndoWeave. It is separate from the server: it grants no file access, and
installing the Python package does not install it.

Pick one folder:

| Client | Just for you | Only in one project |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/undoweave/` | `.claude/skills/undoweave/` |
| Codex | `~/.codex/skills/undoweave/` | |

The simplest way is to ask your agent:

> Install the UndoWeave skill from
> https://github.com/Jgolding1998/undoweave/tree/v0.4.0/skills/undoweave into
> my skills folder without replacing anything already there. Then set up
> UndoWeave for this project.

To copy it yourself, set `dest` to the folder you picked. These commands stop
without copying if `dest` already exists or the download fails, and they never
delete or overwrite anything. The temporary download is left for you to
remove.

macOS / Linux:

```sh
dest="$HOME/.claude/skills/undoweave"
if [ -e "$dest" ]; then
  echo "$dest already exists; nothing was changed."
else
  tmp="$(mktemp -d)" &&
  git clone --depth 1 --branch v0.4.0 https://github.com/Jgolding1998/undoweave "$tmp/undoweave" &&
  mkdir -p "$(dirname "$dest")" &&
  cp -R "$tmp/undoweave/skills/undoweave" "$dest" &&
  echo "Installed $dest (download left in $tmp)."
fi
```

Windows (PowerShell):

```powershell
$dest = Join-Path $HOME '.claude\skills\undoweave'
if (Test-Path -LiteralPath $dest) {
  "$dest already exists; nothing was changed."
} else {
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('undoweave-' + [guid]::NewGuid())
  git clone --depth 1 --branch v0.4.0 https://github.com/Jgolding1998/undoweave $tmp
  if ($LASTEXITCODE -ne 0) {
    "Download failed; nothing was copied."
  } else {
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $tmp 'skills\undoweave') -Destination $dest -Recurse
    "Installed $dest (download left in $tmp)."
  }
}
```

Start a new session so the client finds it.

## From a local checkout (development)

```sh
cd /ABS/PATH/TO/undoweave
python3 -I -m venv .venv
.venv/bin/python -I -m pip install ".[mcp]"     # Windows: .venv\Scripts\python.exe -I -m pip install ".[mcp]"
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

The MCP tests start a real `serve` subprocess and talk to it through the
official mcp 2.x SDK `Client` over stdio. They are skipped if `mcp` 2.x is not
installed. No test uses the network or a model API.

## If `serve` says the MCP dependency is missing

```
The MCP server needs the optional 'mcp' dependency (mcp==2.3.0). Install it with:
    python -I -m pip install "undoweave[mcp] @ git+https://github.com/Jgolding1998/undoweave.git@v0.4.0"
Use the same dedicated interpreter as this server; see docs/INSTALL.md.
```

This also appears if an older `mcp` 1.x is installed, because UndoWeave uses
the 2.x `MCPServer` API. Install the extra into **the same interpreter** that
your MCP client launches. For a release install, that means running the
step 1 command again.

## State directory

By default the journal lives outside the project, under
`%LOCALAPPDATA%\UndoWeave\workspaces\<hash>` on Windows or
`~/.local/state/UndoWeave/workspaces/<hash>` elsewhere. You can pass
`--state-dir ABS_PATH`; it must be an absolute path **outside** the project
root. Back it up if you rely on undo history.
