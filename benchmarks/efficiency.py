"""Bounded merge and real-MCP body-size measurements in disposable projects.

No model calls, network access, package installs, or whole-workspace scans.
The baseline is this project's exact released merge.py, not a reimplementation.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "e37c81ad31cc2c8b26ad0c4a0849843f3ccc7626"
BASELINE_TAG = "v0.1.0"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_size(value) -> int:
    """Compact UTF-8 JSON; JSON-RPC id/method/envelope/framing are excluded."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def merge_fixture(count: int):
    before_lines = [f"value_{i:04d} = 'original_{i:04d}'\n" for i in range(count)]
    after_lines = list(before_lines)
    # Keep a unique unchanged separator before the human's final-line edit.
    # The stable-anchor policy deliberately refuses adjacent changed regions.
    for index in range(0, count - 2, 2):
        after_lines[index] = f"value_{index:04d} = 'agent_A_{index:04d}'\n"
    current_lines = list(after_lines)
    current_lines[-1] = f"value_{count - 1:04d} = 'human_kept'\n"
    expected_lines = list(before_lines)
    expected_lines[-1] = current_lines[-1]
    return tuple("".join(lines) for lines in (before_lines, after_lines, current_lines, expected_lines))


def measure_merges(sizes, repetitions):
    actual = subprocess.check_output(
        ["git", "rev-parse", BASELINE_TAG + "^{commit}"], cwd=REPOSITORY, text=True, timeout=20,
    ).strip()
    if actual != BASELINE_COMMIT:
        raise RuntimeError("The local v0.1.0 tag does not match the reviewed baseline commit.")
    baseline_source = subprocess.check_output(
        ["git", "show", BASELINE_COMMIT + ":src/undoweave/merge.py"], cwd=REPOSITORY, timeout=20,
    )
    current_path = REPOSITORY / "src" / "undoweave" / "merge.py"
    with tempfile.TemporaryDirectory(prefix="undoweave-merge-baseline-") as temporary:
        baseline_path = Path(temporary) / "released_merge.py"
        baseline_path.write_bytes(baseline_source)
        baseline = load_module("undoweave_released_merge_benchmark", baseline_path)
        current = load_module("undoweave_current_merge_benchmark", current_path)
        results = []
        for count in sizes:
            before, after, live, expected = merge_fixture(count)
            timings = {"released": [], "current": []}
            for repeat in range(repetitions):
                order = (("released", baseline), ("current", current))
                if repeat % 2:
                    order = tuple(reversed(order))
                for label, module in order:
                    start = time.perf_counter()
                    result = module.inverse_merge(before, after, live)
                    elapsed = time.perf_counter() - start
                    assert result == expected, (label, count, "exact independent oracle mismatch")
                    timings[label].append(elapsed)
            released_median = statistics.median(timings["released"])
            current_median = statistics.median(timings["current"])
            results.append({
                "lines": count,
                "agent_changed_lines": len(range(0, count - 2, 2)),
                "later_human_changed_lines": 1,
                "input_utf8_bytes": {"before": len(before.encode()), "after": len(after.encode()), "current": len(live.encode())},
                "samples_seconds": timings,
                "median_seconds": {"released": released_median, "current": current_median},
                "released_over_current_median_ratio": released_median / current_median,
                "exact_oracle_passed": True,
            })
    return {
        "scope": "Pure inverse_merge only; no disk, journal, MCP, model or end-to-end latency measurement.",
        "fixture": "Unique lines; agent A replaces alternating lines, retaining unique unchanged separators; a later human replaces the last line, separated from A's last edit.",
        "acceptance_policy": "Current merge requires stable unique shared separators between independently changed regions. Adjacent or repeated-context edits can now refuse even when the released matcher accepted them. Both implementations receive identical inputs in this accepted-fixture timing comparison.",
        "repetitions": repetitions,
        "order": "Alternating released/current order between repetitions; no separate warmup.",
        "baseline": {"tag": BASELINE_TAG, "commit": BASELINE_COMMIT, "merge_sha256": digest(baseline_source)},
        "current_merge_sha256": digest(current_path.read_bytes()),
        "cases": results,
    }


def mcp_fixture():
    lines = [(f"value_{i:04d} = 'original'  # fixture line {i:04d}").ljust(63) + "\n" for i in range(1000)]
    text = "".join(lines)
    assert len(text.encode()) == 64_000
    return text, "value_0499 = 'original'", "value_0499 = 'updated!'"


