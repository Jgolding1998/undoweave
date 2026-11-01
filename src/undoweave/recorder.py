"""Opt-in, bounded filesystem observations. No commands, hooks or project writes."""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import math
import os
from pathlib import Path
import re
import threading
import time

from .engine import (Engine, UndoWeaveError, MAX_OBSERVATION_BYTES, PROTECTED_DIRS,
                     PROTECTED_NAMES, DEVICE, _unsafe_metadata, _linked, _regular)

MAX_SCOPES = 16
MAX_FILES = 256
MAX_BASELINE_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_DEPTH = 32
SCAN_SECONDS = 5
MAX_IGNORE_BYTES = 16 * 1024
MAX_IGNORE_PATTERNS = 512
MAX_IGNORE_FILES = 128
MAX_IGNORE_TOTAL_BYTES = 64 * 1024
EXCLUDED_DIRS = PROTECTED_DIRS | {'.claude', '.codex', '.vscode', '.cursor', '.windsurf', '.continue', 'venv', 'env', 'dist', 'build',
    'target', 'vendor', 'coverage', '__pycache__', '.cache', '.next', '.nuxt',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.nox'}
SENSITIVE = re.compile(r'(^|[-_.])(secrets?|credentials?|tokens?|passwords?)([-_.]|$)', re.I)
CLIENT_NAMES = {'.mcp.json', 'mcp.json', 'mcp.yaml', 'mcp.yml', 'mcp.toml',
                'mcp_config.json', 'claude_desktop_config.json', 'settings.local.json'}
LIMITATIONS = ('Observed net changes can combine actors and miss intermediate saves or save/revert pairs. '
               'Author, intent and failure cause are unknown; no checks or test output are captured.')
IGNORE_POLICY = ('Conservative project .gitignore exclusions only: positive globs with broader globstar/case matching, no reinclusion; '
                 'global Git excludes and .git/info/exclude are not loaded. Not full Git ignore semantics.')


def _protected(parts):
    return any(part.casefold() in EXCLUDED_DIRS or part.casefold() in PROTECTED_NAMES or part.casefold() in CLIENT_NAMES
        or part.casefold() == '.env' or part.casefold().startswith(('.env.', '.undoweave-write-'))
        or part.casefold().endswith(('.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3'))
        or SENSITIVE.search(part) for part in parts)


def validate_scopes(scopes):
    """Format/privacy validation only; safe for config printing without I/O."""
    if not isinstance(scopes, (tuple, list)) or not 1 <= len(scopes) <= MAX_SCOPES:
        raise UndoWeaveError('invalid_scope', 'Choose 1-16 explicit relative file or directory scopes.')
    result = []
    for scope in scopes:
        if (not isinstance(scope, str) or not scope or len(scope) > 1024 or _unsafe_metadata(scope)
                or scope.startswith('/') or any(char in scope for char in '\\:<>"|?*\x7f')):
            raise UndoWeaveError('invalid_scope', 'Use literal project-relative paths with forward slashes.')
        parts = scope.split('/')
        if any(part in {'', '.', '..'} or part.rstrip(' .') != part or DEVICE.match(part) for part in parts):
            raise UndoWeaveError('invalid_scope', 'The observation scope contains an unsafe component.')
        if _protected(parts):
            raise UndoWeaveError('protected_path', 'Credential, client-config, dependency and generated paths are excluded.')
        if scope not in result:
            result.append(scope)
    return tuple(result)


@dataclass
class _Sample:
    text: str | None
    seen: float
    head: int


class Recorder:
    def __init__(self, engine: Engine, scopes, interval=1.0):
        if type(interval) not in (int, float) or not math.isfinite(interval) or not .25 <= interval <= 60:
            raise UndoWeaveError('invalid_interval', 'Choose a finite 0.25-60 second poll interval.')
        self.engine = engine
        self.scopes = validate_scopes(scopes)
        self.interval = float(interval)
        # Canonicalize aliases without reading any source or starting a session.
        self.scopes = tuple(dict.fromkeys(engine._canonical(scope) for scope in self.scopes))
        validate_scopes(self.scopes)
        self._baseline, self._candidates, self._blocked = {}, {}, set()
        self._session = None
        self._lease = None
        self._state, self._reason = 'off', None
        self._files = self._skipped = self._observed = 0
        self._last_scan = None
        self._last_scan_start = None
        self._last_head = 0
        self._stop = threading.Event()
        self._thread = None
        self._scan_lock = threading.Lock()
        self._state_lock = threading.RLock()

    def _ensure_session(self):
        if self._session is not None:
            return
        lease = self.engine._observer_lease()
        lease.__enter__()
        self._lease = lease
        try:
            self._session = self.engine.begin_observation(self.scopes, self.interval)
            self._state = 'initializing'
        except BaseException:
            self._lease = None
            lease.__exit__(None, None, None)
            raise

    @staticmethod
    def _ignored(path, rules):
        for base, pattern in rules:
            if base and not path.startswith(base + '/'):
                continue
            relative = path[len(base) + 1:] if base else path
            pieces = relative.split('/')
            candidates = ['/'.join(pieces[:index]) for index in range(1, len(pieces) + 1)]
            if '/' not in pattern:
                candidates += pieces
            # A bare fnmatch requires a slash for **/, unlike Git's zero-dir
            # globstar. Collapsing **/ to * is deliberately broader and bounded.
            pattern = pattern.replace('**/', '*')
            if any(fnmatch.fnmatchcase(value, pattern) or fnmatch.fnmatchcase(value.casefold(), pattern.casefold()) for value in candidates):
                return True
        return False

    def _snapshot(self):
        """A complete eligible census or a refusal; no partial deletion claims."""
        deadline = time.monotonic() + SCAN_SECONDS
        snapshots, skipped, rules, policy, visited = {}, set(), [], {}, set()
        entries = total_bytes = 0

        def budget():
            if self._stop.is_set():
                raise UndoWeaveError('observation_stopped', 'Recording was stopped before this scan completed.')
            if time.monotonic() > deadline:
                raise UndoWeaveError('scan_limit', 'The cooperative scan-time budget was exceeded.')

        def load_ignore(directory):
            relative = directory.relative_to(self.engine.root).as_posix()
            relative = '' if relative == '.' else relative
            name = (relative + '/' if relative else '') + '.gitignore'
            if name in policy:
                return
            text = self.engine._read(name, allow_missing_parent=True, max_bytes=MAX_IGNORE_BYTES)[0]
            policy[name] = text
            if len(policy) > MAX_IGNORE_FILES or sum(len((value or '').encode('utf-8')) for value in policy.values()) > MAX_IGNORE_TOTAL_BYTES:
                raise UndoWeaveError('ignore_policy_limit', 'Project ignore files exceeded the observation policy budget.')
            if text is None:
                return
            for line in text.removeprefix('\ufeff').splitlines():
                # Leading spaces are meaningful Git patterns; do not strip them.
                line = line.rstrip(' ')
                if not line or line.startswith(('#', '!')):
                    continue
                if '\\' in line or _unsafe_metadata(line) or len(line) > 1024 or any(part in line for part in ('[[:', '[[.', '[[=')):
                    raise UndoWeaveError('ignore_policy_unsupported', 'An ignore rule needs manual review; recording stopped conservatively.')
                line = line.lstrip('/').rstrip('/')
                if line:
                    rules.append((relative, line))
                if len(rules) > MAX_IGNORE_PATTERNS:
                    raise UndoWeaveError('ignore_policy_limit', 'Project ignore rules exceeded the bounded observation policy.')

        def excluded(path):
            parts = path.split('/')
            # Explicit hidden scopes are allowed, but hidden descendants are
            # conservative exclusions. Credential/client paths never override.
            hidden = any(part.startswith('.') and not any(scope == '/'.join(parts[:i + 1])
                         or scope.startswith('/'.join(parts[:i + 1]) + '/') for scope in self.scopes)
                         for i, part in enumerate(parts))
            return _protected(parts) or hidden or self._ignored(path, rules)

        def visit(path, depth=0):
            nonlocal entries, total_bytes
            budget()
            if path in visited:
                return
            visited.add(path)
            entries += 1
            if entries > MAX_ENTRIES or depth > MAX_DEPTH:
                raise UndoWeaveError('scan_limit', 'The directory-entry/depth observation limit was exceeded.')
            relative = path.relative_to(self.engine.root).as_posix()
            if excluded(relative):
                skipped.add(relative)
                return
            # Validate the entire ancestor chain before scandir or source reads.
            target = self.engine._path(relative, require_parent=False)
            try:
                info = target.lstat()
            except FileNotFoundError:
                return
            if _linked(info):
                skipped.add(relative)
                return
            if path.is_dir():
                load_ignore(path)
                with os.scandir(path) as iterator:
                    for entry in iterator:
                        visit(path / entry.name, depth + 1)
                return
            if not _regular(info):
                skipped.add(relative)
                return
            if info.st_size > MAX_OBSERVATION_BYTES:
                raise UndoWeaveError('observation_limit', 'A selected file exceeds the 256 KiB observation limit.')
            try:
                text = self.engine._read(relative, allow_missing_parent=True, max_bytes=MAX_OBSERVATION_BYTES)[0]
            except UndoWeaveError as error:
                if error.code in {'binary_file', 'content_limit'} and relative not in self._baseline:
                    skipped.add(relative)
                    return
                raise
            if text is None:
                return
            snapshots[relative] = text
            total_bytes += len(text.encode('utf-8'))
            if len(snapshots) > MAX_FILES or total_bytes > MAX_BASELINE_BYTES:
                raise UndoWeaveError('observation_limit', 'The selected text exceeds 256 files or the 16 MiB baseline budget.')

        # Parent policies apply even when the explicit scope is a nested file.
        load_ignore(self.engine.root)
        for scope in self.scopes:
            path = self.engine.root / scope
            ancestors = list(reversed(path.parent.relative_to(self.engine.root).parents))
            blocked_scope = False
            for ancestor in [*ancestors, path.parent.relative_to(self.engine.root)]:
                directory = self.engine.root / ancestor
                if directory != self.engine.root:
                    relative = directory.relative_to(self.engine.root).as_posix()
                    if excluded(relative):
                        skipped.add(relative)
                        blocked_scope = True
                        break
                    self.engine._path(directory.relative_to(self.engine.root).as_posix(), require_parent=False)
                    load_ignore(directory)
            if not blocked_scope:
                visit(path)
        for name, text in policy.items():
            budget()
            if self.engine._read(name, allow_missing_parent=True, max_bytes=MAX_IGNORE_BYTES)[0] != text:
                raise UndoWeaveError('policy_changed', 'Ignore policy changed during the scan; no incomplete census was used.')
        return snapshots, skipped

    def scan_once(self):
        if not self._scan_lock.acquire(blocking=False):
            raise UndoWeaveError('observer_busy', 'An observation scan is already running.')
        try:
            if self._state in {'stopping', 'stopped', 'error'}:
                raise UndoWeaveError('observation_stopped', 'Create a new recorder to start a fresh baseline.')
            self._ensure_session()
            scan_started = time.time()
            head = self.engine._observation_head()
            snapshots, skipped = self._snapshot()
            if self._stop.is_set():
                raise UndoWeaveError('observation_stopped', 'Recording stopped before samples were committed.')
            now = time.time()
            if any(path == skip or path.startswith(skip + '/') for path in self._baseline for skip in skipped):
                raise UndoWeaveError('coverage_gap', 'A previously observed path is now excluded or unsupported; it was not recorded as deleted.')
            if self._state == 'initializing':
                self._baseline = {path: _Sample(text, scan_started, head) for path, text in snapshots.items()}
                self._blocked = skipped
            else:
                for path in sorted(set(self._baseline) | set(snapshots)):
                    text = snapshots.get(path)
                    before = self._baseline.get(path)
                    if before is None and any(path == item or path.startswith(item + '/') for item in self._blocked):
                        self._baseline[path] = _Sample(text, scan_started, head)
                        continue  # First supported observation, not a fabricated creation.
                    if before is None:
                        before = _Sample(None, self._last_scan_start, self._last_head)
                        self._baseline[path] = before
                    if before.text == text:
                        if text is None:
                            self._baseline.pop(path, None)
                        else:
                            self._baseline[path] = _Sample(text, scan_started, head)
                        self._candidates.pop(path, None)
                        continue
                    candidate = self._candidates.get(path)
                    if candidate is None or candidate.text != text:
                        self._candidates[path] = _Sample(text, now, head)
                        continue
                    try:
                        if self._stop.is_set():
                            raise UndoWeaveError('observation_stopped', 'Recording stopped before samples were committed.')
                        result = self.engine.append_observation(self._session['session_id'], path, before.text, text,
                            before.seen, now, recorded_head=before.head)
                    except UndoWeaveError as error:
                        if error.code != 'stale_file':
                            raise
                        self._candidates.pop(path, None)
                        continue  # Start two fresh samples; never commit the obsolete one.
                    if result['status'] == 'observed':
                        self._observed += 1
                    if text is None:
                        self._baseline.pop(path, None)
                    else:
                        self._baseline[path] = _Sample(text, scan_started, result['head'])
                    self._candidates.pop(path, None)
                self._blocked.update(skipped)
                if len(self._blocked) > MAX_ENTRIES:
                    raise UndoWeaveError('observation_limit', 'The cumulative unsupported-path budget was exceeded.')
            self._files, self._skipped = len(snapshots), len(skipped)
            self.engine._observation_progress(self._session['session_id'], files=self._files, skipped=self._skipped)
            self._last_scan, self._last_scan_start, self._last_head, self._state = now, scan_started, head, 'active'
            return self.status()
        except BaseException as error:
            if self._session is not None:
                stopping = self._stop.is_set() and isinstance(error, UndoWeaveError) and error.code == 'observation_stopped'
                self._reason = 'stopped' if stopping else error.code if isinstance(error, UndoWeaveError) else 'scan_failed'
                self._state = 'stopped' if stopping else 'error'
                self._finish()
            raise
        finally:
            self._scan_lock.release()

    def _finish(self):
        try:
            if self._session is not None:
                self.engine.end_observation(self._session['session_id'], self._reason or 'stopped')
        finally:
            lease, self._lease = self._lease, None
            if lease is not None:
                lease.__exit__(None, None, None)
            self._baseline.clear()
            self._candidates.clear()

    def start(self):
        if self._thread is not None:
            raise UndoWeaveError('observer_busy', 'This recorder was already started.')
        self.scan_once()
        self._thread = threading.Thread(target=self._run, name='undoweave-recorder', daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._state, self._reason = 'error', 'start_failed'
            self._finish()
            raise UndoWeaveError('recorder_start_failed', 'Recorder background startup failed; its session and lease were closed.') from None
        return self.status()

    def _run(self):
        try:
            while not self._stop.wait(self.interval):
                self.scan_once()
        except BaseException:
            pass  # Fixed reason/state were persisted by scan_once, no raw source logs.
        finally:
            try:
                self._finish()
            except Exception:
                self._state, self._reason = 'error', self._reason or 'cleanup_failed'
            if self._state != 'error':
                self._state = 'stopped'

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                if self._state != 'error':
                    self._state = 'stopping'
                return self.status()  # Worker retains its lease until it really exits.
        elif self._scan_lock.locked():
            self._state = 'stopping'
            return self.status()
        else:
            self._finish()
        if self._state != 'error':
            self._state = 'stopped'
        return self.status()

    def status(self):
        with self._state_lock:
            return {'state': self._state, 'reason': self._reason, 'scopes': list(self.scopes),
                    'session_id': self._session['session_id'] if self._session else None,
                    'running': bool(self._thread and self._thread.is_alive()), 'interval': self.interval,
                    'files': self._files, 'skipped': self._skipped, 'observed': self._observed,
                    'last_scan': self._last_scan, 'check_evidence': 'not_captured',
                    'limits': {'files': MAX_FILES, 'bytes_per_file': MAX_OBSERVATION_BYTES,
                               'baseline_bytes': MAX_BASELINE_BYTES, 'entries': MAX_ENTRIES},
                    'limitations': LIMITATIONS, 'ignore_policy': IGNORE_POLICY}

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
