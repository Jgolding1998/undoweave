"""Independent startup/identity regressions (workspace marker + directory identity).

All projects and journals are disposable temporary directories. Each test
asserts desired behavior. These regressions reproduced concurrent-marker and
cross-version volume-identity failures before the v0.2 startup fixes.
"""

from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import undoweave.engine as engine_module
from undoweave.engine import Engine, UndoWeaveError

MARKER = ".undoweave-id"

# Exact v0.1 DDL and metadata (from released commit e37c81a engine.py).
V1_DDL = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS changes (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    path TEXT NOT NULL, path_key TEXT NOT NULL, actor TEXT NOT NULL, label TEXT NOT NULL,
    before_text TEXT, after_text TEXT, before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
    status TEXT NOT NULL, created REAL NOT NULL, undoes TEXT, undone_by TEXT);
CREATE TABLE IF NOT EXISTS plans (
    token TEXT PRIMARY KEY, edit_id TEXT NOT NULL, current_hash TEXT NOT NULL,
    result_text TEXT, created REAL NOT NULL, head INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0);
"""


def _start_engine(args):
    root, state, barrier = args
    barrier.wait(timeout=30)
    try:
        engine = Engine(root, state)
        return ("ok", engine._workspace_id)
    except UndoWeaveError as error:
        return ("error", error.code)


def _legacy_windows_identity(path):
    """Identity as Python <=3.11 reported it (GetFileInformationByHandle)."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("serial", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(str(path), 0x80, 7, None, 3, 0x02000000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error())
    try:
        info = Info()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)
    return f"{info.serial}:{(info.index_high << 32) | info.index_low}"


class IdentityStartup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="undoweave-identity-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        (self.root / "a.txt").write_bytes(b"one\ntwo\n")

    def make_v1_journal(self, state):
        state.mkdir(mode=0o700)
        with closing(sqlite3.connect(state / "journal.sqlite3")) as db:
            db.executescript(V1_DDL)
            db.execute("INSERT INTO metadata VALUES ('root', ?)", (str(self.root.resolve()),))
            db.execute("INSERT INTO metadata VALUES ('schema', '1')")
            db.commit()
        if os.name != "nt":
            os.chmod(state / "journal.sqlite3", 0o600)

    # Empty v0.1 journal: migrates silently and stays fully usable.
    def test_empty_v1_journal_migrates_and_round_trips(self):
        state = self.base / "state"
        self.make_v1_journal(state)
        engine = Engine(self.root, state)
        marker = (self.root / MARKER).read_bytes()
        self.assertRegex(marker, rb"^[a-f0-9]{32}\n$")
        with closing(sqlite3.connect(state / "journal.sqlite3")) as db:
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            columns = {row[1] for row in db.execute("PRAGMA table_info(plans)")}
        self.assertEqual(meta["schema"], "2")
        self.assertEqual(meta["workspace_id"], marker[:32].decode())
        self.assertIn("result_hash", columns)
        change = engine.edit("a.txt", "one\nTWO\n", "agent", engine.read("a.txt")["sha256"])
        preview = engine.preview_undo(change["id"])
        self.assertEqual(preview["status"], "ready")
        engine.apply_undo(preview["token"])
        self.assertEqual((self.root / "a.txt").read_bytes(), b"one\ntwo\n")
        # A second startup binds the same identity without rewriting the marker.
        Engine(self.root, state)
        self.assertEqual((self.root / MARKER).read_bytes(), marker)

    # Crash after O_EXCL but before the write: refuse, preserve the evidence.
    def test_empty_marker_from_interrupted_creation_refuses_without_repair(self):
        (self.root / MARKER).write_bytes(b"")
        with self.assertRaises(UndoWeaveError) as caught:
            Engine(self.root, self.base / "fresh-state")
        self.assertEqual(caught.exception.code, "invalid_workspace_marker")
        self.assertEqual((self.root / MARKER).read_bytes(), b"")
        self.assertEqual((self.root / "a.txt").read_bytes(), b"one\ntwo\n")

    # Two different state dirs initializing the same project concurrently
    # (real processes). Every start must succeed and share one marker.
    def test_concurrent_fresh_journals_in_separate_processes_share_one_marker(self):
        context = multiprocessing.get_context("spawn")
        failures = []
        for attempt in range(5):
            root = self.base / f"race-{attempt}"
            root.mkdir()
            states = [self.base / f"race-{attempt}-state-{n}" for n in range(4)]
            with context.Manager() as manager:
                barrier = manager.Barrier(len(states))
                with context.Pool(len(states)) as pool:
                    results = pool.map(_start_engine, [(root, s, barrier) for s in states])
            ids = {value for status, value in results if status == "ok"}
            if any(status != "ok" for status, _ in results) or len(ids) != 1:
                failures.append(results)
            marker = (root / MARKER).read_bytes()
            self.assertRegex(marker, rb"^[a-f0-9]{32}\n$")
            self.assertEqual(ids - {marker[:32].decode()}, set())
        self.assertEqual(failures, [])

    # Deterministic version of the race: a second journal starts while the
    # first has created the marker with O_EXCL but not yet written it.
    def test_second_journal_waits_for_marker_being_published(self):
        created = threading.Event()
        release = threading.Event()
        real_write = os.write

        def slow_write(fd, data):
            if len(data) == 33 and threading.current_thread().name == "first":
                created.set()
                release.wait(10)
            return real_write(fd, data)

        outcome = {}

        def first():
            try:
                outcome["first"] = Engine(self.root, self.base / "state-1")._workspace_id
            except UndoWeaveError as error:
                outcome["first"] = error.code

        with patch.object(engine_module.os, "write", slow_write):
            thread = threading.Thread(target=first, name="first")
            thread.start()
            self.assertTrue(created.wait(10))
            timer = threading.Timer(0.3, release.set)
            timer.start()
            try:
                second = Engine(self.root, self.base / "state-2")._workspace_id
            except UndoWeaveError as error:
                second = error.code
            finally:
                release.set()
                thread.join(10)
                timer.cancel()
        self.assertRegex(second, r"^[a-f0-9]{32}$")
        self.assertEqual(second, outcome["first"])

    # Directory identity must not change when only the Python version changes.
    # CPython 3.12+ reports the 64-bit volume serial in st_dev on Windows;
    # 3.11 reported the 32-bit dwVolumeSerialNumber.
    @unittest.skipUnless(os.name == "nt", "Windows volume-serial semantics")
    def test_directory_identity_matches_python_311_windows_identity(self):
        engine = Engine(self.root, self.base / "state")
        legacy = _legacy_windows_identity(engine.root)
        current = engine._directory_identity()
        if sys.version_info >= (3, 12) and current != legacy:
            self.fail(f"identity differs from Python 3.11 form: {current} != {legacy}; "
                      "a journal bound under 3.11 refuses as workspace_replaced after upgrade")
        self.assertEqual(current, legacy)


if __name__ == "__main__":
    unittest.main()