def aggregate(calls):
    requests = sum(call["request_body_utf8_bytes"] for call in calls)
    responses = sum(call["response_body_utf8_bytes"] for call in calls)
    return {"calls": len(calls), "request_body_utf8_bytes": requests, "response_body_utf8_bytes": responses, "total_body_utf8_bytes": requests + responses}


async def measure_mcp_flow(directory: Path, name: str, compact: bool, original: str, old: str, new: str):
    project = directory / name
    project.mkdir()
    path = project / "fixture.py"
    path.write_bytes(original.encode())
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "undoweave", "--root", str(project), "--state-dir", str(directory / (name + "-state")), "serve"],
        env=environment,
        cwd=directory,
    )
    edit_calls, undo_calls = [], []
    with (directory / (name + "-stderr.log")).open("w", encoding="utf-8") as stderr:
        async with Client(stdio_client(parameters, errlog=stderr)) as client:
            advertised = {tool.name for tool in (await client.list_tools()).tools}
            assert "replace_text" in advertised, "Install the current checkout's MCP extra before benchmarking."

            async def measured_call(tool, arguments, phase):
                result = await asyncio.wait_for(client.call_tool(tool, arguments), timeout=45)
                assert not result.is_error, (tool, result.content)
                body = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                phase.append({
                    "tool": tool,
                    "request_body_utf8_bytes": json_size({"name": tool, "arguments": arguments}),
                    "response_body_utf8_bytes": json_size(body),
                })
                return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)

            if compact:
                metadata = await measured_call("read_file", {"path": "fixture.py", "metadata_only": True}, edit_calls)
                assert metadata["content"] is None and metadata["content_scope"] == "metadata"
                selected = await measured_call("read_file", {"path": "fixture.py", "start_line": 499, "end_line": 501}, edit_calls)
                assert selected["sha256"] == metadata["sha256"]
                assert selected["content"] == "".join(original.splitlines(keepends=True)[498:501])
                recorded = await measured_call("replace_text", {
                    "path": "fixture.py", "old_text": old, "new_text": new,
                    "actor": "benchmark-agent", "expected_hash": selected["sha256"],
                }, edit_calls)
            else:
                full = await measured_call("read_file", {"path": "fixture.py"}, edit_calls)
                assert full["content"] == original
                recorded = await measured_call("write_file", {
                    "path": "fixture.py", "content": full["content"].replace(old, new),
                    "actor": "benchmark-agent", "expected_hash": full["sha256"],
                }, edit_calls)

            edited = original.replace(old, new)
            assert path.read_bytes() == edited.encode()
            # Equal subsequent human work must survive undo in both flows.
            human_old, human_new = "value_0899 = 'original'", "value_0899 = 'human!!!'"
            path.write_bytes(edited.replace(human_old, human_new).encode())
            preview = await measured_call("preview_undo", {"edit_id": recorded["id"]}, undo_calls)
            assert preview["status"] == "ready"
            applied = await measured_call("apply_undo", {"token": preview["token"]}, undo_calls)
            assert applied["status"] == "applied"
            after_undo = original.replace(human_old, human_new)
            assert path.read_bytes() == after_undo.encode()
            server = {"name": client.server_info.name, "version": client.server_info.version}
    return {
        "server": server,
        "edit_phase": {**aggregate(edit_calls), "per_call": edit_calls},
        "undo_validation": {**aggregate(undo_calls), "per_call": undo_calls},
        "complete_measured_roundtrip": aggregate(edit_calls + undo_calls),
        "edited_file_sha256": digest(edited.encode()),
        "after_undo_file_sha256": digest(after_undo.encode()),
        "exact_edited_bytes_verified": True,
        "undo_verified": True,
        "later_human_edit_preserved": True,
    }


