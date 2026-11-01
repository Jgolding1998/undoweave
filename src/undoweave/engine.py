"""Bounded file access, durable journal, and preview-bound selective undo."""

from __future__ import annotations

from contextlib import contextmanager
from difflib import unified_diff
from hashlib import sha256
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import unicodedata
import uuid

from . import __version__
from .merge import inverse_merge, MergeConflict

MAX_BYTES = 1_048_576
MAX_LINES = 4000
MAX_STATE_BYTES = 128 * MAX_BYTES
PLAN_SECONDS = 300
MISSING = "missing"
PROTECTED_DIRS = {".git", ".hg", ".svn", ".undoweave", ".ssh", ".aws", ".azure", ".gnupg", ".venv", "node_modules"}
PROTECTED_NAMES = {"credentials", "credentials.json", "secrets.json", "tokens.json", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".git-credentials", ".pgpass", ".netrc", ".npmrc", ".pypirc"}
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
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in text)


def _display_diff(text: str) -> str:
    # Visible escapes prevent terminal/control/bidi spoofing in a reviewed diff.
    return "".join(character if character in "\n\t" or unicodedata.category(character) not in {"Cc", "Cf"}
                   else ("\\u" + format(ord(character), "04x") if ord(character) <= 0xffff
                         else "\\U" + format(ord(character), "08x")) for character in text)


def _hash(content: str | None) -> str:
    return MISSING if content is None else sha256(content.encode("utf-8")).hexdigest()


