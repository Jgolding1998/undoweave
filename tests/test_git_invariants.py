"""Real-Git invariants inspired by upstream restore/undo regressions.

Git is used only to construct and inspect disposable test projects. UndoWeave
must change only its selected text file, never HEAD, refs, the index, or other
working files. These tests do not execute the Cline or Aider implementations.
"""

from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from undoweave.engine import Engine

GIT = shutil.which("git")


@unittest.skipUnless(GIT, "Git is required for disposable-repository invariants")
class GitInvariantTests(unittest.TestCase):
    ORIGINAL = b"agent=original\nseparator=keep\nhuman=original\n"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="undoweave-git-invariants-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state"

        # Initialization may create the protected, nonsecret workspace identity
        # marker. It happens before every Git/project baseline in this suite.
        self.engine = Engine(self.root, self.state)
        self.empty_hooks = self.base / "empty-hooks"
        self.empty_hooks.mkdir()
        empty_config = self.base / "empty-global-config"
        empty_config.write_bytes(b"")
        # Caller Git variables must not redirect fixture commands to a real
        # repository, index or object store outside this TemporaryDirectory.
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
        }
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        })

        self.git("init", "-q", "-b", "initial")
        for key, value in (
            ("core.hooksPath", str(self.empty_hooks)),
            ("core.autocrlf", "false"),
            ("core.safecrlf", "false"),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
            ("user.name", "Disposable invariant test"),
            ("user.email", "invariant@example.invalid"),
        ):
            self.git("config", key, value)
        # Keep workspace identity local. This exclusion is fixture setup, not
        # something UndoWeave is permitted to edit during its operations.
        (self.root / ".git" / "info" / "exclude").write_bytes(b"/.undoweave-id\n/private-cache/\n")
        self.put("app.txt", self.ORIGINAL)
        self.put("other.txt", b"other=original\nsecond=original\n")
        self.put("staged.txt", b"staged=original\n")
        self.put(".gitignore", b"# original ignore rules\n")
        self.git("add", "app.txt", "other.txt", "staged.txt", ".gitignore")
        self.git("commit", "-q", "--no-verify", "-m", "initial fixture")

    def git(self, *arguments, check=True):
        return subprocess.run(
            [GIT, *arguments], cwd=self.root, env=self.environment,
            capture_output=True, check=check, timeout=20,
        )

    def put(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def record_a(self):
        current = self.engine.read("app.txt")
        changed = current["content"].replace("agent=original", "agent=A")
        return self.engine.edit("app.txt", changed, "agent-a", current["sha256"])

    def add_unrelated_work(self):
        self.put("staged.txt", b"staged=human\n")
        self.git("add", "staged.txt")
        self.put("other.txt", b"other=unstaged-human\nsecond=original\n")
        self.put("notes.txt", b"human untracked notes\n")
        self.put("scratch/data/payload.bin", b"\0unrelated binary data\xff\n")
        self.put("private-cache/ignored.bin", b"\0ignored human cache\xff\n")
        self.assertEqual(self.git("check-ignore", "-q", "private-cache/ignored.bin", check=False).returncode, 0)

    def project_files(self):
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.relative_to(self.root).parts[0] != ".git"
        }

    def git_state(self):
        # Optional locks are disabled, so inspection cannot refresh/write the
        # index and accidentally hide a tool-side index mutation.
        result = {
            "head": self.git("rev-parse", "HEAD").stdout,
            "branch": self.git("symbolic-ref", "HEAD").stdout,
            "refs": self.git("for-each-ref", "--sort=refname", "--format=%(refname) %(objectname)").stdout,
            "staged_diff": self.git("diff", "--cached", "--binary", "--no-ext-diff").stdout,
            "index_entries": self.git("ls-files", "--stage", "-z").stdout,
            "stash": self.git("stash", "list", "--format=%H %gs").stdout,
            "config": (self.root / ".git" / "config").read_bytes(),
            "exclude": (self.root / ".git" / "info" / "exclude").read_bytes(),
        }
        result["index_bytes"] = (self.root / ".git" / "index").read_bytes()
        return result

    def apply_and_assert_only(self, change_id, path, expected, *, engine=None):
        engine = engine or self.engine
        files = self.project_files()
        metadata = self.git_state()
        preview = engine.preview_undo(change_id)
        self.assertEqual(preview["status"], "ready", preview)
        self.assertEqual(self.project_files(), files, "preview changed project bytes")
        self.assertEqual(self.git_state(), metadata, "preview changed Git metadata")
        applied = engine.apply_undo(preview["token"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["after_hash"], sha256(expected).hexdigest())
        files[path] = expected
        self.assertEqual(self.project_files(), files, "undo changed an unrelated file")
        self.assertEqual(self.git_state(), metadata, "undo changed refs or staged/index state")
        return applied

    def test_undo_after_human_commit_preserves_head_refs_and_staged_index(self):
        change = self.record_a()
        self.put("app.txt", b"agent=A\nseparator=keep\nhuman=committed\n")
        self.git("add", "app.txt")
        self.git("commit", "-q", "--no-verify", "-m", "human commit after agent edit")
        self.add_unrelated_work()
        self.apply_and_assert_only(
            change["id"], "app.txt", b"agent=original\nseparator=keep\nhuman=committed\n",
        )

    def test_undo_after_human_amend_preserves_amended_commit(self):
        change = self.record_a()
        self.put("app.txt", b"agent=A\nseparator=keep\nhuman=amended\n")
        self.git("add", "app.txt")
        self.git("commit", "-q", "--no-verify", "--amend", "-m", "human amended fixture")
        self.add_unrelated_work()
        self.apply_and_assert_only(
            change["id"], "app.txt", b"agent=original\nseparator=keep\nhuman=amended\n",
        )

    def test_undo_after_branch_switch_preserves_new_branch_and_commit(self):
        change = self.record_a()
        self.git("switch", "-q", "-c", "human-branch")
        self.put("other.txt", b"human committed on another branch\n")
        self.git("add", "other.txt")
        self.git("commit", "-q", "--no-verify", "-m", "human branch commit")
        self.put("app.txt", b"agent=A\nseparator=keep\nhuman=branch-work\n")
        self.add_unrelated_work()
        self.apply_and_assert_only(
            change["id"], "app.txt", b"agent=original\nseparator=keep\nhuman=branch-work\n",
        )

    def test_conflict_preserves_every_file_refs_index_and_existing_stash(self):
        change = self.record_a()
        self.put("other.txt", b"human stash content\n")
        self.git("stash", "push", "-q", "-m", "preserve human stash", "--", "other.txt")
        self.put("app.txt", b"agent=human-overwrite\nseparator=keep\nhuman=original\n")
        self.add_unrelated_work()
        before_files, before_git = self.project_files(), self.git_state()
        self.assertTrue(before_git["stash"], "fixture needs an existing user stash")
        preview = self.engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "conflict")
        self.assertNotIn("token", preview)
        self.assertEqual(self.project_files(), before_files)
        self.assertEqual(self.git_state(), before_git)

    def test_gitignore_undo_never_cleans_previously_ignored_or_untracked_files(self):
        original = self.engine.read(".gitignore")
        change = self.engine.edit(
            ".gitignore", original["content"] + "scratch/\n", "agent-a", original["sha256"],
        )
        self.add_unrelated_work()
        self.assertEqual(self.git("check-ignore", "-q", "scratch/data/payload.bin", check=False).returncode, 0)
        self.apply_and_assert_only(change["id"], ".gitignore", b"# original ignore rules\n")
        self.assertEqual(self.git("check-ignore", "-q", "scratch/data/payload.bin", check=False).returncode, 1)
        self.assertEqual((self.root / "scratch/data/payload.bin").read_bytes(), b"\0unrelated binary data\xff\n")

    def test_undo_preserves_both_staged_and_unstaged_hunks_of_unrelated_file(self):
        change = self.record_a()
        staged = b"other=staged-human\nsecond=original\n"
        self.put("other.txt", staged)
        self.git("add", "other.txt")
        working = b"other=staged-human\nsecond=unstaged-human\n"
        self.put("other.txt", working)
        self.assertEqual(self.git("show", ":other.txt").stdout, staged)
        self.apply_and_assert_only(change["id"], "app.txt", self.ORIGINAL)
        self.assertEqual(self.git("show", ":other.txt").stdout, staged)
        self.assertEqual((self.root / "other.txt").read_bytes(), working)

    def test_restart_keeps_exact_edit_id_after_other_actor_and_git_activity(self):
        change = self.record_a()
        other = self.engine.read("other.txt")
        self.engine.edit("other.txt", "agent B work survives\n", "agent-b", other["sha256"])
        self.git("add", "other.txt")
        self.git("commit", "-q", "--no-verify", "-m", "human accepts agent B work")
        self.put("app.txt", b"agent=A\nseparator=keep\nhuman=after-restart\n")
        before_files, before_git = self.project_files(), self.git_state()
        resumed = Engine(self.root, self.state)
        self.assertTrue(any(row["id"] == change["id"] for row in resumed.history()))
        self.assertEqual(self.project_files(), before_files, "restart changed project files")
        self.assertEqual(self.git_state(), before_git, "restart changed Git state")
        self.apply_and_assert_only(
            change["id"], "app.txt", b"agent=original\nseparator=keep\nhuman=after-restart\n", engine=resumed,
        )


if __name__ == "__main__":
    unittest.main()
