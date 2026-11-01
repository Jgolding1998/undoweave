# Installing UndoWeave

UndoWeave needs **Python 3.11 or newer**. The core and CLI have no third-party
runtime dependencies. The MCP server needs the optional `mcp` extra, which pins
the official MCP Python SDK at exactly `mcp==2.3.0`.

Install into a dedicated virtual environment so your MCP client can point at
one fixed interpreter.

## 1. Install the pinned release

macOS / Linux:

```sh
python3 -m venv ~/.undoweave-venv
~/.undoweave-venv/bin/python -m pip install "undoweave[mcp] @ git+https://github.com/Jgolding1998/undoweave@v0.3.0"
```

Windows (PowerShell):

```powershell
py -3 -m venv $HOME\.undoweave-venv
& $HOME\.undoweave-venv\Scripts\python.exe -m pip install "undoweave[mcp] @ git+https://github.com/Jgolding1998/undoweave@v0.3.0"
```

This installs the `v0.3.0` release tag. Pin a release tag or a full commit
SHA (`...undoweave@<40-character-sha>`), never a branch. A SHA is the strictest
pin because a tag can in principle be moved. Leave out `[mcp]` if you only
want the CLI.

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

## 3. Connect your agent

To observe outside-tool saves, append `--watch src --watch tests` after
`config` in a command below, choosing your actual project paths. The printed
configuration pins those scopes for the server's lifetime. Recording is off
when `--watch` is omitted. Run only one recorder per shared journal.

Try `python -I -m undoweave demo --recording` in the installed environment for
a disposable outside-save and restart example. See [RECORDING.md](RECORDING.md)
before selecting paths: source is retained privately and observations have
unknown authors, capture gaps and no selective undo.

Let UndoWeave print the exact configuration for the interpreter you just
installed into. It prints instructions only and never changes any configuration:

```sh
~/.undoweave-venv/bin/python -I -m undoweave --root /ABS/PATH/TO/project config                   # generic mcpServers JSON
~/.undoweave-venv/bin/python -I -m undoweave --root /ABS/PATH/TO/project config --client claude   # Claude Code command
~/.undoweave-venv/bin/python -I -m undoweave --root /ABS/PATH/TO/project config --client codex    # Codex command / config.toml
```

See [AGENT_INSTRUCTIONS.md](AGENT_INSTRUCTIONS.md) for details and what to tell
the agent.

## From a local checkout (development)

```sh
cd /ABS/PATH/TO/undoweave
python3 -m venv .venv
.venv/bin/python -m pip install ".[mcp]"        # Windows: .venv\Scripts\python.exe -m pip install ".[mcp]"
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

The MCP tests start a real `serve` subprocess and talk to it through the
official mcp 2.x SDK `Client` over stdio. They are skipped if `mcp` 2.x is not
installed. No test uses the network or a model API.

## If `serve` says the MCP dependency is missing

```
The MCP server needs the optional 'mcp' dependency (mcp==2.3.0). Install it with:
    python -m pip install ".[mcp]"
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