def _validate_content(content: str | None) -> None:
    if content is None:
        return
    if not isinstance(content, str):
        raise UndoWeaveError("invalid_content", "Content must be UTF-8 text or null.")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError:
        raise UndoWeaveError("invalid_content", "Content must be valid UTF-8 text.") from None
    if b"\0" in encoded or len(encoded) > MAX_BYTES or len(content.splitlines()) > MAX_LINES:
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
    def __init__(self, root: Path | str, state_dir: Path | str | None = None):
        supplied = Path(root).absolute()
        _check_chain(supplied)
        if not supplied.is_dir():
            raise UndoWeaveError("invalid_root", "Choose an existing project directory.")
        self.root = supplied.resolve()
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
                        result_text TEXT, created REAL NOT NULL, head INTEGER NOT NULL,
                        used INTEGER NOT NULL DEFAULT 0);
                """)
                bound = db.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
                if bound is not None and bound[0] != str(self.root):
                    raise UndoWeaveError("wrong_workspace", "This journal belongs to another project.")
                db.execute("INSERT OR IGNORE INTO metadata VALUES ('root', ?)", (str(self.root),))
                db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema', '1')")
                if db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0] != "1":
                    raise UndoWeaveError("invalid_state", "This journal schema is unsupported.")
                db.commit()
                self._recover(db)
        except sqlite3.Error:
            raise UndoWeaveError("invalid_state", "The journal is unreadable; no project file was changed.") from None

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
        with self._journal_lock():
            with self._open_connection() as db:
                yield db

    @contextmanager
    def _journal_lock(self):
        _check_chain(self.state_dir)
        lock_path = self.state_dir / "session.lock"
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
            if info.st_size == 0:
                os.write(fd, b"\0")
            deadline = time.monotonic() + 5
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

    def _path(self, path: str) -> Path:
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
        if not target.parent.is_dir():
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

    def _read(self, path: str) -> tuple[str | None, int | None]:
        target = self._path(path)
        try:
            initial = target.lstat()
        except FileNotFoundError:
            return None, None
        if not _regular(initial) or _linked(initial):
            raise UndoWeaveError("unsafe_file", "Only regular files with a single link are supported.")
        if initial.st_size > MAX_BYTES:
            raise UndoWeaveError("content_limit", "File exceeds the 1 MiB limit.")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if not _regular(opened) or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino):
                raise UndoWeaveError("unsafe_file", "The file changed during validation.")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read(MAX_BYTES + 1)
            final = os.fstat(descriptor)
            if (opened.st_size, opened.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                raise UndoWeaveError("stale_file", "The file changed while it was being read.")
        finally:
            os.close(descriptor)
        try:
            content = data.decode("utf-8")
        except UnicodeError:
            raise UndoWeaveError("binary_file", "Only valid UTF-8 text is supported.") from None
        _validate_content(content)
        return content, stat.S_IMODE(initial.st_mode)

    def read(self, path: str) -> dict:
        path = self._canonical(path)
        content, _ = self._read(path)
        return {"path": path, "exists": content is not None, "content": content, "sha256": _hash(content)}

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
        for row in db.execute("SELECT * FROM changes WHERE status='pending'").fetchall():
            self._row_check(row)
            current, _ = self._read(row["path"])
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
        return {key: row[key] for key in ("id", "path", "actor", "label", "before_hash", "after_hash", "status", "created", "undoes", "undone_by")}

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
        if self.db_path.stat().st_size + len((before or "").encode()) + len((after or "").encode()) > MAX_STATE_BYTES:
            raise UndoWeaveError("storage_limit", "The journal reached its storage limit; keep a backup before archiving it.")
        change_id = uuid.uuid4().hex
        db.execute("INSERT INTO changes (id,path,path_key,actor,label,before_text,after_text,before_hash,after_hash,status,created,undoes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (change_id, path, self._path_key(path), actor, label, before, after, _hash(before), _hash(after), "pending", time.time(), undoes))
        db.commit()  # Durable intent precedes changing project bytes.
        try:
            self._replace(path, after, _hash(before))
        except _UnchangedWrite as failure:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE changes SET status='aborted' WHERE id=?", (change_id,))
            db.commit()
            if isinstance(failure.original, UndoWeaveError):
                raise failure.original from None
            raise UndoWeaveError("write_refused", "The write failed before any project mutation was attempted.") from None
        except Exception:
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

    def history(self, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise UndoWeaveError("invalid_limit", "Choose a history limit from 1 to 200.")
        with self._connection() as db:
            return [self._summary(row) for row in db.execute("SELECT id,path,actor,label,before_hash,after_hash,status,created,undoes,undone_by FROM changes ORDER BY sequence DESC LIMIT ?", (limit,))]

    def preview_undo(self, edit_id: str) -> dict:
        if not isinstance(edit_id, str) or not re.fullmatch(r"[a-f0-9]{32}", edit_id):
            raise UndoWeaveError("invalid_id", "Use a change ID returned by list_changes.")
        with self._connection() as db:
            self._recover(db)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM changes WHERE id=?", (edit_id,)).fetchone()
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
            raw_diff = "".join(unified_diff((current or "").splitlines(keepends=True), (result or "").splitlines(keepends=True), fromfile="current/" + row["path"], tofile="undo/" + row["path"]))
            diff = _display_diff(raw_diff)
            if len(diff) > 16000:
                return {"status": "conflict", "path": row["path"], "reason": "The diff exceeds the 16000-character review limit. Review and recover this change manually."}
            if self.db_path.stat().st_size + len((result or "").encode()) + 8192 > MAX_STATE_BYTES:
                raise UndoWeaveError("storage_limit", "The journal reached its preview storage limit.")
            token = uuid.uuid4().hex + uuid.uuid4().hex
            db.execute("DELETE FROM plans WHERE created < ?", (time.time() - PLAN_SECONDS,))
            if db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] >= 200:
                raise UndoWeaveError("plan_limit", "Too many live previews; wait five minutes before creating more.")
            head = db.execute("SELECT MAX(sequence) FROM changes WHERE path_key=?", (row["path_key"],)).fetchone()[0]
            db.execute("INSERT INTO plans (token,edit_id,current_hash,result_text,created,head) VALUES (?,?,?,?,?,?)", (token, edit_id, _hash(current), result, time.time(), head))
            db.commit()
            return {"status": "ready", "path": row["path"], "reason": "Review this inverse before applying it. Control and direction characters are displayed as escapes.", "diff": diff, "display_escaped": diff != raw_diff, "diff_truncated": False, "token": token, "expires_in_seconds": PLAN_SECONDS, "current_hash": _hash(current), "result_hash": _hash(result)}

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
            if result != plan["result_text"]:
                raise UndoWeaveError("invalid_state", "Preview content failed its integrity check.")
            db.execute("UPDATE plans SET used=1 WHERE token=?", (token,))
            return self._write_intent(db, row["path"], current, result, "undo", "Undo recorded edit", undoes=row["id"])

    def status(self) -> dict:
        with self._connection() as db:
            return {"version": __version__, "root": str(self.root), "state_dir": str(self.state_dir), "recorded_count": db.execute("SELECT COUNT(*) FROM changes WHERE status='applied'").fetchone()[0], "pending_count": db.execute("SELECT COUNT(*) FROM changes WHERE status='pending'").fetchone()[0], "limits": {"bytes_per_file": MAX_BYTES, "lines_per_file": MAX_LINES, "history_limit": 200, "preview_seconds": PLAN_SECONDS}, "tracking": "Only edits made through UndoWeave are attributed; actor labels are self-reported."}