async def measure_mcp():
    original, old, new = mcp_fixture()
    with tempfile.TemporaryDirectory(prefix="undoweave-mcp-efficiency-") as temporary:
        base = Path(temporary).resolve()
        full = await measure_mcp_flow(base, "full-file", False, original, old, new)
        compact = await measure_mcp_flow(base, "exact-edit", True, original, old, new)
    assert full["edited_file_sha256"] == compact["edited_file_sha256"]
    assert full["after_undo_file_sha256"] == compact["after_undo_file_sha256"]
    full_bytes = full["edit_phase"]["total_body_utf8_bytes"]
    compact_bytes = compact["edit_phase"]["total_body_utf8_bytes"]
    return {
        "scope": "Actual stdio MCP tool calls; compact UTF-8 JSON request-params and CallToolResult bodies, including structuredContent and text content, excluding JSON-RPC framing, discovery, initialization and process startup.",
        "not_measured": ["model token counts", "API cost", "end-to-end latency", "filesystem read reduction", "general product ranking"],
        "fixture": {"language": "Python-shaped ASCII text", "lines": 1000, "utf8_bytes": len(original.encode()), "range_read": {"start_line": 499, "end_line": 501}, "unique_literal_replacement": True, "edit_location_known_in_advance": True},
        "full_read_whole_write": full,
        "metadata_range_exact_replace": compact,
        "edit_body_bytes_saved": full_bytes - compact_bytes,
        "edit_body_reduction_percent": 100 * (full_bytes - compact_bytes) / full_bytes,
        "same_file_outcomes_verified": True,
        "limits": "The smaller flow makes three edit calls instead of two and still reads/hashes the entire file locally. These results do not imply lower latency or identical tokenization.",
    }


def verify_installed_code(expected):
    """Ensure isolated child servers import these measured sources, not an old wheel."""
    probe = (
        "import hashlib,json,pathlib; import undoweave.engine,undoweave.merge,undoweave.mcp_server,undoweave.cli; "
        "mods=[undoweave.engine,undoweave.merge,undoweave.mcp_server,undoweave.cli]; "
        "print(json.dumps({m.__name__.rsplit('.',1)[-1]+'.py':hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest() for m in mods}))"
    )
    with tempfile.TemporaryDirectory(prefix="undoweave-installed-probe-") as temporary:
        actual = json.loads(subprocess.check_output([sys.executable, "-I", "-c", probe], cwd=temporary, text=True, timeout=30))
    assert actual == expected, "The interpreter imports different installed source. Install this checkout with its MCP extra."
    return actual


