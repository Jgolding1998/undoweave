"""stdio MCP adapter for UndoWeave, built on the official ``mcp`` SDK 2.x (MCPServer).

The project root and state directory are pinned when the server starts; no tool
accepts a root, state directory, shell command, or "force" switch. All protocol
handling is done by the SDK. When serving, stdout carries only MCP JSON-RPC
messages; diagnostics go to stderr.

Tool annotations (readOnlyHint, destructiveHint, ...) are *hints* for MCP
clients. They are not a permission system and do not stop any call.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from undoweave.cli import load_core

SERVER_NAME = "undoweave"

SAFE_EDITING_TEXT = """\
# Editing safely with UndoWeave

UndoWeave records the edits you make *through its tools* so that one edit can
later be undone without discarding other people's work. It only knows about
edits made through `write_file`. Edits made any other way (your own built-in
file tools, shell commands, a human's editor) are not recorded and cannot be
undone by UndoWeave, although a later undo will try to keep them intact.

Follow these rules:

1. Edit files in this project only with the `write_file` tool. Do not use other
   file-writing tools or shell commands for files you want to be undoable.
2. Use one fixed `actor` name for the whole session (for example
   `claude-code`), so the history shows who made each edit. Do not impersonate
   a human or another agent.
3. Always call `read_file` first and pass the `sha256` it returns as
   `expected_hash`. For a file that does not exist yet, use `missing`. If the
   write is refused because the hash changed, read the file again, re-check
   what changed (someone else may have edited it), and only then retry.
4. To undo, call `list_changes` to find the edit id, then `preview_undo`.
   Show the user the returned diff and status in plain words.
5. Get the user's explicit confirmation before calling `apply_undo`. A preview
   is not consent. Never apply an undo the user has not approved.
6. If `preview_undo` reports `conflict`, do not try to force it, and do not
   hand-edit the file to make the conflict disappear unless the user asks.
   There is no force option. Explain the conflict and let the user decide.
7. If `apply_undo` fails because the file changed after the preview, run
   `preview_undo` again and ask the user again.
8. Tool annotations such as "read-only" or "destructive" are hints for your
   client. They are not permissions. The user's instructions decide what you
   may do.

Limits to be honest about: UndoWeave handles UTF-8 text files inside the pinned
project root only. It refuses binary files, symlinks, protected paths, and
oversized files. It does not lock files against other editors, so a human can
still change a file between your preview and your apply; UndoWeave re-checks the
file hash at apply time and refuses if it changed.
"""

SERVER_INSTRUCTIONS = (
    "UndoWeave records UTF-8 text edits made through write_file and can undo a single "
    "recorded edit while keeping later, non-overlapping edits by others. Read before "
    "writing and pass expected_hash; preview_undo before apply_undo; get the user's "
    "confirmation before apply_undo. Annotations are hints, not permissions. See the "
    "'safe_editing' prompt or the undoweave://instructions resource."
)


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _ok(payload: Any) -> CallToolResult:
    structured = payload if isinstance(payload, dict) else {"result": payload}
    return CallToolResult(
        content=[TextContent(type="text", text=_json_text(structured))],
        structured_content=structured,
        is_error=False,
    )


def _error(code: str, message: str) -> CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return CallToolResult(
        content=[TextContent(type="text", text=_json_text(payload))],
        structured_content=payload,
        is_error=True,
    )


def build_server(engine: Any) -> MCPServer:
    """Create an MCPServer (mcp 2.x) bound to an already-constructed Engine."""
    _Engine, UndoWeaveError = load_core()

    def guarded(call):
        try:
            return _ok(call())
        except UndoWeaveError as exc:
            code = str(getattr(exc, "code", "undoweave_error") or "undoweave_error")
            message = str(getattr(exc, "message", None) or exc) or code
            return _error(code, message)
        except Exception as exc:  # never leak tracebacks or file contents
            print(f"undoweave: internal error ({type(exc).__name__})", file=sys.stderr)
            return _error("internal_error", "UndoWeave hit an unexpected internal error; no details are exposed.")

    from undoweave import __version__

    mcp = MCPServer(
        SERVER_NAME,
        title="UndoWeave",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )

    read_only = dict(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    @mcp.tool(
        name="read_file",
        title="Read a project file",
        description=(
            "Read a UTF-8 text file relative to the pinned project root. Returns path, exists, "
            "content (null if missing) and sha256 ('missing' if absent). Use sha256 as "
            "expected_hash for write_file."
        ),
        annotations=ToolAnnotations(title="Read a project file", **read_only),
    )
    def read_file(path: str) -> CallToolResult:
        return guarded(lambda: engine.read(path))

    @mcp.tool(
        name="write_file",
        title="Write or delete a project file (recorded)",
        description=(
            "Replace the whole content of one UTF-8 text file (or delete it with delete=true) and "
            "record the edit so it can be undone later. Requires expected_hash: the sha256 from "
            "read_file, or 'missing' for a new file. Refused if the file changed since you read it. "
            "Use one fixed actor name for the session. Content is stored as text and never executed."
        ),
        annotations=ToolAnnotations(
            title="Write or delete a project file (recorded)",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def write_file(
        path: str,
        expected_hash: str,
        actor: str,
        content: str | None = None,
        delete: bool = False,
        label: str = "",
    ) -> CallToolResult:
        if delete and content is not None:
            return _error("invalid_arguments", "Pass either content or delete=true, not both.")
        if not delete and content is None:
            return _error("invalid_arguments", "content is required unless delete=true.")
        return guarded(
            lambda: engine.edit(path, None if delete else content, actor=actor, expected_hash=expected_hash, label=label)
        )

    @mcp.tool(
        name="list_changes",
        title="List recorded changes",
        description="List recorded edits and undos, newest first. Summaries only; no file contents.",
        annotations=ToolAnnotations(title="List recorded changes", **read_only),
    )
    def list_changes(limit: int = 50) -> CallToolResult:
        return guarded(lambda: {"changes": engine.history(limit)})

    @mcp.tool(
        name="preview_undo",
        title="Preview undoing one recorded edit",
        description=(
            "Plan the undo of one recorded edit without changing any project file. Returns status "
            "'ready' (with a bounded diff and a one-use, expiring token), 'conflict' (with a reason; "
            "nothing can be forced), or 'already_undone'. Stores the plan in UndoWeave's local state "
            "directory. Show the diff to the user and get confirmation before apply_undo."
        ),
        annotations=ToolAnnotations(
            title="Preview undoing one recorded edit",
            read_only_hint=False,  # writes a short-lived plan to the local state dir (not project files)
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def preview_undo(edit_id: str) -> CallToolResult:
        return guarded(lambda: engine.preview_undo(edit_id))

    @mcp.tool(
        name="apply_undo",
        title="Apply a previewed undo",
        description=(
            "Apply exactly the undo plan reviewed with preview_undo, identified by its one-use token. "
            "Only call this after the user has confirmed the previewed diff. Refused if the file or "
            "history changed since the preview; then preview again and re-confirm. No force option."
        ),
        annotations=ToolAnnotations(
            title="Apply a previewed undo",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def apply_undo(token: str) -> CallToolResult:
        return guarded(lambda: engine.apply_undo(token))

    @mcp.tool(
        name="workspace_status",
        title="UndoWeave workspace status",
        description="Version, pinned root, state directory, recorded count, journal health and limits. No file contents.",
        annotations=ToolAnnotations(title="UndoWeave workspace status", **read_only),
    )
    def workspace_status() -> CallToolResult:
        return guarded(engine.status)

    @mcp.prompt(
        name="safe_editing",
        title="Edit safely with UndoWeave",
        description="Instructions for editing through UndoWeave and undoing with user confirmation.",
    )
    def safe_editing() -> str:
        return SAFE_EDITING_TEXT

    @mcp.resource(
        "undoweave://instructions",
        name="instructions",
        title="UndoWeave safe-editing instructions",
        description="The same guidance as the safe_editing prompt.",
        mime_type="text/markdown",
    )
    def instructions() -> str:
        return SAFE_EDITING_TEXT

    return mcp


def serve(engine: Any) -> None:
    """Run the MCP server over stdio until the client disconnects."""
    build_server(engine).run(transport="stdio")
