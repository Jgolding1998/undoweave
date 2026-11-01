"""Run synthetic shared cases against UndoWeave and unchanged pi-rewind core.

Requires Node >=24 and Git only for the external comparator. Does not clone,
install, or launch models. All projects are disposable temporary directories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

PIN = "91611ad87992fb7b635a41ba68f67916ff6e6ae3"
HERE = Path(__file__).resolve().parent


def check_checkout(checkout: Path) -> None:
    sha = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    if sha != PIN:
        raise ValueError(f"pi-rewind checkout must be pinned to {PIN}; got {sha}")
    dirty = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"], text=True, capture_output=True, check=True).stdout
    if dirty:
        raise ValueError("pi-rewind tracked files were modified; refusing a mislabeled comparison")


def change(root: Path, edit: dict) -> None:
    target = root / edit["path"]
    if edit["content"] is None:
        target.unlink()
    else:
        target.write_bytes(edit["content"].encode("utf-8"))


def capture(root: Path, fixture: dict) -> dict:
    names = set(fixture["initial"]) | {fixture["edit"]["path"]} | {e["path"] for e in fixture["later"]}
    return {name: (root / name).read_bytes().decode("utf-8") if (root / name).exists() else None for name in sorted(names)}


def equal_files(actual: dict, expected: dict) -> bool:
    return all(actual.get(name) == expected.get(name) for name in actual.keys() | expected.keys())


def run_undoweave(fixtures: list) -> list:
    from undoweave.engine import Engine

    results = []
    for fixture in fixtures:
        with tempfile.TemporaryDirectory(prefix="undoweave-own-comparison-") as temp:
            root = Path(temp) / "project"
            state = Path(temp) / "private-state"
            root.mkdir()
            for path, content in fixture["initial"].items():
                (root / path).write_bytes(content.encode("utf-8"))
            if not fixture.get("no_git"):
                hooks = Path(temp) / "empty-hooks"
                hooks.mkdir()
                for arguments in (
                    ["init", "-q"], ["config", "core.hooksPath", str(hooks)],
                    ["config", "core.autocrlf", "false"], ["config", "commit.gpgsign", "false"],
                    ["config", "user.name", "Disposable comparison"],
                    ["config", "user.email", "comparison@example.invalid"],
                    ["add", "--all"], ["commit", "-q", "-m", "synthetic fixture"],
                ):
                    subprocess.run(["git", *arguments], cwd=root, text=True, capture_output=True, check=True)
                index_before = (root / ".git" / "index").read_bytes()
            engine = Engine(root, state)
            edit = fixture["edit"]
            recorded = engine.edit(edit["path"], edit["content"], "agent-a", engine.read(edit["path"])["sha256"], fixture["id"])
            for later in fixture["later"]:
                if later["actor"] == "human":
                    change(root, later)
                else:
                    engine.edit(later["path"], later["content"], later["actor"], engine.read(later["path"])["sha256"])
            before = capture(root, fixture)
            preview = engine.preview_undo(recorded["id"])
            if preview["status"] == "ready":
                engine.apply_undo(preview["token"])
            actual = capture(root, fixture)
            meets = preview["status"] == fixture["expected_status"] and equal_files(actual, fixture["expected"])
            row = {"id": fixture["id"], "status": preview["status"], "actual": actual, "project_unchanged": equal_files(actual, before), "selective_goal_met": meets}
            if not fixture.get("no_git"):
                row["git_index_unchanged"] = (root / ".git" / "index").read_bytes() == index_before
            results.append(row)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-rewind", type=Path, required=True, help="Reviewed, clean upstream checkout at the pinned SHA")
    parser.add_argument("--output", type=Path, help="Optional JSON result path")
    parser.add_argument("--aider-source", type=Path, help="Optional pinned Aider commands.py for an exact-method comparison")
    parser.add_argument("--aider-python", type=Path, help="Python with GitPython==3.1.46; required with --aider-source")
    args = parser.parse_args()
    if bool(args.aider_source) != bool(args.aider_python):
        parser.error("--aider-source and --aider-python must be supplied together")
    check_checkout(args.pi_rewind.resolve())
    fixtures = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
    baseline = subprocess.run(["node", str(HERE / "pi_rewind_runner.mjs"), str(args.pi_rewind.resolve() / "src" / "core.ts"), str(HERE / "fixtures.json")], text=True, encoding="utf-8", capture_output=True, check=True, timeout=180)
    data = {
        "schema_version": 1,
        "scope": "Eight exposed synthetic fixtures; measured selective-undo objective, not whole-tool quality or model accuracy.",
        "upstream": {"repository": "https://github.com/arpagon/pi-rewind", "commit": PIN, "adapter": "unchanged src/core.ts imported by Node; recovery checkpoint before restore"},
        "fixtures": [{"id": f["id"], "description": f["description"], "expected_status": f["expected_status"]} for f in fixtures],
        "pi_rewind": json.loads(baseline.stdout),
        "undoweave": run_undoweave(fixtures),
    }
    import undoweave.engine
    import undoweave.merge
    data["undoweave_source_sha256"] = {
        "engine.py": hashlib.sha256(Path(undoweave.engine.__file__).read_bytes()).hexdigest(),
        "merge.py": hashlib.sha256(Path(undoweave.merge.__file__).read_bytes()).hexdigest(),
    }
    if args.aider_source:
        aider = subprocess.run([str(args.aider_python), str(HERE / "aider_runner.py"), str(args.aider_source), str(HERE / "fixtures.json")], text=True, encoding="utf-8", capture_output=True, check=True, timeout=180)
        data["aider"] = json.loads(aider.stdout)
        data["aider_upstream"] = {
            "repository": "https://github.com/Aider-AI/aider",
            "commit": "5dc9490bb35f9729ef2c95d00a19ccd30c26339c",
            "commands_py_git_blob": "3881403c5c10212119e77a3ab0d3e092830054e6",
            "adapter": "Exact raw_cmd_undo AST method; real GitPython repos, minimal I/O/session adapter; full Aider CLI not tested",
            "GitPython": "3.1.46",
        }
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if all(row["selective_goal_met"] for row in data["undoweave"]) else 1


if __name__ == "__main__":
    sys.exit(main())
