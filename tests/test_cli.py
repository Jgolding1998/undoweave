"""CLI tests. Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from undoweave import cli  # noqa: E402

try:
    cli.load_core()
    CORE_AVAILABLE = True
except ImportError:
    CORE_AVAILABLE = False


def run_cli(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "undoweave", *args],
        input=stdin,
        capture_output=True,
        env=env,
        timeout=120,
    )


def as_json(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


class CliWithoutCoreTests(unittest.TestCase):
    """Argument handling that must work before the core engine is touched."""

    def test_relative_root_is_usage_error_without_traceback(self):
        proc = run_cli("--root", "relative/dir", "status")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        err = as_json(proc.stderr)
        self.assertEqual(err["error"]["code"], "usage")
        self.assertIn("absolute", err["error"]["message"])
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_edit_requires_content_file_or_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("--root", tmp, "edit", "a.txt", "--actor", "x", "--expected-hash", "missing")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_edit_rejects_both_content_file_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "--root", tmp, "edit", "a.txt", "--delete", "--content-file", "x",
                "--actor", "x", "--expected-hash", "missing",
            )
        self.assertEqual(proc.returncode, 2)

    def test_no_shell_command_option_exists(self):
        parser = cli.build_parser()
        help_text = parser.format_help()
        for word in ("--exec", "--command", "--shell", "--force", "--run"):
            self.assertNotIn(word, help_text)

    def test_demo_rejects_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("--root", tmp, "demo")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("temporary directory", as_json(proc.stderr)["error"]["message"])

    def test_serve_without_mcp_explains_install(self):
        blocked = {name: None for name in list(sys.modules) if name == "mcp" or name.startswith("mcp.")}
        blocked.update({"mcp": None, "mcp.server": None, "mcp.server.mcpserver": None})
        stderr = io.StringIO()
        with mock.patch.dict(sys.modules, blocked), contextlib.redirect_stderr(stderr):
            code = cli.main(["--root", str(Path(tempfile.gettempdir()).resolve()), "serve"])
        self.assertEqual(code, 3)
        self.assertIn('pip install ".[mcp]"', stderr.getvalue())
        self.assertIn("mcp==2.3.0", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class ConfigCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / "my project"
        self.root.mkdir()
        self.state = Path(self._tmp.name).resolve() / "state dir"

    def tearDown(self):
        self._tmp.cleanup()

    def test_generic_json_uses_this_interpreter_and_pins_root(self):
        proc = run_cli("--root", str(self.root), "--state-dir", str(self.state), "config")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entry = as_json(proc.stdout)["mcpServers"]["undoweave"]
        self.assertEqual(Path(entry["command"]), Path(sys.executable))
        self.assertEqual(
            entry["args"],
            ["-I", "-m", "undoweave", "--root", str(self.root), "--state-dir", str(self.state), "serve"],
        )
        self.assertFalse(self.state.exists(), "config must not create state")

    def test_client_instructions_do_not_change_config(self):
        if os.name == "nt":
            claude_needle = "claude 'mcp' 'add' '--transport' 'stdio' '--scope' 'project' 'undoweave' '--' "
            codex_needle = "codex 'mcp' 'add' 'undoweave' '--' "
        else:
            claude_needle = "claude mcp add --transport stdio --scope project undoweave -- "
            codex_needle = "codex mcp add undoweave -- "
        for client, needle in (("claude", claude_needle), ("codex", codex_needle),
                               ("codex", "[mcp_servers.undoweave]")):
            with self.subTest(client=client):
                proc = run_cli("--root", str(self.root), "config", "--client", client)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                text = proc.stdout.decode("utf-8")
                self.assertIn(needle, text)
                self.assertIn("did not change any configuration", text)
                self.assertIn("serve", text)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_codex_toml_strings_round_trip(self):
        import tomllib

        proc = run_cli("--root", str(self.root), "--state-dir", str(self.state), "config", "--client", "codex")
        text = proc.stdout.decode("utf-8").replace("\r\n", "\n")
        table = text[text.index("[mcp_servers.undoweave]"):text.index("\n\n", text.index("[mcp_servers"))]
        parsed = tomllib.loads(table)["mcp_servers"]["undoweave"]
        self.assertEqual(Path(parsed["command"]), Path(sys.executable))
        self.assertIn(str(self.root), parsed["args"])

    def test_config_requires_existing_absolute_root(self):
        self.assertEqual(run_cli("config").returncode, 2)
        self.assertEqual(run_cli("--root", "rel", "config").returncode, 2)
        missing = run_cli("--root", str(self.root / "nope"), "config")
        self.assertEqual(missing.returncode, 2)
        self.assertNotIn(b"Traceback", missing.stderr)

    def test_unknown_client_rejected(self):
        proc = run_cli("--root", str(self.root), "config", "--client", "bash -c id")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")


def _find_powershells() -> list[str]:
    """Every PowerShell available: pwsh (7+) and, on Windows, Windows PowerShell 5.1."""
    import shutil

    found = [shutil.which("pwsh")] + ([shutil.which("powershell")] if os.name == "nt" else [])
    return [path for path in found if path]


POWERSHELLS = _find_powershells()
POWERSHELL = POWERSHELLS[0] if POWERSHELLS else None

# Neutral markers only. If quoting were broken, PowerShell would try to expand a
# variable or look up a command that does not exist; nothing here does anything.
HOSTILE_ARGS = [
    r"C:\Users\a b\$(uwmark-subexpr)\py thon.exe",
    "$uwmark_var",
    "$env:UW_NEUTRAL_MARKER",
    "${uwmark}",
    "`uwmark-backtick`n",
    "it's 'quoted'",
    "\u2018curly\u2019 \u201alow\u201b",
    "a&b",
    "a;b",
    "a|b",
    "@uwmark",
    "%UW_NEUTRAL_MARKER%",
    "--%x",
    "x--%",
    "--",
    "a  b",
    "(uwmark)",
    "{uwmark}",
    "#uwmark",
    "<uwmark>",
    r"C:\dir with space\sub",
    "C:\\",
    "\\\\server\\share\\",
]

# Parses (never runs) a command line read from an environment variable and reports
# each element of the single resulting command.
_PS_PARSE = r"""
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($env:UW_LINE, [ref]$tokens, [ref]$errors)
$cmds = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true))
$elements = @()
foreach ($e in $cmds[0].CommandElements) {
  $kind = if ($e -is [System.Management.Automation.Language.StringConstantExpressionAst]) { [string]$e.StringConstantType } else { $e.GetType().Name }
  $value = if ($e -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $e.Value } else { $e.Extent.Text }
  $elements += ,@{ kind = $kind; value = $value }
}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
@{ errors = @($errors).Count; commands = $cmds.Count; statements = $ast.EndBlock.Statements.Count; elements = $elements } | ConvertTo-Json -Depth 5 -Compress
"""


def powershell_parse(line: str, shell: str | None = None) -> dict:
    env = dict(os.environ, UW_LINE=line)
    proc = subprocess.run(
        [shell or POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _PS_PARSE],
        capture_output=True, env=env, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.decode("utf-8", "replace"))
    return json.loads(proc.stdout.decode("utf-8-sig"))


class ShellQuotingTests(unittest.TestCase):
    def test_posix_join_round_trips_literally(self):
        import shlex

        line = cli._posix_join("claude", HOSTILE_ARGS)
        self.assertEqual(shlex.split(line), ["claude", *HOSTILE_ARGS])

    def test_powershell_quote_doubles_every_single_quote_form(self):
        self.assertEqual(cli._powershell_quote("it's"), "'it''s'")
        self.assertEqual(cli._powershell_quote("\u2019"), "'\u2019\u2019'")
        self.assertEqual(cli._powershell_quote("$(x)`;&"), "'$(x)`;&'")

    def test_powershell_program_must_be_plain_name(self):
        for bad in ("cla ude", "x;y", "$(x)", "& x", "a/b"):
            with self.assertRaises(ValueError):
                cli._powershell_join(bad, [])

    def test_powershell_refuses_arguments_it_cannot_pass_literally(self):
        # Even single-quoted, these reach native programs altered in some PowerShell
        # version: --% switches to verbatim mode (pwsh 7 and 5.1); 5.1 drops empty strings,
        # mangles double quotes, and a trailing backslash after whitespace.
        for bad in ("--%", "", 'a"b', "C:\\dir with space\\", "\\\\server\\share x\\"):
            with self.subTest(arg=bad), self.assertRaises(ValueError):
                cli._powershell_join("claude", ["a", bad, "b c"])

    @unittest.skipUnless(os.name == "nt", "PowerShell output is only produced on Windows")
    def test_config_refuses_unquotable_root_with_usage_error(self):
        args = cli.build_parser().parse_args(["--root", "C:\\x", "config", "--client", "claude"])
        args.root = Path("\\\\server\\share x\\")  # a UNC share root keeps its trailing backslash
        self.assertTrue(str(args.root).endswith("\\"))
        with mock.patch.object(Path, "is_dir", return_value=True):
            with self.assertRaises(cli.UsageError) as ctx:
                cli._config_or_usage_error(args)
        self.assertEqual(ctx.exception.code, "unsafe_shell_argument")
        self.assertIn("generic JSON", str(ctx.exception))

    @unittest.skipUnless(POWERSHELL, "PowerShell not available")
    def test_powershell_parser_sees_only_single_quoted_literals(self):
        line = cli._powershell_join("claude", HOSTILE_ARGS)
        for shell in POWERSHELLS:
            with self.subTest(shell=shell):
                self._check_parsed(powershell_parse(line, shell))

    def _check_parsed(self, parsed):
        self.assertEqual(parsed["errors"], 0)
        self.assertEqual(parsed["statements"], 1)
        self.assertEqual(parsed["commands"], 1)
        elements = parsed["elements"]
        self.assertEqual(elements[0], {"kind": "BareWord", "value": "claude"})
        self.assertEqual([e["kind"] for e in elements[1:]], ["SingleQuoted"] * len(HOSTILE_ARGS))
        self.assertEqual([e["value"] for e in elements[1:]], HOSTILE_ARGS)

    @unittest.skipUnless(POWERSHELL, "PowerShell not available")
    def test_powershell_passes_exact_argv_to_a_program(self):
        """Run the quoted arguments through PowerShell into a script that only records argv."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            recorder = tmp / "write_args.py"
            out = tmp / "argv.json"
            recorder.write_text(
                "import json, os, sys\n"
                "with open(os.environ['UW_ARGV_OUT'], 'w', encoding='utf-8') as f:\n"
                "    json.dump(sys.argv[1:], f)\n",
                encoding="utf-8",
            )
            quoted_args = cli._powershell_join("x", HOSTILE_ARGS)[len("x "):]
            script = tmp / "run.ps1"
            script.write_text(
                f"& {cli._powershell_quote(sys.executable)} {cli._powershell_quote(str(recorder))} {quoted_args}\n",
                encoding="utf-8-sig",
            )
            env = dict(os.environ, UW_ARGV_OUT=str(out), UW_NEUTRAL_MARKER="EXPANDED-MARKER")
            for shell in POWERSHELLS:
                with self.subTest(shell=shell):
                    if out.exists():
                        out.unlink()
                    proc = subprocess.run(
                        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                        capture_output=True, env=env, timeout=120,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
                    received = json.loads(out.read_text(encoding="utf-8"))
                    self.assertEqual(received, HOSTILE_ARGS)
                    self.assertNotIn("EXPANDED-MARKER", json.dumps(received))

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell output only")
    def test_config_output_for_hostile_root_parses_to_literal_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "proj $(uwmark) `x 'q' \u2019c & ;y"
            root.mkdir()
            for client, prefix in (
                ("claude", ["mcp", "add", "--transport", "stdio", "--scope", "project", "undoweave", "--"]),
                ("codex", ["mcp", "add", "undoweave", "--"]),
            ):
                with self.subTest(client=client):
                    proc = run_cli("--root", str(root), "config", "--client", client)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    text = proc.stdout.decode("utf-8")
                    line = next(l for l in text.splitlines() if l.startswith(client + " "))
                    parsed = powershell_parse(line)
                    self.assertEqual(parsed["errors"], 0)
                    self.assertEqual(parsed["statements"], 1)
                    values = [e["value"] for e in parsed["elements"]]
                    kinds = {e["kind"] for e in parsed["elements"][1:]}
                    self.assertEqual(kinds, {"SingleQuoted"})
                    self.assertEqual(
                        values,
                        [client, *prefix, sys.executable, "-I", "-m", "undoweave", "--root", str(root), "serve"],
                    )


class _EndlessStream(io.RawIOBase):
    """Pretends to be an infinite stdin; counts how much the CLI pulls from it."""

    def __init__(self):
        self.consumed = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        buffer[:] = b"a" * len(buffer)
        self.consumed += len(buffer)
        return len(buffer)


@unittest.skipUnless(CORE_AVAILABLE, "undoweave core engine not importable yet")
class ContentInputBoundsTests(unittest.TestCase):
    def setUp(self):
        from undoweave.engine import MAX_BYTES

        self.limit = MAX_BYTES
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        self.inputs = base / "inputs"
        self.inputs.mkdir()
        self.args = ["--root", str(self.root), "--state-dir", str(base / "state")]

    def tearDown(self):
        self._tmp.cleanup()

    def edit(self, source: str, stdin: bytes | None = None):
        return run_cli(*self.args, "edit", "t.txt", "--content-file", source,
                       "--actor", "agent", "--expected-hash", "missing", stdin=stdin)

    def assert_refused(self, proc, code: str):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(as_json(proc.stderr)["error"]["code"], code)
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertFalse((self.root / "t.txt").exists())

    def test_oversized_file_refused(self):
        big = self.inputs / "big.txt"
        big.write_bytes(b"a" * (self.limit + 1))
        self.assert_refused(self.edit(str(big)), "content_limit")

    def test_file_at_limit_reaches_core(self):
        exact = self.inputs / "exact.txt"
        exact.write_bytes(b"a" * self.limit)
        proc = self.edit(str(exact))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.root / "t.txt").stat().st_size, self.limit)

    def test_oversized_stdin_refused(self):
        self.assert_refused(self.edit("-", stdin=b"a" * (self.limit + 10)), "content_limit")

    def test_stdin_within_limit_accepted(self):
        proc = self.edit("-", stdin=b"line one\r\nline two\r\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.root / "t.txt").read_bytes(), b"line one\r\nline two\r\n")

    def test_stdin_read_is_bounded_even_if_endless(self):
        stream = _EndlessStream()
        fake_stdin = mock.Mock()
        fake_stdin.buffer = io.BufferedReader(stream, buffer_size=8192)
        with mock.patch.object(cli.sys, "stdin", fake_stdin):
            with self.assertRaises(cli.UsageError) as ctx:
                cli._read_content_file("-")
        self.assertEqual(ctx.exception.code, "content_limit")
        self.assertLessEqual(stream.consumed, self.limit + 1 + 8192)

    def test_directory_refused(self):
        self.assert_refused(self.edit(str(self.inputs)), "unsafe_input")

    def test_symlink_refused(self):
        real = self.inputs / "real.txt"
        real.write_bytes(b"hello\n")
        link = self.inputs / "link.txt"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")
        self.assert_refused(self.edit(str(link)), "unsafe_input")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are POSIX-only")
    def test_fifo_refused_without_blocking(self):
        fifo = self.inputs / "pipe"
        os.mkfifo(fifo)
        proc = self.edit(str(fifo))  # run_cli has a timeout; a blocking open would fail the test
        self.assert_refused(proc, "unsafe_input")

    def test_missing_file_is_clean_error(self):
        proc = self.edit(str(self.inputs / "nope.txt"))
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(b"Traceback", proc.stderr)


@unittest.skipUnless(CORE_AVAILABLE, "undoweave core engine not importable yet")
class CliWithCoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        self.state = base / "state"
        self.inputs = base / "inputs"
        self.inputs.mkdir()
        self.base_args = ["--root", str(self.root), "--state-dir", str(self.state)]

    def tearDown(self):
        self._tmp.cleanup()

    def cli_ok(self, *args: str) -> dict:
        proc = run_cli(*self.base_args, *args)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return as_json(proc.stdout)

    def content_file(self, name: str, data: bytes) -> str:
        path = self.inputs / name
        path.write_bytes(data)
        return str(path)

    def test_demo_passes_and_is_json(self):
        proc = run_cli("demo")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        result = as_json(proc.stdout)
        self.assertTrue(result["ok"])
        self.assertIn("timeout = 99", result["final_content"])
        self.assertIn("owner = agent-b", result["final_content"])
        self.assertIn("name = demo", result["final_content"])

    def test_status(self):
        status = self.cli_ok("status")
        self.assertIsInstance(status, dict)

    def test_edit_undo_roundtrip_preserves_human_edit_and_crlf(self):
        original = b"one\r\ntwo\r\nthree\r\nfour\r\nfive\r\nsix\r\nseven\r\n"
        target = self.root / "notes.txt"
        target.write_bytes(original)
        first = self.cli_ok("read", "notes.txt")
        self.assertTrue(first["exists"])

        agent_bytes = original.replace(b"two", b"TWO-agent")
        edit = self.cli_ok(
            "edit", "notes.txt", "--content-file", self.content_file("a.txt", agent_bytes),
            "--actor", "agent-a", "--expected-hash", first["sha256"], "--label", "agent change",
        )
        self.assertEqual(target.read_bytes(), agent_bytes)

        # External human edit, far from the agent's line.
        human = agent_bytes.replace(b"six", b"SIX-human")
        target.write_bytes(human)

        history = self.cli_ok("history", "--limit", "10")
        self.assertTrue(any(item.get("id") == edit["id"] for item in history["changes"]))
        self.assertNotIn("TWO-agent", json.dumps(history))  # summaries only

        preview = self.cli_ok("preview", edit["id"])
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(target.read_bytes(), human)  # preview is read-only for project files
        self.cli_ok("undo", preview["token"])
        self.assertEqual(target.read_bytes(), original.replace(b"six", b"SIX-human"))

        # The token is one-use.
        proc = run_cli(*self.base_args, "undo", preview["token"])
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn(b"Traceback", proc.stderr)

    def test_stale_hash_refused_without_write(self):
        target = self.root / "a.txt"
        target.write_bytes(b"hello\n")
        proc = run_cli(
            *self.base_args, "edit", "a.txt", "--content-file", self.content_file("n.txt", b"bye\n"),
            "--actor", "agent", "--expected-hash", "0" * 64,
        )
        self.assertEqual(proc.returncode, 1)
        err = as_json(proc.stderr)
        self.assertFalse(err["ok"])
        self.assertTrue(err["error"]["code"])
        self.assertNotIn(b"Traceback", proc.stderr)
        self.assertEqual(target.read_bytes(), b"hello\n")

    def test_create_and_delete(self):
        created = self.cli_ok(
            "edit", "new.txt", "--content-file", self.content_file("c.txt", "héllo\n".encode("utf-8")),
            "--actor", "agent", "--expected-hash", "missing",
        )
        self.assertEqual((self.root / "new.txt").read_bytes(), "héllo\n".encode("utf-8"))
        self.cli_ok("edit", "new.txt", "--delete", "--actor", "agent", "--expected-hash", created["after_hash"])
        self.assertFalse((self.root / "new.txt").exists())

    def test_non_utf8_content_file_refused(self):
        proc = run_cli(
            *self.base_args, "edit", "x.txt", "--content-file", self.content_file("b.bin", b"\xff\xfe\x00"),
            "--actor", "agent", "--expected-hash", "missing",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertFalse((self.root / "x.txt").exists())

    def test_path_outside_root_refused(self):
        proc = run_cli(*self.base_args, "read", "../outside.txt")
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn(b"Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
