"""Bounded file access, durable journal, and preview-bound selective undo."""

from __future__ import annotations

from contextlib import contextmanager
from difflib import unified_diff
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import unicodedata
import uuid

from . import __version__
from .merge import inverse_merge, MergeConflict, physical_lines

MAX_BYTES = 1_048_576
MAX_LINES = 4000
MAX_STATE_BYTES = 128 * MAX_BYTES
MAX_OBSERVATION_BYTES = 256 * 1024
PLAN_SECONDS = 300
SCHEMA_VERSION = "2"
WORKSPACE_MARKER = ".undoweave-id"
MISSING = "missing"
PROTECTED_DIRS = {".git", ".hg", ".svn", ".undoweave", ".ssh", ".aws", ".azure", ".gnupg", ".venv", "node_modules"}
PROTECTED_NAMES = {WORKSPACE_MARKER, "credentials", "credentials.json", "secrets.json", "tokens.json", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".git-credentials", ".pgpass", ".netrc", ".npmrc", ".pypirc"}
DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9¹²³]|LPT[0-9¹²³])(?:\.|$)", re.I)


class UndoWeaveError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _UnchangedWrite(Exception):
    """An internal preflight failure before any project mutation was attempted."""
    def __init__(self, original):
        self.original = original


def _unsafe_metadata(text: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in text)


def _display_diff(text: str) -> str:
    # Visible escapes prevent terminal/control/bidi spoofing in a reviewed diff.
    return "".join(character if character in "\n\t" or unicodedata.category(character) not in {"Cc", "Cf", "Zl", "Zp"}
                   else ("\\u" + format(ord(character), "04x") if ord(character) <= 0xffff
                         else "\\U" + format(ord(character), "08x")) for character in text)


def _review_diff(current: str | None, result: str | None, path: str, *, before_label="current", after_label="undo") -> str:
    def ending(line):
        return "CRLF" if line.endswith("\r\n") else "CR" if line.endswith("\r") else "LF" if line.endswith("\n") else "NONE"
    before_lines, after_lines = physical_lines(current or ""), physical_lines(result or "")
    styles = [{ending(line) for line in lines} - {"NONE"} for lines in (before_lines, after_lines)]
    show_endings = len(styles[0] | styles[1]) > 1
    def display(lines):
        shown = []
        for line in lines:
            style = ending(line)
            body = line[:-2] if style == "CRLF" else line[:-1] if style != "NONE" else line
            shown.append(body + (f" [{style}]" if show_endings else "") + ("\n" if style != "NONE" else ""))
        return shown
    lines = unified_diff(display(before_lines), display(after_lines), fromfile=before_label + "/" + path, tofile=after_label + "/" + path)
    # difflib does not add Git's EOF marker, and otherwise joins -old+new into
    # one misleading line when either side has no final newline.
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def _line_ending_profile(text):
    if text is None:
        return None
    lines = physical_lines(text)
    return {"crlf": sum(line.endswith("\r\n") for line in lines), "lf": sum(line.endswith("\n") and not line.endswith("\r\n") for line in lines), "cr": sum(line.endswith("\r") for line in lines), "final_newline": bool(text) and text.endswith(("\r", "\n"))}


def _hash(content: str | None) -> str:
    return MISSING if content is None else sha256(content.encode("utf-8")).hexdigest()


def _validate_content(content: str | None) -> None:
    if content is None:
        return
    if not isinstance(content, str):
        raise UndoWeaveError("invalid_content", "Content must be UTF-8 text or null.")
    if len(content) > MAX_BYTES:
        raise UndoWeaveError("content_limit", "Content exceeds the 1 MiB UTF-8 text limit.")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError:
        raise UndoWeaveError("invalid_content", "Content must be valid UTF-8 text.") from None
    if b"\0" in encoded or len(encoded) > MAX_BYTES or len(physical_lines(content)) > MAX_LINES:
        raise UndoWeaveError("content_limit", "Binary text or content beyond the 1 MiB / 4000-line limit is unsupported.")


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _check_chain(path: Path) -> None:
    for candidate in [*reversed(path.parents), path]:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if _linked(info):
            raise UndoWeaveError("unsafe_path", "Symbolic links and reparse points are unsupported.")


