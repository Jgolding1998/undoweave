"""Real subprocess MCP integration tests (official mcp 2.x SDK ``Client`` over stdio).

Skipped when the optional ``mcp`` package or the core engine is unavailable.
No network or model API is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.server.mcpserver import MCPServer  # noqa: F401  (mcp 2.x is required)

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from undoweave import cli  # noqa: E402

try:
    cli.load_core()
    CORE_AVAILABLE = True
except ImportError:
    CORE_AVAILABLE = False

EXPECTED_TOOLS = {"read_file", "write_file", "replace_text", "list_changes", "inspect_change", "preview_undo", "apply_undo", "workspace_status"}


def server_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def payload(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


@unittest.skipUnless(MCP_AVAILABLE, "optional 'mcp' 2.x package not installed (pip install .[mcp])")
@unittest.skipUnless(CORE_AVAILABLE, "undoweave core engine not importable yet")
class McpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        self.state = base / "state"
        self.errlog_path = base / "server-stderr.log"
        self.target = self.root / "app.cfg"
        self.original = b"alpha = 1\nbeta = 2\ngamma = 3\ndelta = 4\nepsilon = 5\nzeta = 6\neta = 7\n"
        self.target.write_bytes(self.original)

    def tearDown(self):
        self._tmp.cleanup()

    def server_args(self) -> list[str]:
        return ["-m", "undoweave", "--root", str(self.root), "--state-dir", str(self.state), "serve"]

    def run_session(self, body, extra_args=()):
        params = StdioServerParameters(command=sys.executable, args=[*self.server_args(), *extra_args], env=server_env())

        async def main():
            with open(self.errlog_path, "w", encoding="utf-8") as errlog:
                # Client(StdioServerParameters) has no errlog option, so pass the SDK's stdio
                # transport directly to capture the server's stderr.
                async with Client(stdio_client(params, errlog=errlog)) as client:
                    return await body(client)

        return asyncio.run(asyncio.wait_for(main(), timeout=120))

    def test_startup_opt_in_records_an_outside_edit_without_a_logging_tool_call(self):
        """The file is edited outside MCP; only startup config enables observation."""
        async def body(client):
            status = payload(await client.call_tool("workspace_status"))
            self.assertIn("observation", status)
            self.assertEqual(payload(await client.call_tool("list_changes"))["changes"], [])
            changed = self.original.replace(b"alpha = 1", b"alpha = 9")
            self.target.write_bytes(changed)
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                changes = payload(await client.call_tool("list_changes"))["changes"]
                if changes:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    self.fail("The startup-pinned recorder never observed the outside save")
                await asyncio.sleep(.1)
            self.assertEqual(len(changes), 1)
            row = changes[0]
            self.assertEqual(row["status"], "observed")
            self.assertEqual(row["actor"], "unknown")
            self.assertNotIn("before_text", row)
            self.assertNotIn("after_text", row)
            inspected = await client.call_tool("inspect_change", {"change_id": row["id"]})
            self.assertFalse(inspected.is_error)
            evidence = payload(inspected)
            self.assertIn("-alpha = 1", evidence["diff"])
            self.assertIn("+alpha = 9", evidence["diff"])
            self.assertEqual(evidence["check_evidence"], "not_captured")
            self.assertEqual(evidence["observation"]["time_basis"], "conservative_scan_bounds_not_exact_save_times")
            self.assertEqual(evidence["observation"]["snapshot_basis"], "per_file_not_atomic_multi_file")
            self.assertEqual(evidence["observation"]["order_basis"], "journal_sequence_not_actual_save_order")
            self.assertEqual(evidence["observation"], row["observation"])
            self.assertNotIn("token", evidence)
            refused = await client.call_tool("preview_undo", {"edit_id": row["id"]})
            self.assertTrue(refused.is_error)
            self.assertEqual(payload(refused)["error"]["code"], "observation_not_undoable")
            self.assertEqual(self.target.read_bytes(), changed)
            timeline = (await client.read_resource("undoweave://timeline")).contents[0].text
            self.assertNotIn("alpha = 9", timeline)
            self.assertNotIn(str(self.state), timeline)
            return row["id"]
        change_id = self.run_session(body, ["--watch", "app.cfg", "--interval", ".25"])
        # Persistence survives a new interpreter/client with no remembered conversation.
        async def restarted(client):
            evidence = payload(await client.call_tool("inspect_change", {"change_id": change_id}))
            self.assertIn("+alpha = 9", evidence["diff"])
        self.run_session(restarted)

    def assert_no_traceback(self, result):
        text = "".join(getattr(c, "text", "") for c in result.content)
        self.assertNotIn("Traceback", text)
        self.assertNotIn('File "', text)

    def test_tools_prompt_resource_and_annotations(self):
        async def body(client):
            self.assertEqual(client.server_info.name, "undoweave")
            tools = {t.name: t for t in (await client.list_tools()).tools}
            prompts = {p.name for p in (await client.list_prompts()).prompts}
            prompt = await client.get_prompt("safe_editing")
            resource = await client.read_resource("undoweave://instructions")
            timeline = await client.read_resource("undoweave://timeline")
            return tools, prompts, prompt, resource, timeline

        tools, prompts, prompt, resource, timeline = self.run_session(body)
        self.assertEqual(timeline.contents[0].mime_type, "text/html")
        self.assertIn("No changes in this snapshot", timeline.contents[0].text)
        self.assertNotIn(str(self.root), timeline.contents[0].text)
        self.assertNotIn(str(self.state), timeline.contents[0].text)
        self.assertEqual(set(tools), EXPECTED_TOOLS)
        for name in ("read_file", "list_changes", "inspect_change", "workspace_status"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.destructive_hint, name)
        for name in ("write_file", "replace_text", "apply_undo"):
            self.assertFalse(tools[name].annotations.read_only_hint, name)
            self.assertTrue(tools[name].annotations.destructive_hint, name)
        for tool in tools.values():
            schema = json.dumps(tool.input_schema)
            for forbidden in ("root", "state_dir", "command", "force"):
                self.assertNotIn(f'"{forbidden}"', schema, (tool.name, forbidden))
        self.assertIn("safe_editing", prompts)
        prompt_text = prompt.messages[0].content.text
        for phrase in ("expected_hash", "preview_undo", "confirmation", "not permissions", "no force"):
            self.assertIn(phrase, prompt_text)
        self.assertEqual(resource.contents[0].text, prompt_text)

    def test_undo_keeps_human_and_other_agent_edits(self):
        target = self.target
        original = self.original

        async def body(client):
            r = payload(await client.call_tool("read_file", {"path": "app.cfg"}))
            a = await client.call_tool("write_file", {
                "path": "app.cfg", "content": r["content"].replace("beta = 2", "beta = A"),
                "expected_hash": r["sha256"], "actor": "agent-a", "label": "A edit",
            })
            self.assertFalse(a.is_error, a.content)
            edit_a = payload(a)
            r = payload(await client.call_tool("read_file", {"path": "app.cfg"}))
            b = await client.call_tool("write_file", {
                "path": "app.cfg", "content": r["content"].replace("eta = 7", "eta = B"),
                "expected_hash": r["sha256"], "actor": "agent-b",
            })
            self.assertFalse(b.is_error, b.content)

            # External human edit, not recorded by UndoWeave.
            target.write_bytes(target.read_bytes().replace(b"delta = 4", b"delta = human"))

            changes = payload(await client.call_tool("list_changes", {"limit": 10}))["changes"]
            self.assertTrue(any(c.get("id") == edit_a["id"] for c in changes))

            before_preview = target.read_bytes()
            preview = await client.call_tool("preview_undo", {"edit_id": edit_a["id"]})
            self.assertFalse(preview.is_error, preview.content)
            preview = payload(preview)
            self.assertEqual(preview["status"], "ready")
            self.assertEqual(target.read_bytes(), before_preview)

            applied = await client.call_tool("apply_undo", {"token": preview["token"]})
            self.assertFalse(applied.is_error, applied.content)

            reused = await client.call_tool("apply_undo", {"token": preview["token"]})
            self.assertTrue(reused.is_error)
            self.assert_no_traceback(reused)

            status = await client.call_tool("workspace_status", {})
            self.assertFalse(status.is_error, status.content)

        self.run_session(body)
        expected = original.replace(b"delta = 4", b"delta = human").replace(b"eta = 7", b"eta = B")
        self.assertEqual(target.read_bytes(), expected)
        self.assertNotIn("Traceback", self.errlog_path.read_text(encoding="utf-8"))

    def test_partial_read_exact_replace_and_undo_acknowledgement(self):
        async def body(client):
            metadata = payload(await client.call_tool("read_file", {"path": "app.cfg", "metadata_only": True}))
            selected = payload(await client.call_tool("read_file", {"path": "app.cfg", "start_line": 2, "end_line": 2}))
            self.assertIsNone(metadata["content"])
            self.assertEqual(selected["content"], "beta = 2\n")
            self.assertEqual(selected["sha256"], metadata["sha256"])
            edited = await client.call_tool("replace_text", {"path": "app.cfg", "old_text": "beta = 2", "new_text": "beta = PATCH", "actor": "agent", "expected_hash": selected["sha256"]})
            self.assertFalse(edited.is_error, edited.content)
            operation = payload(edited)
            timeline = await client.read_resource("undoweave://timeline")
            timeline_html = timeline.contents[0].text
            self.assertIn("app.cfg", timeline_html)
            self.assertIn(operation["id"], timeline_html)
            self.assertNotIn("beta = PATCH", timeline_html)
            self.assertNotIn(str(self.root), timeline_html)
            self.assertNotIn(str(self.state), timeline_html)
            latest = payload(await client.call_tool("read_file", {"path": "app.cfg", "metadata_only": True}))
            self.assertEqual(latest["sha256"], operation["after_hash"])
            preview = payload(await client.call_tool("preview_undo", {"edit_id": operation["id"]}))
            undone = await client.call_tool("apply_undo", {"token": preview["token"]})
            self.assertFalse(undone.is_error, undone.content)
            self.assertTrue(payload(undone)["id"])
            self.assertEqual(payload(undone)["after_hash"], metadata["sha256"])
        self.run_session(body)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_stale_token_and_stale_hash_refused_without_writes(self):
        target = self.target

        async def body(client):
            r = payload(await client.call_tool("read_file", {"path": "app.cfg"}))
            edit = payload(await client.call_tool("write_file", {
                "path": "app.cfg", "content": r["content"].replace("gamma = 3", "gamma = A"),
                "expected_hash": r["sha256"], "actor": "agent-a",
            }))
            preview = payload(await client.call_tool("preview_undo", {"edit_id": edit["id"]}))
            self.assertEqual(preview["status"], "ready")

            # Human edits after the preview: the reviewed plan is stale.
            target.write_bytes(target.read_bytes().replace(b"alpha = 1", b"alpha = human"))
            before = target.read_bytes()
            stale = await client.call_tool("apply_undo", {"token": preview["token"]})
            self.assertTrue(stale.is_error)
            self.assertTrue(payload(stale)["error"]["code"])
            self.assert_no_traceback(stale)
            self.assertEqual(target.read_bytes(), before)

            # Write with an outdated expected_hash is refused.
            bad = await client.call_tool("write_file", {
                "path": "app.cfg", "content": "overwritten\n", "expected_hash": r["sha256"], "actor": "agent-a",
            })
            self.assertTrue(bad.is_error)
            self.assert_no_traceback(bad)
            self.assertEqual(target.read_bytes(), before)

            # Paths outside the pinned root are refused without leaking internals.
            outside = await client.call_tool("read_file", {"path": "../escape.txt"})
            self.assertTrue(outside.is_error)
            self.assert_no_traceback(outside)

            # Ambiguous arguments are refused by the adapter.
            both = await client.call_tool("write_file", {
                "path": "app.cfg", "content": "x", "delete": True, "expected_hash": "missing", "actor": "agent-a",
            })
            self.assertTrue(both.is_error)
            self.assertEqual(payload(both)["error"]["code"], "invalid_arguments")
            self.assertEqual(target.read_bytes(), before)

        self.run_session(body)
        self.assertNotIn("Traceback", self.errlog_path.read_text(encoding="utf-8"))

    def test_serve_stdout_is_only_jsonrpc(self):
        """Raw check (test-only) that every stdout line from `serve` is a JSON-RPC message."""
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "undoweave-test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        proc = subprocess.Popen(
            [sys.executable, *self.server_args()], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=server_env(),
        )
        try:
            lines = []
            for message in messages:
                proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                proc.stdin.flush()
                if "id" in message:
                    lines.append(proc.stdout.readline())
            proc.stdin.close()
            # Python 3.11 communicate() otherwise tries to flush closed stdin.
            proc.stdin = None
            rest, _err = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        lines.extend(rest.splitlines())
        parsed = [json.loads(line) for line in lines if line.strip()]
        self.assertGreaterEqual(len(parsed), 2)
        for item in parsed:
            self.assertEqual(item.get("jsonrpc"), "2.0")
        names = {t["name"] for t in parsed[1]["result"]["tools"]}
        self.assertEqual(names, EXPECTED_TOOLS)


if __name__ == "__main__":
    unittest.main()
