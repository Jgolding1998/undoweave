"""Verify an installed wheel/Git package, without importing checkout src/.

Run with the fresh environment's Python and no PYTHONPATH override.
All file edits are isolated to TemporaryDirectory. No models or network calls.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp import Client, StdioServerParameters
import undoweave


async def check(root):
    project = root / "project"
    project.mkdir()
    target = project / "sample.txt"
    target.write_bytes(b"agent=old\nseparator=keep\nhuman=old\n")
    params = StdioServerParameters(command=sys.executable, args=["-I", "-m", "undoweave", "--root", str(project), "--state-dir", str(root / "state"), "serve"])
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {"read_file", "write_file", "list_changes", "preview_undo", "apply_undo", "workspace_status"}
        read = (await client.call_tool("read_file", {"path": "sample.txt"})).structured_content
        changed = await client.call_tool("write_file", {"path": "sample.txt", "expected_hash": read["sha256"], "actor": "smoke-agent", "content": read["content"].replace("agent=old", "agent=new")})
        assert not changed.is_error
        target.write_bytes(target.read_bytes().replace(b"human=old", b"human=kept"))
        preview = await client.call_tool("preview_undo", {"edit_id": changed.structured_content["id"]})
        assert preview.structured_content["status"] == "ready"
        applied = await client.call_tool("apply_undo", {"token": preview.structured_content["token"]})
        assert not applied.is_error
        assert target.read_bytes() == b"agent=old\nseparator=keep\nhuman=kept\n"
    config = subprocess.run([sys.executable, "-I", "-m", "undoweave", "--root", str(project), "config"], check=True, capture_output=True, encoding="utf-8", timeout=30)
    assert json.loads(config.stdout)["mcpServers"]["undoweave"]["command"] == sys.executable
    return {"ok": True, "version": undoweave.__version__, "installed_package": str(Path(undoweave.__file__).resolve()), "mcp_tools": len(tools), "human_edit_preserved": True, "generated_config_valid": True}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        result = asyncio.run(asyncio.wait_for(check(Path(temporary).resolve()), timeout=60))
    print(json.dumps(result, indent=2))
