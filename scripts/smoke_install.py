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
        assert {tool.name for tool in tools} == {"read_file", "write_file", "replace_text", "list_changes", "inspect_change", "preview_undo", "apply_undo", "workspace_status"}
        read = (await client.call_tool("read_file", {"path": "sample.txt"})).structured_content
        changed = await client.call_tool("write_file", {"path": "sample.txt", "expected_hash": read["sha256"], "actor": "smoke-agent", "content": read["content"].replace("agent=old", "agent=new")})
        assert not changed.is_error
        evidence = await client.call_tool("inspect_change", {"change_id": changed.structured_content["id"]})
        assert not evidence.is_error
        assert evidence.structured_content["before_hash"] == read["sha256"]
        target.write_bytes(target.read_bytes().replace(b"human=old", b"human=kept"))
        preview = await client.call_tool("preview_undo", {"edit_id": changed.structured_content["id"]})
        assert preview.structured_content["status"] == "ready"
        applied = await client.call_tool("apply_undo", {"token": preview.structured_content["token"]})
        assert not applied.is_error
        assert target.read_bytes() == b"agent=old\nseparator=keep\nhuman=kept\n"
    config = subprocess.run([sys.executable, "-I", "-m", "undoweave", "--root", str(project), "config"], check=True, capture_output=True, encoding="utf-8", timeout=30)
    assert json.loads(config.stdout)["mcpServers"]["undoweave"]["command"] == sys.executable

    # Exercise the exact generated setup connection, from a nested project cwd.
    selected = root / 'setup project'
    (selected / 'src').mkdir(parents=True)
    (selected / '.git').mkdir()
    outside = selected / 'src/app.py'
    outside.write_bytes(b'value = 1\n')
    (selected / 'src/undoweave.py').write_text('raise RuntimeError("project import shadow")')
    selected_state = root / 'setup state'
    before_names = sorted(p.relative_to(selected).as_posix() for p in selected.rglob('*'))
    setup = subprocess.run([sys.executable, '-I', '-m', 'undoweave', '--state-dir', str(selected_state),
        'setup', '--interval', '.25'], cwd=selected / 'src', check=True, capture_output=True,
        encoding='utf-8', timeout=30)
    plan = json.loads(setup.stdout)
    assert plan['root'] == str(selected) and plan['scopes'] == ['src']
    assert not plan['recording_started'] and not plan['configuration_changed']
    assert not selected_state.exists() and not (selected / '.undoweave-id').exists()
    assert before_names == sorted(p.relative_to(selected).as_posix() for p in selected.rglob('*'))
    entry = plan['mcpServers'][plan['server_name']]
    assert entry['command'] == sys.executable and entry['args'][:3] == ['-I', '-m', 'undoweave']
    async with Client(StdioServerParameters(command=entry['command'], args=entry['args'], cwd=selected / 'src')) as client:
        status = (await client.call_tool('workspace_status')).structured_content
        assert status['root'] == str(selected) and status['observation']['scopes'] == ['src']
        assert not (await client.call_tool('list_changes')).structured_content['changes']
        outside.write_bytes(b'value = 2\n')
        deadline = asyncio.get_running_loop().time() + 15
        while True:
            rows = (await client.call_tool('list_changes')).structured_content['changes']
            if rows:
                break
            assert asyncio.get_running_loop().time() < deadline, 'Generated setup recorder did not retain the outside save'
            await asyncio.sleep(.1)
        assert len(rows) == 1 and rows[0]['path'] == 'src/app.py' and rows[0]['actor'] == 'unknown'
        evidence = (await client.call_tool('inspect_change', {'change_id': rows[0]['id']})).structured_content
        assert '-value = 1' in evidence['diff'] and '+value = 2' in evidence['diff']
        assert not evidence['undo_supported'] and outside.read_bytes() == b'value = 2\n'
    return {"ok": True, "version": undoweave.__version__, "installed_package": str(Path(undoweave.__file__).resolve()), "mcp_tools": len(tools), "human_edit_preserved": True, "generated_config_valid": True, "project_setup_valid": True, "generated_setup_connection_observes_outside_save": True}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temporary:
        result = asyncio.run(asyncio.wait_for(check(Path(temporary).resolve()), timeout=90))
    print(json.dumps(result, indent=2))
