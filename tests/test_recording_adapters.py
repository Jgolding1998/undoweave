"""Opt-in recording configuration and foreground lifetime; synthetic inputs only."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from undoweave import cli


class RecordingConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root.parent / (self.root.name + "-unused-state")

    def args(self, *extra):
        return cli.build_parser().parse_args([
            "--root", str(self.root), "--state-dir", str(self.state), "config", *extra])

    def test_printed_config_pins_paths_without_creating_a_recorder_or_state(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli._config(self.args("--watch", "src", "--watch", "tests", "--interval", ".5"))
        config = json.loads(output.getvalue())["mcpServers"]["undoweave"]
        self.assertEqual(config["args"][-7:], ["serve", "--watch", "src", "--watch", "tests", "--interval", "0.5"])
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / ".undoweave-id").exists())

    def test_default_configuration_does_not_start_observation(self):
        argv = cli._server_argv(self.args())
        self.assertEqual(argv[-1], "serve")
        self.assertNotIn("--watch", argv)

    def test_explicit_client_configuration_is_refused_before_state_creation(self):
        from undoweave.engine import UndoWeaveError
        for scope in (".mcp.json", "mcp.json", ".claude/settings.json", ".codex/config.toml",
                      ".vscode/mcp.json", ".cursor/mcp.json", ".windsurf/mcp.json"):
            with self.subTest(scope=scope), self.assertRaises(UndoWeaveError):
                cli._server_argv(self.args("--watch", scope))
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / ".undoweave-id").exists())

    def test_invalid_scopes_and_intervals_fail_before_any_state_write(self):
        cases = [("--watch", value) for value in (".", "../outside", "/absolute", "src\\file.py", "src//file.py", "a\nb.py")]
        cases += [("--interval", value) for value in ("nan", "inf", "0", ".249", "60.1")]
        cases += [tuple(value for _ in range(17) for value in ("--watch", "src"))]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(cli.UsageError):
                cli._server_argv(self.args(*extra))
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / ".undoweave-id").exists())

    def test_foreground_interrupt_stops_owned_recorder_and_emits_only_json_events(self):
        from undoweave import recorder
        lifecycle = []

        class FakeRecorder:
            def __init__(self, engine, scopes, interval):
                lifecycle.append(("create", scopes, interval))
            def start(self):
                lifecycle.append("start")
            def status(self):
                return {"state": "active"}
            def stop(self):
                lifecycle.append("stop")

        args = cli.build_parser().parse_args(["--root", str(self.root), "watch", "src"])
        with patch.object(recorder, "Recorder", FakeRecorder), patch.object(cli, "_make_engine", return_value=object()), \
                patch.object(cli.threading, "Event") as event, contextlib.redirect_stdout(io.StringIO()) as output:
            event.return_value.wait.side_effect = KeyboardInterrupt
            self.assertEqual(cli._watch(args), 0)
        events = [json.loads(line)["event"] for line in output.getvalue().splitlines()]
        self.assertEqual(events, ["recording_started", "recording_stopped"])
        self.assertEqual(lifecycle[-1], "stop")

    def test_foreground_start_failure_still_closes_owned_recorder(self):
        from undoweave import recorder
        lifecycle = []
        class FailedRecorder:
            def __init__(self, *args, **kwargs):
                pass
            def start(self):
                raise RuntimeError("synthetic start failure")
            def stop(self):
                lifecycle.append("stop")
        args = cli.build_parser().parse_args(["--root", str(self.root), "watch", "src"])
        with patch.object(recorder, "Recorder", FailedRecorder), patch.object(cli, "_make_engine", return_value=object()), \
                self.assertRaises(RuntimeError):
            cli._watch(args)
        self.assertEqual(lifecycle, ["stop"])


if __name__ == "__main__":
    unittest.main()
