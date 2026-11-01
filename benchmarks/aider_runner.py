"""Execute the exact upstream raw_cmd_undo method with real GitPython repos.

This isolates Aider's undo method, not its CLI or agent runtime. The minimal
adapter supplies its I/O, session-owned commit set, and repository accessors.
No model imports, API calls, plugins, or upstream module-level code are run.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from git import Repo
from git.exc import GitError

SOURCE_BLOB = "3881403c5c10212119e77a3ab0d3e092830054e6"


def load_undo(source: Path):
    data = source.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if blob != SOURCE_BLOB:
        raise ValueError("Aider source does not match the reviewed pinned commands.py Git blob")
    parsed = ast.parse(data.decode("utf-8"), filename=str(source))
    command_class = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "Commands")
    method = next(n for n in command_class.body if isinstance(n, ast.FunctionDef) and n.name == "raw_cmd_undo")
    if method.decorator_list:
        raise ValueError("Unexpected decorated method")
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {"ANY_GIT_ERROR": (GitError,)}
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["raw_cmd_undo"]


class Output:
    def __init__(self):
        self.errors = []
        self.messages = []

    def tool_error(self, value, *args, **kwargs):
        self.errors.append(str(value))

    def tool_output(self, value, *args, **kwargs):
        self.messages.append(str(value))


class RepositoryAdapter:
    def __init__(self, repo):
        self.repo = repo

    def get_head_commit(self):
        return self.repo.head.commit

    def get_head_commit_sha(self, short=False):
        sha = self.repo.head.commit.hexsha
        return sha[:7] if short else sha

    def get_head_commit_message(self, default):
        return self.repo.head.commit.message or default


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def change(root: Path, edit: dict):
    target = root / edit["path"]
    if edit["content"] is None:
        target.unlink()
    else:
        target.write_bytes(edit["content"].encode("utf-8"))


def capture(root: Path, fixture: dict):
    paths = set(fixture["initial"]) | {fixture["edit"]["path"]} | {e["path"] for e in fixture["later"]}
    return {p: (root / p).read_bytes().decode("utf-8") if (root / p).exists() else None for p in sorted(paths)}


def equal(actual, expected):
    return all(actual.get(p) == expected.get(p) for p in actual.keys() | expected.keys())


def main():
    source, fixture_path = map(Path, sys.argv[1:])
    undo = load_undo(source)
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    results = []
    for fixture in fixtures:
        with tempfile.TemporaryDirectory(prefix="undoweave-aider-comparison-") as temp:
            root = Path(temp) / "project"
            root.mkdir()
            for path, content in fixture["initial"].items():
                (root / path).write_bytes(content.encode("utf-8"))
            io = Output()
            if fixture.get("no_git"):
                command = SimpleNamespace(coder=SimpleNamespace(repo=None), io=io)
                undo(command, "")
                results.append({"id": fixture["id"], "status": "unsupported_no_git", "messages": io.errors, "selective_goal_met": False})
                continue
            hook_dir = Path(temp) / "empty-hooks"
            hook_dir.mkdir()
            git(root, "init", "-q")
            git(root, "config", "core.hooksPath", str(hook_dir))
            git(root, "config", "core.autocrlf", "false")
            git(root, "config", "commit.gpgsign", "false")
            git(root, "config", "user.name", "Disposable comparison")
            git(root, "config", "user.email", "comparison@example.invalid")
            git(root, "add", "--all")
            git(root, "commit", "-q", "-m", "synthetic fixture")
            change(root, fixture["edit"])
            git(root, "add", "--all")
            git(root, "commit", "-q", "-m", "agent-a synthetic change")
            agent_sha = git(root, "rev-parse", "HEAD")
            for later in fixture["later"]:
                change(root, later)
            before = capture(root, fixture)
            repo = Repo(root)
            try:
                command = SimpleNamespace(
                    io=io,
                    coder=SimpleNamespace(repo=RepositoryAdapter(repo), aider_commit_hashes={agent_sha[:7]}, main_model=SimpleNamespace(send_undo_reply=False)),
                )
                undo(command, "")
                actual = capture(root, fixture)
                status = "refused" if io.errors else "undone_commit"
                meets = equal(actual, fixture["expected"]) and (
                    (fixture["expected_status"] == "conflict" and status == "refused") or
                    (fixture["expected_status"] == "ready" and status == "undone_commit")
                )
                results.append({"id": fixture["id"], "status": status, "actual": actual, "project_unchanged": equal(actual, before), "selective_goal_met": meets, "messages": io.errors})
            finally:
                repo.close()
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