def _mkdir_private(path: Path) -> None:
    _check_chain(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise UndoWeaveError("unsafe_state", "State must be a private directory.")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise UndoWeaveError("unsafe_state", "State directory must have owner-only permissions (chmod 700).")


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Engine:
    def __init__(self, root: Path | str, state_dir: Path | str | None = None, *, _adopt_legacy: bool = False):
        supplied = Path(root).absolute()
        _check_chain(supplied)
        if not supplied.is_dir():
            raise UndoWeaveError("invalid_root", "Choose an existing project directory.")
        self.root = supplied.resolve()
        self._root_identity = self._directory_identity()
        self._workspace_id = None
        self._initializing = True
        if state_dir is None:
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "state"))) / "UndoWeave" / "workspaces"
            state_dir = base / sha256(str(self.root).encode()).hexdigest()
        state_path = Path(state_dir).absolute()
        _check_chain(state_path)
        self.state_dir = state_path.resolve(strict=False)
        self._check_state_location()
        _mkdir_private(self.state_dir)
        self.state_dir = self.state_dir.resolve(strict=True)
        self._check_state_location()
        self.db_path = self.state_dir / "journal.sqlite3"
        try:
            with self._connection() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS changes (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                        path TEXT NOT NULL, path_key TEXT NOT NULL, actor TEXT NOT NULL, label TEXT NOT NULL,
                        before_text TEXT, after_text TEXT, before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
                        status TEXT NOT NULL, created REAL NOT NULL, undoes TEXT, undone_by TEXT);
                    CREATE TABLE IF NOT EXISTS plans (
                        token TEXT PRIMARY KEY, edit_id TEXT NOT NULL, current_hash TEXT NOT NULL,
                        result_text TEXT, result_hash TEXT, created REAL NOT NULL, head INTEGER NOT NULL,
                        used INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS observation_sessions (
                        id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL,
                        state TEXT NOT NULL, scopes_json TEXT NOT NULL, interval REAL NOT NULL,
                        last_scan REAL, files INTEGER NOT NULL DEFAULT 0,
                        skipped INTEGER NOT NULL DEFAULT 0, reason TEXT);
                    CREATE TABLE IF NOT EXISTS observation_records (
                        change_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                        observed_from REAL NOT NULL, observed_to REAL NOT NULL,
                        related_json TEXT NOT NULL);
                """)
                self._bind_journal(db, adopt_legacy=_adopt_legacy)
                db.commit()
                self._initializing = False
                try:
                    self._recover(db)
                except UndoWeaveError as error:
                    if error.code != "recovery_required":
                        raise
                    self._recovery_problem = str(error)
        except sqlite3.Error:
            raise UndoWeaveError("invalid_state", "The journal is unreadable; no project file was changed.") from None

    def _directory_identity(self) -> str:
        _check_chain(self.root)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or not info.st_ino:
            raise UndoWeaveError("unsupported_filesystem", "The project needs a stable directory identity.")
        # The nonce marker handles inode reuse. Keep this identity independent
        # of Python's platform/version-specific birthtime attribute availability.
        # CPython 3.12 expanded Windows volume serials to 64 bits. Normalize
        # to the serial reported by all supported versions; retain the nonce
        # and full inode checks as independent accidental-replacement guards.
        device = info.st_dev & 0xffffffff if os.name == "nt" else info.st_dev
        return f"{device}:{info.st_ino}"

    def _marker(self, *, create=False) -> str:
        path = self.root / WORKSPACE_MARKER
        if create and not path.exists():
            if self._directory_identity() != self._root_identity:
                raise UndoWeaveError("workspace_replaced", "The project directory was replaced. No edit was applied.")
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
            except FileExistsError:
                pass  # Another journal may have initialized this project concurrently.
            else:
                try:
                    os.write(fd, (uuid.uuid4().hex + "\n").encode("ascii"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                _sync_dir(self.root)
        try:
            # Another fresh journal can see O_EXCL's empty file before the
            # creator writes/fsyncs its nonce. Wait only during initialization,
            # and only for an ordinary empty marker; never replace or repair it.
            deadline = time.monotonic() + (2 if create else 0)
            while True:
                info = path.lstat()
                if _linked(info) or not _regular(info) or info.st_size != 0 or time.monotonic() >= deadline:
                    break
                time.sleep(0.025)
            if _linked(info) or not _regular(info) or info.st_size != 33:
                raise UndoWeaveError("invalid_workspace_marker", "The workspace marker is invalid. Preserve it for investigation.")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            try:
                opened = os.fstat(fd)
                if not _regular(opened) or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    raise UndoWeaveError("invalid_workspace_marker", "The workspace marker changed during validation.")
                data = os.read(fd, 34)
            finally:
                os.close(fd)
        except FileNotFoundError:
            raise UndoWeaveError("workspace_replaced", "The workspace identity marker is missing. No edit was applied.") from None
        if not re.fullmatch(rb"[a-f0-9]{32}\n", data):
            raise UndoWeaveError("invalid_workspace_marker", "The workspace marker is invalid. Preserve it for investigation.")
        return data[:32].decode("ascii")

    def _assert_root(self):
        if self._directory_identity() != self._root_identity:
            raise UndoWeaveError("workspace_replaced", "The project directory was replaced. Use a separate journal for the new project.")
        if self._workspace_id is not None and self._marker() != self._workspace_id:
            raise UndoWeaveError("workspace_replaced", "The project identity marker changed. No edit was applied.")

    def _bind_journal(self, db, *, adopt_legacy=False):
        values = dict(db.execute("SELECT key,value FROM metadata"))
        if values.get("root") not in (None, str(self.root)):
            raise UndoWeaveError("wrong_workspace", "This journal belongs to another project.")
        schema = values.get("schema")
        if schema not in (None, "1", SCHEMA_VERSION):
            raise UndoWeaveError("invalid_state", "This journal schema is unsupported.")
        if schema == SCHEMA_VERSION:
            if values.get("root_identity") != self._root_identity:
                raise UndoWeaveError("workspace_replaced", "This journal belongs to a different directory instance.")
            self._workspace_id = values.get("workspace_id")
            if not self._workspace_id or self._marker() != self._workspace_id:
                raise UndoWeaveError("workspace_replaced", "The project identity does not match this journal.")
        else:
            has_history = db.execute("SELECT EXISTS(SELECT 1 FROM changes)").fetchone()[0]
            if has_history and not adopt_legacy:
                raise UndoWeaveError("legacy_journal", "This v0.1 journal has no workspace identity. Review the migration guide and explicitly adopt it locally, or select a fresh state directory.")
            if adopt_legacy and db.execute("SELECT COUNT(*) FROM changes WHERE status='pending'").fetchone()[0]:
                raise UndoWeaveError("recovery_required", "Resolve interrupted legacy operations before adopting this journal.")
            self._workspace_id = self._marker(create=True)
            columns = {row[1] for row in db.execute("PRAGMA table_info(plans)")}
            if "result_hash" not in columns:
                db.execute("ALTER TABLE plans ADD COLUMN result_hash TEXT")
            db.execute("DELETE FROM plans")  # Previews are ephemeral; old tokens cannot bind a new identity.
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", [("root", str(self.root)), ("schema", SCHEMA_VERSION), ("root_identity", self._root_identity), ("workspace_id", self._workspace_id)])
        db.execute("CREATE INDEX IF NOT EXISTS changes_path_head ON changes(path_key,sequence)")
        db.execute("CREATE INDEX IF NOT EXISTS changes_status ON changes(status)")
        db.execute("CREATE INDEX IF NOT EXISTS plans_expiry ON plans(created)")

    def _verify_journal(self, db):
        values = dict(db.execute("SELECT key,value FROM metadata"))
        if (values.get("root") != str(self.root) or values.get("root_identity") != self._root_identity
                or values.get("workspace_id") != self._workspace_id or values.get("schema") != SCHEMA_VERSION):
            raise UndoWeaveError("wrong_workspace", "The journal binding changed. No project file was changed.")

    @classmethod
    def adopt_legacy(cls, root, state_dir=None, *, confirm_root: str):
        resolved = str(Path(root).resolve())
        if confirm_root != resolved:
            raise UndoWeaveError("confirmation_required", "Confirm the exact resolved project root after reviewing the legacy journal.")
        engine = cls(root, state_dir, _adopt_legacy=True)
        return {"status": "adopted", "root": str(engine.root), "state_dir": str(engine.state_dir), "schema": SCHEMA_VERSION}

    def _check_state_location(self):
        # Resolve spelling aliases and compare directory identities as well, so
        # dot-dot, Windows short names/subst, and root bind aliases cannot hide it.
        if self.state_dir == self.root or self.root in self.state_dir.parents:
            raise UndoWeaveError("unsafe_state", "Store the journal outside the project directory.")
        root_info = self.root.stat()
        for candidate in (self.state_dir, *self.state_dir.parents):
            try:
                info = candidate.stat()
            except FileNotFoundError:
                continue
            if (info.st_dev, info.st_ino) == (root_info.st_dev, root_info.st_ino):
                raise UndoWeaveError("unsafe_state", "The state directory resolves inside the project.")

    @contextmanager
    def _connection(self):
        # Span database commits AND filesystem replacement with a process lock.
        self._assert_root()
        with self._journal_lock():
            with self._open_connection() as db:
                if not self._initializing:
                    self._verify_journal(db)
                yield db

    @contextmanager
    def _journal_lock(self):
        with self._file_lock("session.lock", 5):
            yield

    @contextmanager
    def _observer_lease(self):
        try:
            with self._file_lock("observer.lock", 0):
                yield
        except UndoWeaveError as error:
            if error.code == "workspace_busy":
                raise UndoWeaveError("observer_busy", "Another recorder already owns this journal's observation lease.") from None
            raise

    @contextmanager
    def _file_lock(self, name, seconds):
        _check_chain(self.state_dir)
        lock_path = self.state_dir / name
        try:
            info = lock_path.lstat()
            if _linked(info) or not _regular(info):
                raise UndoWeaveError("unsafe_state", "The journal lock is unsafe.")
        except FileNotFoundError:
            pass
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
        acquired = False
        try:
            info = os.fstat(fd)
            if not _regular(info) or (os.name != "nt" and info.st_mode & 0o077):
                raise UndoWeaveError("unsafe_state", "The journal lock must be a private, regular file.")
            # Windows can lock byte zero beyond EOF. Do not write a marker
            # before locking: another process may already own that empty byte.
            deadline = time.monotonic() + seconds
            while not acquired:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise UndoWeaveError("workspace_busy", "Another UndoWeave process is busy. Try again shortly.") from None
                    time.sleep(0.025)
            yield
        finally:
            if acquired:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def _open_connection(self):
        _check_chain(self.state_dir)
        for candidate in (self.db_path, Path(str(self.db_path) + "-journal"), Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if _linked(info) or not _regular(info) or info.st_size > MAX_STATE_BYTES:
                raise UndoWeaveError("unsafe_state", "The local journal is unsafe or exceeds its storage limit.")
        if not self.db_path.exists():
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        if os.name != "nt" and self.db_path.stat().st_mode & 0o077:
            raise UndoWeaveError("unsafe_state", "Journal must have owner-only permissions (chmod 600).")
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={MAX_STATE_BYTES // page_size}")
            yield db
        except sqlite3.Error:
            db.rollback()
            raise UndoWeaveError("invalid_state", "The local journal could not be read or updated.") from None
        finally:
            db.close()

    def _path(self, path: str, *, require_parent: bool = True) -> Path:
        self._assert_root()
        if not isinstance(path, str) or not path or len(path) > 1024 or _unsafe_metadata(path):
            raise UndoWeaveError("unsafe_path", "Use a bounded project-relative path.")
        if any(c in path for c in '\\:<>"|?*\x7f') or path.startswith("/"):
            raise UndoWeaveError("unsafe_path", "Use relative paths with forward slashes; absolute paths and streams are unsupported.")
        parts = path.split("/")
        for part in parts:
            lowered = part.casefold()
            if part in {"", ".", ".."} or part.rstrip(" .") != part or DEVICE.match(part):
                raise UndoWeaveError("unsafe_path", "The relative path contains an unsafe component.")
            if (lowered in PROTECTED_DIRS or lowered in PROTECTED_NAMES or lowered == ".env"
                    or lowered.startswith(".env.") or lowered.endswith((".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3"))):
                raise UndoWeaveError("protected_path", "Credential, journal, dependency, and version-control paths are protected.")
        target = self.root.joinpath(*parts)
        _check_chain(target)
        target = target.resolve(strict=False)
        if self.root not in target.parents:
            raise UndoWeaveError("unsafe_path", "The path must stay inside the chosen project.")
        # Windows short-name aliases resolve to their long form. Recheck actual
        # components so an alias cannot bypass a credential or metadata denylist.
        for part in target.relative_to(self.root).parts:
            lowered = part.casefold()
            if (lowered in PROTECTED_DIRS or lowered in PROTECTED_NAMES or lowered == ".env"
                    or lowered.startswith(".env.") or lowered.endswith((".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3"))):
                raise UndoWeaveError("protected_path", "The resolved path is protected.")
        if require_parent and not target.parent.is_dir():
            raise UndoWeaveError("missing_parent", "The parent directory must already exist.")
        if target == self.state_dir or self.state_dir in target.parents:
            raise UndoWeaveError("protected_path", "The journal cannot be edited through this tool.")
        return target

    def _canonical(self, path: str) -> str:
        return self._path(path).relative_to(self.root).as_posix()

    @staticmethod
    def _path_key(path: str) -> str:
        # Used only for history invalidation, NEVER as a filesystem destination.
        # On case-sensitive Windows directories this may invalidate extra plans,
        # a conservative refusal rather than conflating their actual file writes.
        return os.path.normcase(path).replace("\\", "/")

    def _read(self, path: str, *, allow_missing_parent: bool = False, max_bytes: int = MAX_BYTES) -> tuple[str | None, int | None]:
        target = self._path(path, require_parent=not allow_missing_parent)
        try:
            initial = target.lstat()
        except FileNotFoundError:
            return None, None
        if not _regular(initial) or _linked(initial):
            raise UndoWeaveError("unsafe_file", "Only regular files with a single link are supported.")
        if initial.st_size > max_bytes:
            raise UndoWeaveError("content_limit", "File exceeds the 1 MiB limit." if max_bytes == MAX_BYTES else "File exceeds the selected bounded read limit.")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if not _regular(opened) or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino):
                raise UndoWeaveError("unsafe_file", "The file changed during validation.")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(max_bytes + 1)
            final = os.fstat(descriptor)
            if (opened.st_size, opened.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                raise UndoWeaveError("stale_file", "The file changed while it was being read.")
            # Atomic-save editors can leave the old descriptor consistent while
            # the pathname already names a replacement. Reject that old sample.
            try:
                named = self._path(path, require_parent=not allow_missing_parent).lstat()
            except (FileNotFoundError, NotADirectoryError):
                raise UndoWeaveError("stale_file", "The file changed while it was being read.") from None
            if (_linked(named) or not _regular(named)
                    or (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns)
                    != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
                raise UndoWeaveError("stale_file", "The file changed while it was being read.")
        finally:
            os.close(descriptor)
        if len(data) > max_bytes:
            raise UndoWeaveError("content_limit", "The file grew beyond the selected content limit.")
        try:
            content = data.decode("utf-8")
        except UnicodeError:
            raise UndoWeaveError("binary_file", "Only valid UTF-8 text is supported.") from None
        _validate_content(content)
        return content, stat.S_IMODE(initial.st_mode)

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None, metadata_only: bool = False) -> dict:
        path = self._canonical(path)
        content, _ = self._read(path)
        if type(metadata_only) is not bool:
            raise UndoWeaveError("invalid_arguments", "metadata_only must be a boolean.")
        ranged = start_line is not None or end_line is not None
        if metadata_only and ranged:
            raise UndoWeaveError("invalid_arguments", "Choose metadata_only or a line range, not both.")
        lines = physical_lines(content or "")
        result = {"path": path, "exists": content is not None, "content": content, "sha256": _hash(content), "byte_length": len((content or "").encode()), "line_count": len(lines), "content_scope": "full"}
        if metadata_only:
            result.update(content=None, content_scope="metadata")
        elif ranged:
            first, last = 1 if start_line is None else start_line, len(lines) if end_line is None else end_line
            if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(lines):
                raise UndoWeaveError("invalid_range", "Use an inclusive 1-based line range within this nonempty file.")
            result.update(content="".join(lines[first - 1:last]), content_scope="lines", start_line=first, end_line=last)
        return result

    @staticmethod
    def _row_check(row) -> None:
        try:
            _validate_content(row["before_text"])
            _validate_content(row["after_text"])
            valid = (_hash(row["before_text"]) == row["before_hash"] and _hash(row["after_text"]) == row["after_hash"])
            valid = valid and bool(re.fullmatch(r"[a-f0-9]{32}", row["id"]))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise UndoWeaveError("invalid_state", "The journal failed its integrity check.")

    def _recover(self, db) -> None:
        db.execute("BEGIN IMMEDIATE")
        self._gc_plans(db)
        for row in db.execute("SELECT * FROM changes WHERE status='pending'").fetchall():
            self._row_check(row)
            try:
                current, _ = self._read(row["path"])
            except UndoWeaveError as error:
                if error.code in {"binary_file", "content_limit", "unsafe_file"}:
                    raise UndoWeaveError("recovery_required", f"Interrupted operation {row['id']} has an unsupported target. Preserve the journal and file for manual recovery.") from None
                if error.code != "missing_parent":
                    raise
                current = None
            except OSError:
                raise UndoWeaveError("recovery_required", f"Interrupted operation {row['id']} cannot be inspected. Preserve the journal and file for manual recovery.") from None
            current_hash = _hash(current)
            if current_hash == row["after_hash"]:
                db.execute("UPDATE changes SET status='applied' WHERE id=?", (row["id"],))
                if row["undoes"]:
                    db.execute("UPDATE changes SET undone_by=? WHERE id=?", (row["id"], row["undoes"]))
            elif current_hash == row["before_hash"]:
                db.execute("UPDATE changes SET status='aborted' WHERE id=?", (row["id"],))
            else:
                raise UndoWeaveError("recovery_required", "An interrupted write has an unknown result. Preserve the journal and file for manual recovery.")
        db.commit()

    @staticmethod
    def _summary(row) -> dict:
        result = {key: row[key] for key in ("id", "path", "actor", "label", "before_hash", "after_hash", "status", "created", "undoes", "undone_by")}
        observed = row["status"] == "observed"
        result.update(source="filesystem_poll" if observed else "undoweave_tool",
                      attribution="unknown" if observed else "self_reported")
        if observed and "session_id" in row.keys():
            result["observation"] = {"session_id": row["session_id"], "observed_from": row["observed_from"],
                                     "observed_to": row["observed_to"], "related_change_ids": json.loads(row["related_json"]),
                                     "time_basis": "conservative_scan_bounds_not_exact_save_times",
                                     "snapshot_basis": "per_file_not_atomic_multi_file",
                                     "order_basis": "journal_sequence_not_actual_save_order",
                                     "clock_order_uncertain": row["observed_to"] < row["observed_from"]}
        return result

    def _replace(self, path: str, content: str | None, expected_hash: str) -> None:
        self._write_phase = "preparing"
        try:
            self._replace_impl(path, content, expected_hash)
        except Exception as error:
            if self._write_phase == "preparing":
                raise _UnchangedWrite(error) from None
            raise

    def _replace_impl(self, path: str, content: str | None, expected_hash: str) -> None:
        previous, mode = self._read(path)
        if _hash(previous) != expected_hash:
            raise UndoWeaveError("stale_file", "The file changed; read it again before editing.")
        target = self._path(path)
        if content is None:
            if previous is not None:
                # Final validation reduces, but cannot eliminate, races with outside editors.
                if _hash(self._read(path)[0]) != expected_hash:
                    raise UndoWeaveError("stale_file", "The file changed before deletion.")
                self._write_phase = "mutating"
                target.unlink()
                self._write_phase = "committed"
                _sync_dir(target.parent)
            return
        temp = target.parent / (".undoweave-write-" + uuid.uuid4().hex)
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            if mode is not None:
                os.chmod(temp, mode)
            if _hash(self._read(path)[0]) != expected_hash:
                raise UndoWeaveError("stale_file", "The file changed before replacement.")
            self._path(path)
            self._write_phase = "mutating"
            os.replace(temp, target)
            self._write_phase = "committed"
            _sync_dir(target.parent)
        finally:
            if descriptor != -1:
                os.close(descriptor)
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # A failed replacement of a Windows read-only file can leave its
                # temporary counterpart read-only too. Do not mask the real error.
                try:
                    os.chmod(temp, 0o600)
                    temp.unlink()
                except OSError:
                    pass

    def _write_intent(self, db, path, before, after, actor, label, undoes=None) -> dict:
        self._gc_plans(db)
        payload = len((before or "").encode()) + len((after or "").encode())
        target_payload = 0
        if undoes:
            target = db.execute("SELECT length(CAST(before_text AS BLOB)),length(CAST(after_text AS BLOB)) FROM changes WHERE id=?", (undoes,)).fetchone()
            target_payload = sum(size or 0 for size in target)
        # SQLite may temporarily rewrite large overflow payloads when updating
        # status and the original operation's undo link after the file write.
        # Reserve that peak allocation BEFORE crossing the filesystem boundary.
        if self._used_storage(db) + 2 * payload + target_payload + 65536 > MAX_STATE_BYTES:
            raise UndoWeaveError("storage_limit", "The journal reached its storage limit; keep a backup before archiving it.")
        change_id = uuid.uuid4().hex
        db.execute("INSERT INTO changes (id,path,path_key,actor,label,before_text,after_text,before_hash,after_hash,status,created,undoes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (change_id, path, self._path_key(path), actor, label, before, after, _hash(before), _hash(after), "pending", time.time(), undoes))
        db.commit()  # Durable intent precedes changing project bytes.
        self._write_phase = "unknown"
        try:
            self._replace(path, after, _hash(before))
        except _UnchangedWrite as failure:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE changes SET status='aborted' WHERE id=?", (change_id,))
            db.commit()
            if isinstance(failure.original, UndoWeaveError):
                raise failure.original from None
            raise UndoWeaveError("write_refused", "The write failed before any project mutation was attempted.") from None
        except Exception as error:
            if isinstance(error, OSError) and self._write_phase == "mutating":
                try:
                    observed = self._read(path)[0]
                except (OSError, UndoWeaveError):
                    observed = object()
                if isinstance(observed, (str, type(None))) and _hash(observed) == _hash(before):
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("UPDATE changes SET status='aborted' WHERE id=?", (change_id,))
                    db.commit()
                    raise UndoWeaveError("write_refused", "The filesystem refused the replacement and unchanged bytes were verified. Close conflicting handles or check permissions before retrying.") from None
            # Keep pending: startup can distinguish before, after, or unknown.
            raise
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE changes SET status='applied' WHERE id=?", (change_id,))
        if undoes:
            db.execute("UPDATE changes SET undone_by=? WHERE id=?", (change_id, undoes))
        db.commit()
        return self._summary(db.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone())

    def edit(self, path: str, content: str | None, actor: str, expected_hash: str, label: str = "") -> dict:
        path = self._canonical(path)
        _validate_content(content)
        if not isinstance(actor, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", actor):
            raise UndoWeaveError("invalid_actor", "Actor must be 1-64 letters, digits, dots, underscores, or hyphens.")
        if not isinstance(label, str) or len(label) > 200 or _unsafe_metadata(label):
            raise UndoWeaveError("invalid_label", "Label must be a single line of at most 200 characters.")
        if not isinstance(expected_hash, str) or not (expected_hash == MISSING or re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
            raise UndoWeaveError("invalid_hash", "Use the exact sha256 returned by read_file.")
        with self._connection() as db:
            self._recover(db)
            db.execute("BEGIN IMMEDIATE")
            before, _ = self._read(path)
            if _hash(before) != expected_hash:
                raise UndoWeaveError("stale_file", "Expected hash is stale; no file was changed.")
            if before == content:
                raise UndoWeaveError("no_change", "This edit does not change the file.")
            return self._write_intent(db, path, before, content, actor, label)

    def replace_text(self, path: str, old_text: str, new_text: str, actor: str, expected_hash: str, label: str = "") -> dict:
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise UndoWeaveError("invalid_match", "Use nonempty old_text and a text replacement.")
        _validate_content(old_text)
        _validate_content(new_text)
        snapshot = self.read(path)
        if snapshot["sha256"] != expected_hash:
            raise UndoWeaveError("stale_file", "Expected hash is stale; no file was changed.")
        content = snapshot["content"]
        if content is None:
            raise UndoWeaveError("missing_file", "Exact replacement requires an existing file.")
        position = content.find(old_text)
        if position < 0:
            normalized = lambda value: value.replace("\r\n", "\n").replace("\r", "\n")
            hint = " Line endings differ; copy exact old_text from read_file." if normalized(old_text) in normalized(content) else ""
            raise UndoWeaveError("match_not_found", "old_text was not found; no file was changed." + hint)
        if content.find(old_text, position + 1) >= 0:
            raise UndoWeaveError("ambiguous_match", "old_text must match exactly once, including overlapping occurrences.")
        replacement = content[:position] + new_text + content[position + len(old_text):]
        # edit rechecks both journal binding and the entire file hash under lock.
        return self.edit(path, replacement, actor, expected_hash, label)

    @staticmethod
    def _used_storage(db):
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        return (pages - free) * db.execute("PRAGMA page_size").fetchone()[0]

    @staticmethod
    def _gc_plans(db):
        db.execute("DELETE FROM plans WHERE used<>0 OR created < ?", (time.time() - PLAN_SECONDS,))

    def history(self, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise UndoWeaveError("invalid_limit", "Choose a history limit from 1 to 200.")
        with self._connection() as db:
            return [self._summary(row) for row in db.execute("""SELECT c.id,c.path,c.actor,c.label,c.before_hash,c.after_hash,c.status,c.created,c.undoes,c.undone_by,
                o.session_id,o.observed_from,o.observed_to,o.related_json FROM changes c
                LEFT JOIN observation_records o ON o.change_id=c.id ORDER BY c.sequence DESC LIMIT ?""", (limit,))]

    def begin_observation(self, scopes, interval=1.0) -> dict:
        """Internal recorder API; caller holds the exclusive observation lease."""
        if (not isinstance(scopes, (tuple, list)) or not 1 <= len(scopes) <= 16
                or type(interval) not in (int, float) or not 0.25 <= interval <= 60):
            raise UndoWeaveError("invalid_observation", "Use 1-16 relative scopes and a 0.25-60 second interval.")
        canonical = list(dict.fromkeys(self._canonical(scope) for scope in scopes))
        with self._connection() as db:
            if self._used_storage(db) + 65536 > MAX_STATE_BYTES or db.execute("SELECT COUNT(*) FROM observation_sessions").fetchone()[0] >= 1000:
                raise UndoWeaveError("storage_limit", "The journal reached its observation-session limit.")
            if db.execute("SELECT EXISTS(SELECT 1 FROM changes WHERE status='pending')").fetchone()[0]:
                raise UndoWeaveError("recovery_required", "Resolve interrupted writes before starting observation.")
            now = time.time()
            db.execute("UPDATE observation_sessions SET state='interrupted',ended=?,reason='unclean_stop' WHERE state IN ('active','initializing')", (now,))
            session_id = uuid.uuid4().hex
            db.execute("INSERT INTO observation_sessions(id,started,state,scopes_json,interval) VALUES (?,?,?,?,?)",
                       (session_id, now, "initializing", json.dumps(canonical), interval))
            db.commit()
            return {"session_id": session_id, "scopes": canonical, "head": db.execute("SELECT COALESCE(MAX(sequence),0) FROM changes").fetchone()[0]}

    def _observation_progress(self, session_id, *, files, skipped):
        with self._connection() as db:
            db.execute("UPDATE observation_sessions SET state='active',last_scan=?,files=?,skipped=? WHERE id=? AND state IN ('active','initializing')",
                       (time.time(), files, skipped, session_id))
            db.commit()

    def _observation_head(self):
        with self._connection() as db:
            return db.execute("SELECT COALESCE(MAX(sequence),0) FROM changes").fetchone()[0]

    def append_observation(self, session_id, path, before, after, observed_from, observed_to, *, recorded_head=0):
        """Append a sampled net transition, never a pending project-write intent."""
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (observed_from, observed_to))
                or type(recorded_head) is not int or recorded_head < 0):
            raise UndoWeaveError("invalid_observation", "Use finite sample times and a valid journal head.")
        for text in (before, after):
            _validate_content(text)
            if text is not None and len(text.encode("utf-8")) > MAX_OBSERVATION_BYTES:
                raise UndoWeaveError("observation_limit", "Observed text exceeds the 256 KiB per-file limit.")
        if before == after:
            raise UndoWeaveError("no_change", "The observed snapshots are identical.")
        path = self._path(path, require_parent=False).relative_to(self.root).as_posix()
        with self._connection() as db:
            session = db.execute("SELECT * FROM observation_sessions WHERE id=?", (session_id,)).fetchone()
            if session is None or session["state"] not in {"active", "initializing"}:
                raise UndoWeaveError("observation_stopped", "The observation session is not active.")
            scopes = json.loads(session["scopes_json"])
            if not any(path == scope or path.startswith(scope + "/") for scope in scopes):
                raise UndoWeaveError("unsafe_path", "The observation is outside the pinned scopes.")
            if db.execute("SELECT EXISTS(SELECT 1 FROM changes WHERE status='pending')").fetchone()[0]:
                raise UndoWeaveError("recovery_required", "Interrupted writes block new observations.")
            if _hash(self._read(path, allow_missing_parent=True, max_bytes=MAX_OBSERVATION_BYTES)[0]) != _hash(after):
                raise UndoWeaveError("stale_file", "The sampled file changed before observation was recorded.")
            known = db.execute("SELECT id,before_hash,after_hash FROM changes WHERE path=? AND sequence>? AND status='applied' ORDER BY sequence LIMIT 201",
                               (path, recorded_head)).fetchall()
            if len(known) > 200:
                raise UndoWeaveError("observation_limit", "Too many recorded writes occurred between samples.")
            accounted, contiguous = _hash(before), bool(known)
            for item in known:
                contiguous = contiguous and item["before_hash"] == accounted
                accounted = item["after_hash"]
            if contiguous and accounted == _hash(after):
                return {"status": "represented", "head": db.execute("SELECT COALESCE(MAX(sequence),0) FROM changes").fetchone()[0]}
            payload = len((before or "").encode()) + len((after or "").encode())
            if self._used_storage(db) + 2 * payload + 65536 > MAX_STATE_BYTES:
                raise UndoWeaveError("storage_limit", "The journal reached its observation storage limit.")
            change_id = uuid.uuid4().hex
            db.execute("INSERT INTO changes(id,path,path_key,actor,label,before_text,after_text,before_hash,after_hash,status,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (change_id, path, self._path_key(path), "unknown", "Observed net change between samples", before, after, _hash(before), _hash(after), "observed", time.time()))
            db.execute("INSERT INTO observation_records VALUES (?,?,?,?,?)",
                       (change_id, session_id, observed_from, observed_to, json.dumps([item["id"] for item in known])))
            db.commit()
            return {"status": "observed", "id": change_id, "head": db.execute("SELECT MAX(sequence) FROM changes").fetchone()[0]}

    def end_observation(self, session_id, reason="stopped"):
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
            raise UndoWeaveError("invalid_observation", "Use a bounded observation stop reason.")
        with self._connection() as db:
            db.execute("UPDATE observation_sessions SET state=?,ended=?,reason=? WHERE id=? AND state IN ('active','initializing')",
                       ("stopped" if reason == "stopped" else "interrupted" if reason == "interrupted" else "error", time.time(), reason, session_id))
            db.commit()

    def observation_status(self):
        with self._connection() as db:
            return self._observation_status(db)

    @staticmethod
    def _observation_status(db):
        row = db.execute("SELECT rowid,* FROM observation_sessions ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            return {"state": "off", "liveness": "not_checked", "check_evidence": "not_captured"}
        return {"session_id": row["id"], "state": row["state"], "liveness": "not_checked", "started": row["started"], "ended": row["ended"],
                "last_scan": row["last_scan"], "scopes": json.loads(row["scopes_json"]), "files": row["files"], "skipped": row["skipped"], "reason": row["reason"],
                "check_evidence": "not_captured", "limitations": "Sampled net changes can combine actors and miss intermediate saves; author and cause are unknown."}

    def inspect_change(self, change_id):
        if not isinstance(change_id, str) or not re.fullmatch(r"[a-f0-9]{32}", change_id):
            raise UndoWeaveError("invalid_id", "Use a change ID returned by history.")
        with self._connection() as db:
            row = db.execute("SELECT c.*,o.session_id,o.observed_from,o.observed_to,o.related_json FROM changes c LEFT JOIN observation_records o ON o.change_id=c.id WHERE c.id=?", (change_id,)).fetchone()
            if row is None:
                raise UndoWeaveError("unknown_change", "No recorded or observed change has that ID.")
            self._row_check(row)
            raw = _review_diff(row["before_text"], row["after_text"], row["path"], before_label="before", after_label="after")
            diff = _display_diff(raw)
            note = {
                "observed": "Two matching filesystem samples, with unknown authorship. This is a net transition, not one verified agent action.",
                "applied": "Applied or recovery-classified recorded operation. This is historical evidence, not a check of the current file or author identity.",
                "pending": "Unverified write intent. Its after snapshot may never have existed in the project.",
                "aborted": "Refused write intent. Its after snapshot was not applied by this recorded operation.",
            }.get(row["status"], "Unverified journal entry.")
            return {**self._summary(row), "diff": diff[:16000], "diff_truncated": len(diff) > 16000,
                    "diff_limit": 16000, "display_escaped": diff != raw, "check_evidence": "not_captured",
                    "cause": "not_inferred", "undo_supported": row["status"] == "applied",
                    "effect_verified": row["status"] in {"applied", "observed"}, "evidence_note": note}

    def preview_undo(self, edit_id: str) -> dict:
        if not isinstance(edit_id, str) or not re.fullmatch(r"[a-f0-9]{32}", edit_id):
            raise UndoWeaveError("invalid_id", "Use a change ID returned by list_changes.")
        with self._connection() as db:
            self._recover(db)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM changes WHERE id=?", (edit_id,)).fetchone()
            if row is not None and row["status"] == "observed":
                raise UndoWeaveError("observation_not_undoable", "This is a sampled net change with unknown authorship, not one recorded agent edit. Inspect it; automatic undo is not supported.")
            if row is None or row["status"] != "applied":
                raise UndoWeaveError("unknown_change", "No applied change has that ID.")
            self._row_check(row)
            if row["undone_by"]:
                return {"status": "already_undone", "path": row["path"], "reason": "This edit already has a recorded undo."}
            current, _ = self._read(row["path"])
            try:
                if current == row["after_text"]:
                    result = row["before_text"]
                elif None in (current, row["before_text"], row["after_text"]):
                    raise MergeConflict("Creation or deletion overlaps later work; the file was left untouched.")
                else:
                    result = inverse_merge(row["before_text"], row["after_text"], current)
            except MergeConflict as error:
                return {"status": "conflict", "path": row["path"], "reason": str(error)}
            _validate_content(result)
            raw_diff = _review_diff(current, result, row["path"])
            diff = _display_diff(raw_diff)
            if len(diff) > 16000:
                return {"status": "conflict", "path": row["path"], "reason": "The diff exceeds the 16000-character review limit. Review and recover this change manually."}
            self._gc_plans(db)
            if self._used_storage(db) + 8192 > MAX_STATE_BYTES:
                raise UndoWeaveError("storage_limit", "The journal reached its preview storage limit.")
            token = uuid.uuid4().hex + uuid.uuid4().hex
            if db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] >= 200:
                raise UndoWeaveError("plan_limit", "Too many live previews; wait five minutes before creating more.")
            head = db.execute("SELECT MAX(sequence) FROM changes WHERE path_key=?", (row["path_key"],)).fetchone()[0]
            db.execute("INSERT INTO plans (token,edit_id,current_hash,result_text,result_hash,created,head) VALUES (?,?,?,?,?,?,?)", (token, edit_id, _hash(current), None, _hash(result), time.time(), head))
            db.commit()
            return {"status": "ready", "path": row["path"], "operation": "delete" if result is None else "create" if current is None else "edit", "reason": "Review this inverse before applying it. Display newlines are normalized; exact source endings are preserved and described separately. Control and direction characters are displayed as escapes.", "diff": diff, "line_endings": {"current": _line_ending_profile(current), "result": _line_ending_profile(result)}, "display_escaped": diff != raw_diff, "diff_truncated": False, "token": token, "expires_in_seconds": PLAN_SECONDS, "current_hash": _hash(current), "result_hash": _hash(result)}

    def apply_undo(self, token: str) -> dict:
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
            raise UndoWeaveError("invalid_token", "Use a fresh preview token.")
        with self._connection() as db:
            self._recover(db)
            db.execute("BEGIN IMMEDIATE")
            plan = db.execute("SELECT * FROM plans WHERE token=?", (token,)).fetchone()
            if plan is None or plan["used"] or time.time() - plan["created"] > PLAN_SECONDS:
                raise UndoWeaveError("expired_plan", "The preview expired or was used. Preview again.")
            row = db.execute("SELECT * FROM changes WHERE id=?", (plan["edit_id"],)).fetchone()
            if row is None or row["status"] != "applied" or row["undone_by"]:
                raise UndoWeaveError("stale_plan", "The change history changed. Preview again.")
            head = db.execute("SELECT MAX(sequence) FROM changes WHERE path_key=?", (row["path_key"],)).fetchone()[0]
            if head != plan["head"]:
                raise UndoWeaveError("stale_plan", "The recorded history changed after preview. Preview again.")
            self._row_check(row)
            current, _ = self._read(row["path"])
            if _hash(current) != plan["current_hash"]:
                raise UndoWeaveError("stale_plan", "The file changed after preview. Preview again.")
            # Recompute instead of trusting a stored result if local state was edited.
            if current == row["after_text"]:
                result = row["before_text"]
            elif None not in (current, row["before_text"], row["after_text"]):
                try:
                    result = inverse_merge(row["before_text"], row["after_text"], current)
                except MergeConflict:
                    raise UndoWeaveError("stale_plan", "The inverse can no longer be applied safely.") from None
            else:
                raise UndoWeaveError("stale_plan", "The inverse can no longer be applied safely.")
            if plan["result_text"] is not None or _hash(result) != plan["result_hash"]:
                raise UndoWeaveError("invalid_state", "Preview content failed its integrity check.")
            db.execute("UPDATE plans SET used=1 WHERE token=?", (token,))
            return self._write_intent(db, row["path"], current, result, "undo", "Undo recorded edit", undoes=row["id"])

    def status(self) -> dict:
        with self._connection() as db:
            pending = db.execute("SELECT path FROM changes WHERE status='pending' LIMIT 20").fetchall()
            return {"version": __version__, "root": str(self.root), "state_dir": str(self.state_dir), "observation": self._observation_status(db), "recorded_count": db.execute("SELECT COUNT(*) FROM changes WHERE status='applied'").fetchone()[0], "pending_count": db.execute("SELECT COUNT(*) FROM changes WHERE status='pending'").fetchone()[0], "pending_paths": [row[0] for row in pending], "recovery_notice": "Pending operations block mutations until safely classified; status and history remain available." if pending else None, "limits": {"bytes_per_file": MAX_BYTES, "lines_per_file": MAX_LINES, "history_limit": 200, "preview_seconds": PLAN_SECONDS}, "tracking": "Tool actors are self-reported; opt-in filesystem observations have unknown authorship and may miss intermediate states."}
