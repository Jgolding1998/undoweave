"""Command-line interface for UndoWeave.

Every command except ``config --client claude|codex`` (plain-text instructions)
prints one JSON document. Successful results go to stdout; errors go to stderr as
``{"ok": false, "error": {"code": ..., "message": ...}}`` with a non-zero exit
status. No command runs shell commands, executes file content, or changes any
MCP client configuration.

Exit codes: 0 success, 1 UndoWeave refused/failed, 2 usage error,
3 optional MCP dependency missing, 4 demo assertion failed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shlex
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

MCP_VERSION = "2.3.0"
MCP_MISSING_MESSAGE = (
    "The MCP server needs the optional 'mcp' dependency (mcp=={v}). Install it with:\n"
    "    python -m pip install \".[mcp]\"\n"
    "from an UndoWeave checkout, or see docs/INSTALL.md for the pinned release install."
).format(v=MCP_VERSION)


def load_core() -> tuple[type, type]:
    """Return (Engine, UndoWeaveError) from the core engine."""
    from undoweave.engine import Engine, UndoWeaveError

    return Engine, UndoWeaveError


class UsageError(Exception):
    def __init__(self, message: str, code: str = "usage"):
        super().__init__(message)
        self.code = code


class DemoFailure(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse would print usage text and exit 2
        raise UsageError(message)


def _abs_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError(f"must be an absolute path: {value!r}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="undoweave",
        description="Record UTF-8 text edits and undo one of them without discarding later work.",
    )
    parser.add_argument("--root", type=_abs_path, help="absolute path of the project root (pinned)")
    parser.add_argument("--state-dir", type=_abs_path, help="absolute path of the state directory (optional)")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)
    sub.required = True

    sub.add_parser("serve", help="run the MCP server over stdio (needs the 'mcp' extra)")

    p = sub.add_parser("read", help="read a file and print its sha256")
    p.add_argument("path")

    p = sub.add_parser("edit", help="record a whole-file write or delete")
    p.add_argument("path")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--content-file", help="UTF-8 file with the new content ('-' for stdin)")
    src.add_argument("--delete", action="store_true", help="delete the file")
    p.add_argument("--actor", required=True, help="who is making the edit")
    p.add_argument("--expected-hash", required=True, help="sha256 from 'read', or 'missing' for a new file")
    p.add_argument("--label", default="", help="short description of the edit")

    p = sub.add_parser("history", help="list recorded operations, newest first")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("preview", help="preview undoing one recorded edit (no project file changes)")
    p.add_argument("edit_id")

    p = sub.add_parser("undo", help="apply a previewed undo by its one-use token")
    p.add_argument("token")

    sub.add_parser("status", help="show version, root, state directory and journal health")

    sub.add_parser("demo", help="run a self-checking demo in a throwaway temporary directory")

    p = sub.add_parser(
        "config",
        help="print MCP client configuration for this interpreter (changes no configuration)",
    )
    p.add_argument(
        "--client", choices=("generic", "claude", "codex"), default="generic",
        help="generic: mcpServers JSON (default); claude/codex: copy-paste instructions",
    )
    return parser


def _emit(payload: Any, stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")
    stream.flush()


def _fail(code: str, message: str, exit_code: int) -> int:
    _emit({"ok": False, "error": {"code": code, "message": message}}, sys.stderr)
    return exit_code


def _content_limit() -> int:
    from undoweave.engine import MAX_BYTES

    return MAX_BYTES


def _read_bounded(handle, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes; refuse if there were more than ``limit``."""
    chunks, total = [], 0
    while total <= limit:
        chunk = handle.read(min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > limit:
        raise UsageError(f"--content-file exceeds the {limit}-byte limit", code="content_limit")
    return b"".join(chunks)


def _is_reparse_point(info: os.stat_result) -> bool:
    attrs = getattr(info, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _read_content_file(value: str) -> str:
    """Read new content without ever reading more than the core limit plus one byte.

    Only regular files are accepted: symlinks, reparse points, FIFOs, devices and
    directories are refused before opening, so a FIFO cannot block the CLI. ``-``
    reads stdin (bounded the same way).
    """
    limit = _content_limit()
    try:
        if value == "-":
            data = _read_bounded(sys.stdin.buffer, limit)
        else:
            path = Path(value)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or _is_reparse_point(info):
                raise UsageError("--content-file must not be a symlink or reparse point", code="unsafe_input")
            if not stat.S_ISREG(info.st_mode):
                raise UsageError("--content-file must be a regular file", code="unsafe_input")
            if info.st_size > limit:
                raise UsageError(f"--content-file exceeds the {limit}-byte limit", code="content_limit")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise UsageError("--content-file changed while it was being opened", code="unsafe_input")
                data = _read_bounded(handle, limit)
    except OSError as exc:
        raise UsageError(f"cannot read --content-file: {exc.strerror or type(exc).__name__}") from None
    try:
        return data.decode("utf-8")  # bytes decoded as-is: line endings are preserved
    except UnicodeDecodeError:
        raise UsageError("--content-file is not valid UTF-8 text", code="invalid_content") from None


def _make_engine(args: argparse.Namespace):
    if args.root is None:
        raise UsageError(f"--root is required for '{args.command}'")
    Engine, _ = load_core()
    return Engine(args.root, args.state_dir)


def _serve(args: argparse.Namespace) -> int:
    try:
        importlib.import_module("mcp.server.mcpserver")  # mcp 2.x API; absent in mcp 1.x
    except ImportError:
        print(MCP_MISSING_MESSAGE, file=sys.stderr)
        return 3
    try:
        from importlib.metadata import version

        installed = version("mcp")
        if installed != MCP_VERSION:
            print(f"undoweave: warning: tested with mcp=={MCP_VERSION}, found {installed}", file=sys.stderr)
    except Exception:
        pass
    engine = _make_engine(args)  # fail fast (before any MCP traffic) on a bad root
    from undoweave import mcp_server

    mcp_server.serve(engine)
    return 0


# --------------------------------------------------------------------------- demo

_DEMO_FILE = "settings.ini"
_DEMO_ORIGINAL = (
    "[app]\r\n"  # CRLF on purpose: line endings must survive undo byte-for-byte
    "name = demo\r\n"
    "mode = safe\r\n"
    "retries = 3\r\n"
    "timeout = 30\r\n"
    "color = blue\r\n"
    "size = medium\r\n"
    "owner = team\r\n"
    "[end]\r\n"
)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


def _run_demo() -> dict:
    Engine, UndoWeaveError = load_core()
    steps: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="undoweave-demo-") as tmp:
        root = Path(tmp) / "project"
        root.mkdir()
        state = Path(tmp) / "state"
        target = root / _DEMO_FILE
        target.write_bytes(_DEMO_ORIGINAL.encode("utf-8"))
        engine = Engine(root, state)

        def raw() -> str:
            return target.read_bytes().decode("utf-8")

        def record(actor: str, old: str, new: str, label: str) -> dict:
            current = engine.read(_DEMO_FILE)
            _check(old in current["content"], f"demo setup: {old!r} not found")
            result = engine.edit(
                _DEMO_FILE, current["content"].replace(old, new, 1), actor=actor,
                expected_hash=current["sha256"], label=label,
            )
            steps.append({"step": f"{actor} edit", "label": label, "result": result})
            return result

        def human(old: str, new: str, label: str) -> None:
            text = raw()
            _check(old in text, f"demo setup: {old!r} not found")
            target.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            steps.append({"step": "external human edit (not recorded)", "label": label})

        # 1. Agents A and B make recorded edits; a human edits the same file outside UndoWeave.
        edit_a = record("agent-a", "name = demo", "name = agent-a", "A renames app")
        record("agent-b", "owner = team", "owner = agent-b", "B sets owner")
        human("timeout = 30", "timeout = 99", "human raises timeout")

        # 2. Undo only agent A. Human and agent B work must survive, bytes and CRLF intact.
        preview = engine.preview_undo(edit_a["id"])
        steps.append({"step": "preview undo of agent-a", "result": preview})
        _check(preview.get("status") == "ready", f"expected ready preview, got {preview.get('status')!r}")
        applied = engine.apply_undo(preview["token"])
        steps.append({"step": "apply undo of agent-a", "result": applied})
        expected = (
            _DEMO_ORIGINAL.replace("timeout = 30", "timeout = 99").replace("owner = team", "owner = agent-b")
        )
        _check(raw() == expected, "undo of agent-a did not preserve human and agent-b edits exactly")
        steps.append({"step": "verified", "detail": "agent-a reverted; human and agent-b edits and CRLF preserved"})

        # 3. Conflict: the human overwrites the same line agent A just changed. Undo must refuse.
        edit_a2 = record("agent-a", "mode = safe", "mode = fast", "A speeds up")
        human("mode = fast", "mode = human-choice", "human overrides A's line")
        before = target.read_bytes()
        conflict = engine.preview_undo(edit_a2["id"])
        steps.append({"step": "preview undo of overlapped agent-a edit", "result": conflict})
        _check(conflict.get("status") == "conflict", f"expected conflict, got {conflict.get('status')!r}")
        _check(not conflict.get("token"), "conflict preview must not return a token")
        _check(target.read_bytes() == before, "conflict preview mutated the file")
        steps.append({"step": "verified", "detail": "conflict refused, file unchanged"})

        # 4. Stale token: a human edit between preview and apply invalidates the token.
        latest_b = record("agent-b", "size = medium", "size = large", "B enlarges")
        stale = engine.preview_undo(latest_b["id"])
        _check(stale.get("status") == "ready", f"expected ready preview, got {stale.get('status')!r}")
        human("color = blue", "color = green", "human edits after preview")
        before = target.read_bytes()
        try:
            engine.apply_undo(stale["token"])
        except UndoWeaveError as exc:
            steps.append({"step": "apply stale token refused", "error": {"code": getattr(exc, "code", None), "message": str(exc)}})
        else:
            raise DemoFailure("apply_undo accepted a token after the file changed")
        _check(target.read_bytes() == before, "refused apply mutated the file")
        steps.append({"step": "verified", "detail": "stale token refused, file unchanged; preview again to retry"})

        final = raw()
    return {"ok": True, "demo": "undoweave", "file": _DEMO_FILE, "final_content": final, "steps": steps}


# --------------------------------------------------------------------------- config


def _server_argv(args: argparse.Namespace) -> list[str]:
    if args.root is None:
        raise UsageError("--root is required for 'config'")
    if not args.root.is_dir():
        raise UsageError("--root must be an existing directory")
    # Ignore cwd, PYTHONPATH and user site: the selected project is data, not
    # a trusted location from which to import the installed server or its SDK.
    argv = [sys.executable, "-I", "-m", "undoweave", "--root", str(args.root)]
    if args.state_dir is not None:
        argv += ["--state-dir", str(args.state_dir)]
    return argv + ["serve"]


# PowerShell treats the ASCII apostrophe and these typographic quotes as single-quote
# delimiters; inside a single-quoted literal each one is escaped by doubling it.
_PS_SINGLE_QUOTES = ("'", "‘", "’", "‚", "‛")


def _powershell_quote(value: str) -> str:
    """Single-quoted PowerShell literal: no $, $(), backtick, ;, & or @ is interpreted."""
    for quote in _PS_SINGLE_QUOTES:
        value = value.replace(quote, quote + quote)
    return "'" + value + "'"


def _powershell_join(program: str, args: list[str]) -> str:
    """A PowerShell command line: a known bare program name, then literal arguments.

    Every argument, including ``--``, is a single-quoted literal, so PowerShell
    passes it to the program verbatim without expansion or command execution.
    """
    if not program.replace("-", "").isalnum():
        raise ValueError("program must be a plain command name")
    for arg in args:
        # Each case below is mis-passed to native programs by some PowerShell version even
        # when single-quoted (verified by test_cli against pwsh 7 and Windows PowerShell 5.1):
        # --% switches to verbatim mode; 5.1 drops empty strings, mangles embedded double
        # quotes, and mangles a trailing backslash in an argument containing whitespace.
        if arg == "--%" or arg == "" or '"' in arg or (arg.endswith("\\") and any(c.isspace() for c in arg)):
            raise ValueError(f"cannot quote {arg!r} safely for every PowerShell version")
    return " ".join([program, *(_powershell_quote(a) for a in args)])


def _posix_join(program: str, args: list[str]) -> str:
    return shlex.join([program, *args])


def _shell_join(program: str, args: list[str]) -> tuple[str, str]:
    """Return (shell description, command line) for the shell the user most likely has."""
    if os.name == "nt":
        return "PowerShell", _powershell_join(program, args)
    return "POSIX shell (sh, bash, zsh)", _posix_join(program, args)


def _toml_str(value: str) -> str:
    return json.dumps(value)  # JSON string escapes are valid TOML basic-string escapes


def _config(args: argparse.Namespace) -> None:
    """Print configuration only. Never writes any client configuration."""
    argv = _server_argv(args)
    command, server_args = argv[0], argv[1:]
    try:
        importlib.import_module("mcp.server.mcpserver")
    except ImportError:
        print("undoweave: warning: this interpreter cannot run 'serve' yet.\n" + MCP_MISSING_MESSAGE, file=sys.stderr)
    if args.client == "generic":
        _emit({"mcpServers": {"undoweave": {"command": command, "args": server_args}}})
        return
    note = (
        "UndoWeave did not change any configuration. Review the command, then run it yourself.\n"
        f"The interpreter above must have 'undoweave[mcp]' installed (mcp=={MCP_VERSION})."
    )
    if args.client == "claude":
        shell, line = _shell_join(
            "claude", ["mcp", "add", "--transport", "stdio", "--scope", "project", "undoweave", "--", *argv]
        )
        text = (
            "# Claude Code: run from inside the project. --scope project writes .mcp.json in the\n"
            "# current directory (review and commit it); it does not touch user-level config.\n"
            f"# Quoted for {shell}; do not paste into a different shell (for example cmd.exe).\n"
            f"{line}\n\n# Then check it with:\nclaude mcp list\n\n{note}\n"
        )
    else:
        shell, line = _shell_join("codex", ["mcp", "add", "undoweave", "--", *argv])
        toml = (
            "[mcp_servers.undoweave]\n"
            f"command = {_toml_str(command)}\n"
            "args = [" + ", ".join(_toml_str(a) for a in server_args) + "]\n"
        )
        text = (
            f"# Codex: either run this command (quoted for {shell}; do not paste into another shell):\n"
            f"{line}\n\n"
            "# or add this table to ~/.codex/config.toml:\n"
            f"{toml}\n{note}\n"
        )
    sys.stdout.write(text)
    sys.stdout.flush()


def _config_or_usage_error(args: argparse.Namespace) -> None:
    try:
        _config(args)
    except ValueError as exc:
        raise UsageError(
            f"{exc}. Use 'config' (generic JSON) or the Codex config.toml table instead of a shell command.",
            code="unsafe_shell_argument",
        ) from None


# --------------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "serve":
            return _serve(args)
        if args.command == "demo":
            if args.root is not None or args.state_dir is not None:
                raise UsageError("'demo' always uses a temporary directory; do not pass --root/--state-dir")
            _emit(_run_demo())
            return 0
        if args.command == "config":
            _config_or_usage_error(args)
            return 0
        if args.command == "edit":
            content = None if args.delete else _read_content_file(args.content_file)
            engine = _make_engine(args)
            result = engine.edit(args.path, content, actor=args.actor, expected_hash=args.expected_hash, label=args.label)
        else:
            engine = _make_engine(args)
            if args.command == "read":
                result = engine.read(args.path)
            elif args.command == "history":
                result = {"changes": engine.history(args.limit)}
            elif args.command == "preview":
                result = engine.preview_undo(args.edit_id)
            elif args.command == "undo":
                result = engine.apply_undo(args.token)
            elif args.command == "status":
                result = engine.status()
            else:  # pragma: no cover - argparse restricts choices
                raise UsageError(f"unknown command {args.command!r}")
        _emit(result)
        return 0
    except UsageError as exc:
        return _fail(exc.code, str(exc), 2)
    except DemoFailure as exc:
        return _fail("demo_failed", str(exc), 4)
    except ImportError as exc:
        return _fail("core_missing", str(exc), 1)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        try:
            _, UndoWeaveError = load_core()
        except ImportError:
            UndoWeaveError = ()
        if UndoWeaveError and isinstance(exc, UndoWeaveError):
            return _fail(str(getattr(exc, "code", "undoweave_error")), str(getattr(exc, "message", None) or exc), 1)
        return _fail("internal_error", f"unexpected {type(exc).__name__}; no details exposed", 1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
