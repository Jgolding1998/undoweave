"""Read-only project/scope suggestions. No source snapshots or client writes."""
from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
import re
import stat
import time

from .engine import UndoWeaveError, _check_chain, _linked, _regular, _unsafe_metadata
from .recorder import (MAX_IGNORE_BYTES, MAX_IGNORE_PATTERNS, MAX_SCOPES,
                       Recorder, _protected, validate_scopes)

MAX_PARENTS = 32
MAX_ENTRIES = 512
PLAN_SECONDS = 2
PROJECT_MARKERS = ('pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod',
                   'pom.xml', 'build.gradle', 'CMakeLists.txt', 'requirements.txt',
                   'setup.py', 'index.html', 'Makefile')
SOURCE_DIRS = {'src', 'lib', 'app', 'apps', 'packages', 'tests', 'test',
               'public', 'web', 'frontend', 'backend', 'server', 'client',
               'components', 'scripts', 'pages', 'routes', 'include'}
SOURCE_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.rs',
                   '.go', '.java', '.kt', '.swift', '.cpp', '.c', '.h', '.hpp',
                   '.cs', '.rb', '.php', '.html', '.css', '.scss', '.svelte',
                   '.vue', '.astro', '.sql', '.sh', '.ps1'}
CONTAINERS = {'desktop', 'documents', 'downloads', 'pictures', 'videos',
              'music', 'users', 'home', 'tmp', 'temp'}


def project_server_name(root):
    """Stable project-specific name so setup need not replace another project."""
    root = Path(root)
    slug = re.sub('[^a-z0-9]+', '-', root.name.casefold()).strip('-')[:28] or 'project'
    identity = os.path.normcase(str(root))
    return 'undoweave-' + slug + '-' + sha256(identity.encode('utf-8')).hexdigest()[:8]