def markdown_report(result):
    rows = []
    for case in result["merge"]["cases"]:
        values = case["median_seconds"]
        rows.append(f"| {case['lines']:,} | {values['released'] * 1000:.3f} | {values['current'] * 1000:.3f} | {case['released_over_current_median_ratio']:.2f} |")
    flows = result["mcp"]
    full, small = flows["full_read_whole_write"], flows["metadata_range_exact_replace"]
    traffic_rows = []
    for label, flow in (("Full read + whole-file write", full), ("Metadata read + 3-line read + exact replacement", small)):
        phase = flow["edit_phase"]
        traffic_rows.append(f"| {label} | {phase['calls']} | {phase['request_body_utf8_bytes']:,} | {phase['response_body_utf8_bytes']:,} | {phase['total_body_utf8_bytes']:,} |")
    env = result["environment"]
    return f"""# Bounded efficiency measurements

These are exposed synthetic development fixtures, not a whole-product ranking. The benchmark uses this project's actual released merge code and real current MCP calls. It makes no model or network requests.

Recorded {result['recorded_at_utc']} on {env['system']} {env['release']} ({env['machine']}), Python {env['python']}, official MCP SDK {env['mcp']}. [Raw samples, per-call counts and exact source hashes](../benchmarks/performance-results.json) identify what was measured. No home-directory or machine hostname is stored.

## Many small inverse hunks

The baseline is unchanged `src/undoweave/merge.py` from [v0.1.0 / e37c81a](https://github.com/Jgolding1998/undoweave/blob/{BASELINE_COMMIT}/src/undoweave/merge.py), extracted with `git show`. The current merge partitions all three snapshots using unchanged lines that appear exactly once in each snapshot and retain their order. A region changed both by the recorded edit and later work is refused.

**The v0.1.0 baseline has a known repeated-line silent-corruption bug.** It is included only as a historical timing reference on the exact-oracle fixture below, which does not trigger that bug. This comparison never recommends using the unsafe old release.

Each fixture has unique lines. Agent A changes alternating lines, leaving unchanged unique separators. A later human changes the final line, with unchanged unique text between that line and A's last edit. Both implementations receive the exact same inputs. Every accepted result must exactly equal an independently constructed expected file with A's changes removed and the human line retained. Timings cover only `inverse_merge`, excluding filesystem, SQLite, MCP and model work. Median of {result['merge']['repetitions']} repetitions; baseline/current execution order alternates between repetitions, without a separate warmup.

| Lines | Released median ms | Stable-anchor median ms | Released/current time ratio |
| --- | ---: | ---: | ---: |
{chr(10).join(rows)}

The acceptance policy is stricter: adjacent changes without an unchanged unique separator and ambiguous repeated-context edits may require manual review even when the released matcher accepted them. This timing fixture is accepted by both versions and does not compare their overall acceptance rates. The stable-anchor change addresses unsafe repeated-text alignment; timing results do not establish correctness by themselves.

This workload deliberately stresses many small inverse regions. It does not establish the same improvement for one edit, repeated text, all merge shapes or complete agent workflows. Source and comparison limits remain necessary. There is no timing-based unit-test threshold.

## Real MCP edit bodies

Both flows edit the same known location in a 1000-line, 64,000-byte ASCII file. The smaller flow requests metadata, then lines 499-501, then replaces one unique literal on line 500. Its full-file hash still binds the edit. The extra metadata/range call overhead is included. The edit location is supplied by the fixture: search and discovery costs are not measured.

| Edit flow | Tool calls | Request-body bytes | Response-body bytes | Total body bytes |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(traffic_rows)}

The measured edit bodies were **{flows['edit_body_reduction_percent']:.2f}% smaller** in the second flow. These are actual tool arguments and returned `CallToolResult` models, reserialized as compact UTF-8 JSON. Response counts include both the text block and `structuredContent`. JSON-RPC framing, discovery, initialization and process startup are excluded. This is not a token-count, API-cost, transport-packet, filesystem-I/O or end-to-end speed measurement.

Both flows produced exactly the same edited bytes. Each then received the same separate human edit and completed actual `preview_undo` / `apply_undo` calls. Both restored the agent-edited line while preserving the human line. Those two validation calls are reported separately in the JSON. Including them, the measured body totals were {full['complete_measured_roundtrip']['total_body_utf8_bytes']:,} bytes across {full['complete_measured_roundtrip']['calls']} calls and {small['complete_measured_roundtrip']['total_body_utf8_bytes']:,} bytes across {small['complete_measured_roundtrip']['calls']} calls respectively.

The smaller responses do not imply fewer local file reads: range/metadata reads still read and hash the entire selected file, and safe replacement rechecks it. Journal snapshots still retain full before/after text. Additional tool calls can add latency. IDs and timestamps can change byte counts slightly between runs.

## Reproduce

From this Git checkout with the local `v0.1.0` tag available, install the current checkout into an isolated environment with `python -m pip install -e \".[mcp]\"`, then run:

```sh
python -I benchmarks/efficiency.py
```

The script verifies the baseline tag, checks that isolated MCP child processes import the same source hashes, creates only disposable projects, and refuses to save results if measured runtime source changes during the run. It writes the JSON results and this Markdown report. It never runs undo against your working project. Repeat on a stable source checkout and compare the recorded hashes before citing numbers.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "benchmarks" / "performance-results.json")
    parser.add_argument("--markdown", type=Path, default=REPOSITORY / "docs" / "PERFORMANCE.md")
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--sizes", type=int, nargs="+", choices=(500, 1000, 2000, 4000), default=(500, 1000, 2000, 4000))
    args = parser.parse_args()
    source_names = ("engine.py", "merge.py", "mcp_server.py", "cli.py")
    hashes = {name: digest((REPOSITORY / "src" / "undoweave" / name).read_bytes()) for name in source_names}
    installed_hashes = verify_installed_code(hashes)
    result = {
        "format": "undoweave-efficiency-v1",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": platform.python_version(), "mcp": importlib.metadata.version("mcp")},
        "source_sha256": hashes,
        "verified_installed_server_sha256": installed_hashes,
        "merge": measure_merges(args.sizes, args.repetitions),
        "mcp": asyncio.run(asyncio.wait_for(measure_mcp(), timeout=180)),
    }
    assert hashes == {name: digest((REPOSITORY / "src" / "undoweave" / name).read_bytes()) for name in source_names}, "Source changed during the run; rerun on a stable checkout."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps({"output": args.output.name, "merge_cases": len(result["merge"]["cases"]), "mcp_edit_body_reduction_percent": result["mcp"]["edit_body_reduction_percent"], "exact_oracles_passed": True}))


if __name__ == "__main__":
    main()
