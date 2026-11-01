"""JSON transport must acknowledge Unicode edits on legacy terminal encodings.

All CLI calls use disposable projects. Default-pipe tests deliberately do not
force UTF-8, unlike the general CLI helper. An ASCII pipe and strict ASCII
TextIOWrapper also exercise the transport contract on UTF-8-default platforms.
"""

from __future__ import annotations

from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from undoweave import cli


SOURCE = Path(__file__).resolve().parents[1] / "src"
UNICODE_PATH = "source-caf\u00e9-\U0001f680.txt"
UNICODE_LABEL = "Update caf\u00e9 \U0001f9f5"
UNICODE_CONTENT = "caf\u00e9 \U0001f680\r\nparagraph\u2028separator\nlast line"


class CliEncoding(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="undoweave-cli-encoding-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "state"
        self.incoming = self.base / "incoming.txt"
        self.content_bytes = UNICODE_CONTENT.encode("utf-8")
        self.incoming.write_bytes(self.content_bytes)

    def run_cli(self, *arguments, pipe_encoding=None):
        environment = dict(os.environ)
        # Exercise the real default Windows pipe encoding rather than inheriting
        # the UTF-8 override installed by another test harness or host process.
        environment.pop("PYTHONIOENCODING", None)
        environment.pop("PYTHONUTF8", None)
        environment["PYTHONPATH"] = str(SOURCE)
        if pipe_encoding is not None:
            environment["PYTHONIOENCODING"] = pipe_encoding
        return subprocess.run(
            [sys.executable, "-m", "undoweave", "--root", str(self.root),
             "--state-dir", str(self.state), *arguments],
            env=environment, capture_output=True, timeout=30,
        )

    def result(self, process):
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, b"")
        # JSON escapes preserve exact Unicode values while every emitted byte
        # remains valid even on ASCII/cp1252 pipes.
        return json.loads(process.stdout.decode("ascii"))

    def create(self, pipe_encoding=None):
        return self.result(self.run_cli(
            "edit", UNICODE_PATH, "--content-file", str(self.incoming),
            "--actor", "agent", "--expected-hash", "missing",
            "--label", UNICODE_LABEL, pipe_encoding=pipe_encoding,
        ))

    def assert_acknowledged(self, acknowledgement):
        self.assertEqual(acknowledgement["path"], UNICODE_PATH)
        self.assertEqual(acknowledgement["label"], UNICODE_LABEL)
        self.assertEqual(acknowledgement["status"], "applied")
        self.assertEqual(acknowledgement["before_hash"], "missing")
        self.assertEqual(acknowledgement["after_hash"], sha256(self.content_bytes).hexdigest())
        self.assertEqual((self.root / UNICODE_PATH).read_bytes(), self.content_bytes)
        self.assertRegex(acknowledgement["id"], r"^[a-f0-9]{32}$")

    def test_default_pipe_read_round_trips_unicode_content_path_and_source_hash(self):
        (self.root / UNICODE_PATH).write_bytes(self.content_bytes)
        snapshot = self.result(self.run_cli("read", UNICODE_PATH))
        self.assertEqual(snapshot["path"], UNICODE_PATH)
        self.assertEqual(snapshot["content"], UNICODE_CONTENT)
        self.assertEqual(snapshot["sha256"], sha256(self.content_bytes).hexdigest())
        self.assertEqual(snapshot["byte_length"], len(self.content_bytes))

    def test_default_pipe_edit_returns_successful_unicode_acknowledgement_and_history(self):
        acknowledgement = self.create()
        self.assert_acknowledged(acknowledgement)
        history = self.result(self.run_cli("history"))["changes"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0], acknowledgement)

    def test_ascii_pipe_edit_read_preview_and_undo_round_trip(self):
        acknowledgement = self.create(pipe_encoding="ascii")
        self.assert_acknowledged(acknowledgement)
        snapshot = self.result(self.run_cli("read", UNICODE_PATH, pipe_encoding="ascii"))
        self.assertEqual(snapshot["content"], UNICODE_CONTENT)
        self.assertEqual(snapshot["sha256"], acknowledgement["after_hash"])
        preview = self.result(self.run_cli("preview", acknowledgement["id"], pipe_encoding="ascii"))
        self.assertEqual((preview["status"], preview["path"]), ("ready", UNICODE_PATH))
        undo = self.result(self.run_cli("undo", preview["token"], pipe_encoding="ascii"))
        self.assertEqual(undo["status"], "applied")
        self.assertEqual(undo["path"], UNICODE_PATH)
        self.assertEqual(undo["undoes"], acknowledgement["id"])
        self.assertEqual(undo["after_hash"], "missing")
        self.assertFalse((self.root / UNICODE_PATH).exists())

    def test_emit_round_trips_through_strict_ascii_text_wrapper(self):
        payload = {"content": UNICODE_CONTENT, "path": UNICODE_PATH,
                   "label": UNICODE_LABEL, "sha256": sha256(self.content_bytes).hexdigest()}
        raw = io.BytesIO()
        with io.TextIOWrapper(raw, encoding="ascii", errors="strict", write_through=True) as stream:
            cli._emit(payload, stream)
            encoded = raw.getvalue()
        self.assertEqual(json.loads(encoded.decode("ascii")), payload)


if __name__ == "__main__":
    unittest.main()