def _info(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise UndoWeaveError('setup_unreadable', 'A setup path could not be inspected.') from None


def _broad(path):
    home = Path.home().absolute()
    return (path == Path(path.anchor) or path == home or path in home.parents
            or path.name.casefold() in CONTAINERS)


def _directory(path):
    path = Path(path).absolute()
    try:
        str(path).encode('utf-8')
    except UnicodeError:
        raise UndoWeaveError('unsafe_path', 'Use a project path representable as UTF-8.') from None
    if _unsafe_metadata(str(path)):
        raise UndoWeaveError('unsafe_path', 'The project path contains unsupported control characters.')
    _check_chain(path)
    info = _info(path)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise UndoWeaveError('setup_project_required', 'Choose an existing project directory.')
    path = path.resolve(strict=True)
    if _broad(path):
        raise UndoWeaveError('setup_project_required', 'Choose one project, rather than a home, drive or general files folder.')
    return path


def discover_project(explicit=None, *, cwd=None):
    """Select the nearest project boundary without broadening past a subproject."""
    if explicit is not None:
        return _directory(explicit), 'explicit'
    current = _directory(Path.cwd() if cwd is None else cwd)
    for depth, candidate in enumerate([current, *current.parents]):
        if depth >= MAX_PARENTS or _broad(candidate):
            break
        marker = _info(candidate / '.git')
        if marker is not None:
            if _linked(marker) or not (stat.S_ISDIR(marker.st_mode) or _regular(marker)):
                raise UndoWeaveError('unsafe_path', 'The project marker must not be linked or special.')
            return candidate, 'git_marker'
        for name in PROJECT_MARKERS:
            info = _info(candidate / name)
            if info is not None:
                if _linked(info) or not _regular(info):
                    raise UndoWeaveError('unsafe_path', 'A nearer project marker needs review; setup will not broaden to a parent project.')
                return candidate, 'project_marker'
    raise UndoWeaveError('setup_project_required', 'No project was identified. Run inside a project or provide --root with its absolute path.')


def _ignore_rules(root):
    """Read only a bounded root ignore policy, using recorder matching rules."""
    target = root / '.gitignore'
    initial = _info(target)
    if initial is None:
        return []
    if _linked(initial) or not _regular(initial) or initial.st_size > MAX_IGNORE_BYTES:
        raise UndoWeaveError('ignore_policy_unsupported', 'Review the project ignore policy before choosing recording paths.')
    descriptor = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(descriptor)
        if not _regular(opened) or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise UndoWeaveError('stale_file', 'The ignore policy changed during setup.')
        with os.fdopen(descriptor, 'rb', closefd=False) as handle:
            data = handle.read(MAX_IGNORE_BYTES + 1)
        final, named = os.fstat(descriptor), _info(target)
        if (named is None or _linked(named) or not _regular(named)
                or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
                or (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns)
                != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
            raise UndoWeaveError('stale_file', 'The ignore policy changed during setup.')
    finally:
        os.close(descriptor)
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise UndoWeaveError('ignore_policy_unsupported', 'The ignore policy must be bounded UTF-8 text.') from None
    if len(data) > MAX_IGNORE_BYTES or '\0' in text:
        raise UndoWeaveError('ignore_policy_unsupported', 'The ignore policy exceeds supported setup limits.')
    rules = []
    for line in text.removeprefix('\ufeff').splitlines():
        line = line.rstrip(' ')
        if not line or line.startswith(('#', '!')):
            continue
        if ('\\' in line or _unsafe_metadata(line) or len(line) > 1024
                or any(part in line for part in ('[[:', '[[.', '[[='))):
            raise UndoWeaveError('ignore_policy_unsupported', 'An ignore rule needs manual review before setup.')
        line = line.lstrip('/').rstrip('/')
        if line:
            rules.append(('', line))
        if len(rules) > MAX_IGNORE_PATTERNS:
            raise UndoWeaveError('ignore_policy_limit', 'Project ignore rules exceed setup limits.')
    return rules


def _candidate(root, relative):
    try:
        relative.encode('utf-8')
    except UnicodeError:
        raise UndoWeaveError('invalid_scope', 'Use recording paths representable as UTF-8.') from None
    validate_scopes([relative])
    target = root / relative
    _check_chain(target)
    info = _info(target)
    if info is None or _linked(info) or not (stat.S_ISDIR(info.st_mode) or _regular(info)):
        raise UndoWeaveError('setup_scope_required', 'Selected recording paths must exist and be regular files or directories.')
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise UndoWeaveError('unsafe_path', 'A recording path resolves outside the selected project.')
    canonical = resolved.relative_to(root).as_posix()
    validate_scopes([canonical])
    return canonical


def plan_setup(explicit=None, scopes=None, *, cwd=None):
    """Suggest bounded literal scopes without initializing an Engine/Recorder."""
    root, detection = discover_project(explicit, cwd=cwd)
    rules = _ignore_rules(root)
    provided = list(scopes or [])
    selected, skipped = [], 0
    if provided:
        for name in validate_scopes(provided):
            name = _candidate(root, name)
            if Recorder._ignored(name, rules):
                raise UndoWeaveError('setup_scope_ignored', 'A selected recording path is excluded by the root ignore policy.')
            if name not in selected:
                selected.append(name)
    else:
        deadline = time.monotonic() + PLAN_SECONDS
        with os.scandir(root) as entries:
            names = []
            for entry in entries:
                if len(names) >= MAX_ENTRIES or time.monotonic() > deadline:
                    raise UndoWeaveError('setup_limit', 'This project needs explicit --watch paths; setup does not scan large directories.')
                names.append(entry.name)
        for name in sorted(names, key=lambda value: (value.casefold(), value)):
            if time.monotonic() > deadline:
                raise UndoWeaveError('setup_limit', 'Setup exceeded its inspection budget; choose explicit --watch paths.')
            target = root / name
            info = _info(target)
            if (info is None or name.startswith('.') or _protected([name])
                    or _linked(info) or Recorder._ignored(name, rules)):
                skipped += 1
                continue
            package = _info(target / '__init__.py') if stat.S_ISDIR(info.st_mode) else None
            python_package = package is not None and not _linked(package) and _regular(package)
            wanted = ((stat.S_ISDIR(info.st_mode) and (name.casefold() in SOURCE_DIRS or python_package))
                      or (_regular(info) and Path(name).suffix.casefold() in SOURCE_SUFFIXES))
            if not wanted:
                skipped += 1
                continue
            try:
                selected.append(_candidate(root, name))
            except UndoWeaveError as error:
                if error.code in {'unsafe_path', 'invalid_scope', 'protected_path', 'setup_scope_required'}:
                    skipped += 1
                    continue
                raise
        if len(selected) > MAX_SCOPES:
            raise UndoWeaveError('setup_scope_required', 'More than 16 code paths were found. Choose the paths you want with --watch.')
    if not selected:
        raise UndoWeaveError('setup_scope_required', 'No suitable code paths were found. Select project files or folders with --watch PATH.')
    marker = '.git' if detection == 'git_marker' else None
    if detection == 'project_marker':
        marker = next((name for name in PROJECT_MARKERS
                       if (info := _info(root / name)) is not None and not _linked(info) and _regular(info)), None)
    return {'root': str(root), 'root_detection': detection, 'root_marker': marker, 'scopes': selected,
            'selection': 'explicit' if provided else 'suggested', 'skipped_top_level_entries': skipped,
            'recording_started': False, 'configuration_changed': False,
            'source_content_read': False, 'ignore_policy_read': bool(_info(root / '.gitignore')),
            'tool_access': 'Supported file tools use the pinned project root; recording paths do not restrict tool access.',
            'scope_note': 'Suggested paths are not a completed capture scan. Nested ignore rules, text support and recorder limits still apply.',
            'privacy_note': 'Recording retains plaintext source privately. Review these paths before enabling the printed configuration.'}
