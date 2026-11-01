"""Launch the actual generated MCP config from an untrusted-looking project.

The shadow modules are harmless fixtures. If imported they only write markers
inside this test's TemporaryDirectory and raise an exception. No real user
configuration, credentials, files, network, or agent model is accessed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
if MCP_AVAILABLE:
    # A present but incompatible SDK must fail rather than silently skip.
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client


@unittest.skipUnless(MCP_AVAILABLE, "install the optional 'mcp' extra for real MCP startup checks")
class IsolatedStartupTests(unittest.TestCase):
    def test_generated_config_ignores_project_and_pythonpath_shadow_modules(self):
        with tempfile.TemporaryDirectory(prefix="undoweave-isolated-startup-") as temporary:
            base = Path(temporary)
            root = base / "untrusted-project"
            root.mkdir()
            state = base / "state"
            expected = "The installed UndoWeave server read this fixture.\n"
            (root / "fixture.txt").write_bytes(expected.encode("utf-8"))
            markers = []
            for module in ("undoweave", "mcp", "sitecustomize"):
                marker = base / (module + ".imported")
                markers.append(marker)
                source = (
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('benign shadow-module probe', encoding='utf-8')\n"
                    "raise RuntimeError('benign test shadow imported')\n"
                )
                (root / (module + ".py")).write_text(source, encoding="utf-8")

            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(root)

            # Obtain command/args from the public CLI, not from a test-built
            # approximation. Isolate this config-generation invocation too.
            generated = subprocess.run(
                [sys.executable, "-I", "-m", "undoweave", "--root", str(root),
                 "--state-dir", str(state), "config"],
                cwd=root, env=environment, capture_output=True, text=True,
                encoding="utf-8", timeout=20,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            config = json.loads(generated.stdout)["mcpServers"]["undoweave"]
            self.assertEqual(config["args"][:3], ["-I", "-m", "undoweave"])
            self.assertTrue(Path(config["command"]).is_absolute())
            for marker in markers:
                self.assertFalse(marker.exists(), f"config generation imported {marker.stem}")

            parameters = StdioServerParameters(
                command=config["command"], args=config["args"], env=environment, cwd=root,
            )
            stderr_path = base / "server-stderr.log"

            async def connect_and_read():
                with stderr_path.open("w", encoding="utf-8") as error_log:
                    async with Client(stdio_client(parameters, errlog=error_log)) as client:
                        self.assertEqual(client.server_info.name, "undoweave")
                        tools = {tool.name for tool in (await client.list_tools()).tools}
                        self.assertIn("read_file", tools)
                        result = await client.call_tool("read_file", {"path": "fixture.txt"})
                        self.assertFalse(result.is_error, result.content)
                        payload = result.structured_content
                        if payload is None:
                            payload = json.loads(result.content[0].text)
                        self.assertEqual(payload["content"], expected)
                        self.assertTrue(payload["exists"])

            asyncio.run(asyncio.wait_for(connect_and_read(), timeout=30))
            for marker in markers:
                self.assertFalse(marker.exists(), f"server startup imported shadow module {marker.stem}")
            self.assertFalse((root / ".mcp.json").exists(), "test changed a project MCP configuration")
            self.assertEqual((root / "fixture.txt").read_bytes(), expected.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
