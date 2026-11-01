"""Setup discovers synthetic projects without opening source or starting capture."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from undoweave import cli
from undoweave.engine import UndoWeaveError
from undoweave.onboarding import discover_project, plan_setup, project_server_name


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='undoweave-setup-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'sample project'
        self.root.mkdir()
        (self.root / '.git').mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'tests').mkdir()
        (self.root / 'src/app.py').write_text('PRIVATE_SOURCE_CANARY = 42\n')

    def call(self, *arguments, cwd=None):
        output, errors = io.StringIO(), io.StringIO()
        old = Path.cwd()
        try:
            if cwd is not None:
                os.chdir(cwd)
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = cli.main(list(arguments))
        finally:
            os.chdir(old)
        return code, output.getvalue(), errors.getvalue()

    def test_nested_project_plan_reads_no_source_and_creates_no_state(self):
        (self.root / 'package.json').write_text('not executable, not even valid JSON')
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('*'))
        with patch.object(Path, 'open', side_effect=AssertionError('source must not be opened')):
            plan = plan_setup(cwd=self.root / 'src')
        self.assertEqual(plan['root'], str(self.root))
        self.assertEqual(plan['scopes'], ['src', 'tests'])
        self.assertEqual(plan['root_detection'], 'git_marker')
        self.assertFalse(plan['source_content_read'])
        self.assertFalse(plan['recording_started'])
        self.assertEqual(before, sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('*')))
        self.assertFalse((self.root / '.undoweave-id').exists())

    def test_nested_package_does_not_expand_to_the_whole_monorepo(self):
        nested = self.root / 'packages' / 'small-app'
        nested.mkdir(parents=True)
        (nested / 'package.json').write_text('untrusted manifest body is not read')
        (nested / 'main.js').write_text('console.log(1)')
        plan = plan_setup(cwd=nested)
        self.assertEqual(plan['root'], str(nested))
        self.assertEqual(plan['root_marker'], 'package.json')
        self.assertEqual(plan['scopes'], ['main.js'])

    def test_unmarked_project_requires_explicit_root_or_a_project_marker(self):
        root = self.base / 'plain'
        root.mkdir()
        (root / 'main.py').write_text('private body')
        with self.assertRaises(UndoWeaveError) as result:
            discover_project(cwd=root)
        self.assertEqual(result.exception.code, 'setup_project_required')
        self.assertEqual(plan_setup(root)['scopes'], ['main.py'])
        (root / 'pyproject.toml').write_text('untrusted manifest body is not parsed')
        with patch.object(Path, 'open', side_effect=AssertionError('manifest must not be read')):
            selected, basis = discover_project(cwd=root)
        self.assertEqual((selected, basis), (root, 'project_marker'))

    def test_worktree_marker_does_not_follow_or_read_its_gitdir(self):
        root = self.base / 'worktree'
        root.mkdir()
        (root / '.git').write_text('gitdir: /a/private/path/that/must/not/be/read')
        with patch.object(Path, 'open', side_effect=AssertionError('gitdir must not be read')):
            self.assertEqual(discover_project(cwd=root), (root, 'git_marker'))

    def test_unsupported_nearer_project_marker_does_not_broaden_to_parent_git(self):
        target = self.base / 'untrusted-marker'
        target.write_text('this body is not read')
        for kind in ('directory', 'hardlink'):
            nested = self.root / 'src' / ('package-' + kind)
            nested.mkdir()
            marker = nested / 'package.json'
            if kind == 'directory':
                marker.mkdir()
            else:
                try:
                    os.link(target, marker)
                except OSError:
                    self.skipTest('hardlinks unavailable')
            with self.subTest(kind=kind):
                with self.assertRaises(UndoWeaveError) as result:
                    discover_project(cwd=nested)
                self.assertEqual(result.exception.code, 'unsafe_path')

    def test_home_drive_and_general_files_folders_are_not_projects(self):
        for target in (Path.home(), Path(self.root.anchor), self.base / 'Documents'):
            if target == self.base / 'Documents':
                target.mkdir()
                (target / '.git').mkdir()
            with self.subTest(target=target), self.assertRaises(UndoWeaveError) as result:
                discover_project(target)
            self.assertEqual(result.exception.code, 'setup_project_required')

    def test_auto_scopes_exclude_credentials_artifacts_data_and_ignored_files(self):
        for name in ('node_modules', 'dist', 'data', 'uploads', '.claude'):
            (self.root / name).mkdir()
        for name in ('.env', 'secret.py', 'local.py', 'main.py', 'notes.txt'):
            (self.root / name).write_text('this body is not a setup input')
        (self.root / '.gitignore').write_text('local.py\n/tests/\n')
        plan = plan_setup(self.root)
        self.assertEqual(plan['scopes'], ['main.py', 'src'])
        self.assertTrue(plan['ignore_policy_read'])
        self.assertFalse(plan['source_content_read'])

    def test_named_python_package_is_suggested_without_importing_or_reading_it(self):
        package = self.root / 'my_package'
        package.mkdir()
        (package / '__init__.py').write_text('raise RuntimeError("never execute this package")')
        with patch.object(Path, 'open', side_effect=AssertionError('source must not be opened')):
            plan = plan_setup(self.root)
        self.assertEqual(plan['scopes'], ['my_package', 'src', 'tests'])

    def test_conservative_ignore_case_and_zero_directory_globstar_are_shared(self):
        (self.root / 'LOCAL.py').write_text('private')
        (self.root / '.gitignore').write_text('**/local.py\n')
        self.assertNotIn('LOCAL.py', plan_setup(self.root)['scopes'])
        with self.assertRaises(UndoWeaveError) as result:
            plan_setup(self.root, ['LOCAL.py'])
        self.assertEqual(result.exception.code, 'setup_scope_ignored')

    def test_invalid_ignore_policy_does_not_fall_back_to_broader_recording(self):
        for contents in (b'private\\ file.py\n', b'\xff', b'a' * 16385, b'\0'):
            (self.root / '.gitignore').write_bytes(contents)
            with self.subTest(contents=contents[:20]), self.assertRaises(UndoWeaveError):
                plan_setup(self.root)
        self.assertFalse((self.root / '.undoweave-id').exists())

    def test_no_code_paths_is_a_selection_error_not_whole_root(self):
        (self.root / 'src/app.py').unlink()
        (self.root / 'src').rmdir()
        (self.root / 'tests').rmdir()
        (self.root / 'README.md').write_text('planning-only repository')
        with self.assertRaises(UndoWeaveError) as result:
            plan_setup(self.root)
        self.assertEqual(result.exception.code, 'setup_scope_required')

    def test_scope_overflow_does_not_silently_drop_code(self):
        for index in range(17):
            (self.root / f'app{index}.py').write_text('pass\n')
        with self.assertRaises(UndoWeaveError) as result:
            plan_setup(self.root)
        self.assertEqual(result.exception.code, 'setup_scope_required')
        self.assertEqual(plan_setup(self.root, ['app0.py'])['scopes'], ['app0.py'])

    def test_directory_census_is_bounded_but_explicit_scope_remains_usable(self):
        for index in range(513):
            (self.root / f'data{index}.txt').touch()
        with self.assertRaises(UndoWeaveError) as result:
            plan_setup(self.root)
        self.assertEqual(result.exception.code, 'setup_limit')
        self.assertEqual(plan_setup(self.root, ['src'])['scopes'], ['src'])

    def test_hardlinked_sources_and_ignore_policy_are_refused(self):
        original = self.root / 'src/app.py'
        linked = self.root / 'copied.py'
        try:
            os.link(original, linked)
        except OSError:
            self.skipTest('hardlinks unavailable')
        self.assertNotIn('copied.py', plan_setup(self.root)['scopes'])
        with self.assertRaises(UndoWeaveError):
            plan_setup(self.root, ['copied.py'])
        os.link(original, self.root / '.gitignore')
        with self.assertRaises(UndoWeaveError):
            plan_setup(self.root)

    def test_linked_scope_and_linked_root_are_refused(self):
        linked = self.root / 'app'
        try:
            linked.symlink_to(self.root / 'src', target_is_directory=True)
        except OSError:
            self.skipTest('symlink privilege unavailable')
        self.assertNotIn('app', plan_setup(self.root)['scopes'])
        with self.assertRaises(UndoWeaveError):
            plan_setup(self.root, ['app'])
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(UndoWeaveError):
            discover_project(alias)

    def test_generated_configuration_stays_literal_and_pins_each_project(self):
        state = self.base / 'unused state'
        code, output, errors = self.call('--state-dir', str(state), 'setup', cwd=self.root / 'src')
        self.assertEqual(code, 0, errors)
        plan = json.loads(output)
        self.assertEqual(plan['root'], str(self.root))
        name, entry = next(iter(plan['mcpServers'].items()))
        self.assertEqual(name, project_server_name(self.root))
        parsed = cli.build_parser().parse_args(entry['args'][3:])
        self.assertEqual(parsed.root, self.root)
        self.assertEqual(parsed.watch, ['src', 'tests'])
        self.assertEqual(entry['args'][:3], ['-I', '-m', 'undoweave'])
        self.assertNotIn('PRIVATE_SOURCE_CANARY', output)
        self.assertFalse(state.exists())
        self.assertFalse((self.root / '.undoweave-id').exists())

    def test_leading_dash_source_path_roundtrips_through_server_parser(self):
        (self.root / '-sample.py').write_text('pass\n')
        code, output, errors = self.call('--root', str(self.root), 'setup', '--watch=-sample.py')
        self.assertEqual(code, 0, errors)
        entry = next(iter(json.loads(output)['mcpServers'].values()))
        parsed = cli.build_parser().parse_args(entry['args'][3:])
        self.assertEqual(parsed.watch, ['-sample.py'])

    def test_named_config_and_both_human_clients_preserve_scope_and_state(self):
        for client in ('claude', 'codex'):
            code, output, errors = self.call('--root', str(self.root), 'setup', '--client', client, '--name', 'my-project')
            self.assertEqual(code, 0, errors)
            self.assertIn('my-project', output)
            self.assertIn(str(self.root), output)
            self.assertIn('Recording paths: src, tests', output)
            self.assertFalse((self.root / '.undoweave-id').exists())
        code, output, errors = self.call('--root', str(self.root), 'config', '--name', 'my-project')
        self.assertEqual(code, 0, errors)
        self.assertEqual(list(json.loads(output)['mcpServers']), ['my-project'])

    def test_printed_project_switch_uses_the_literal_directory(self):
        root = self.base / "project ' ; & $(Set-Content PWN 1)"
        root.mkdir()
        (root / 'app.py').write_text('pass\n')
        code, output, errors = self.call('--root', str(root), 'setup', '--client', 'claude')
        self.assertEqual(code, 0, errors)
        if os.name == 'nt':
            line = next(line for line in output.splitlines() if line.startswith('Set-Location '))
            programs = [path for name in ('powershell', 'pwsh') if (path := shutil.which(name))]
            if not programs:
                self.skipTest('PowerShell unavailable')
            for program in programs:
                result = subprocess.run([program, '-NoProfile', '-NonInteractive', '-Command',
                    line + '\nConvertTo-Json -Compress -InputObject (Get-Location).Path'],
                    cwd=self.base, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(json.loads(result.stdout)), root)
        else:
            program = shutil.which('sh')
            if not program:
                self.skipTest('POSIX shell unavailable')
            line = next(line for line in output.splitlines() if line.startswith('cd '))
            result = subprocess.run([program, '-c', line + '\npwd -P'], cwd=self.base,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), root)
        self.assertFalse((self.base / 'PWN').exists())
        self.assertFalse((root / '.undoweave-id').exists())

    def test_invalid_selection_name_interval_and_state_refuse_without_effects(self):
        cases = [('setup', '--name', 'bad.name'), ('setup', '--watch', '../outside'),
                 ('setup', '--watch', 'missing'), ('setup', '--watch', '.env'),
                 ('setup', '--interval', 'nan'), ('setup', '--interval', '0')]
        for arguments in cases:
            code, output, errors = self.call('--root', str(self.root), *arguments)
            self.assertNotEqual(code, 0)
            self.assertEqual(output, '')
            self.assertNotIn('Traceback', errors)
        code, output, errors = self.call('--root', str(self.root), '--state-dir', str(self.root / 'state'), 'setup')
        self.assertNotEqual(code, 0)
        self.assertEqual(output, '')
        self.assertFalse((self.root / 'state').exists())

    def test_project_names_are_stable_and_do_not_clobber_same_named_projects(self):
        first, second = self.base / 'team-a/app', self.base / 'team-b/app'
        self.assertEqual(project_server_name(first), project_server_name(first))
        self.assertNotEqual(project_server_name(first), project_server_name(second))
        self.assertLessEqual(len(project_server_name(self.base / ('p' * 100))), 64)

    def test_codex_setup_targets_project_configuration_and_preserves_unicode(self):
        root = self.base / 'project \U0001f600'
        root.mkdir()
        (root / 'main.py').write_text('pass\n')
        code, output, errors = self.call('--root', str(root), 'setup', '--client', 'codex')
        self.assertEqual(code, 0, errors)
        self.assertIn(str(root / '.codex/config.toml'), output)
        self.assertNotIn('codex mcp add', output)
        self.assertNotIn('~/.codex/config.toml', output)
        lines = output.splitlines()
        first = next(index for index, line in enumerate(lines) if line.startswith('[mcp_servers.'))
        table = tomllib.loads('\n'.join(lines[first:first + 3]))
        entry = next(iter(table['mcp_servers'].values()))
        self.assertEqual(cli.build_parser().parse_args(entry['args'][3:]).root, root)
        self.assertFalse((root / '.codex').exists())

    def test_toml_non_bmp_and_literal_escape_sequences_roundtrip(self):
        value = 'C:\\literal\\ud83d\\ude00\\project \U0001f600 "quoted"\n'
        self.assertEqual(tomllib.loads('value = ' + cli._toml_str(value))['value'], value)

    def test_legacy_human_encoding_refuses_without_partial_configuration(self):
        root = self.base / 'project \U0001f600'
        root.mkdir()
        (root / 'main.py').write_text('pass\n')
        class AsciiOutput(io.StringIO):
            encoding = 'ascii'
        output, errors = AsciiOutput(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(['--root', str(root), 'setup', '--client', 'codex'])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(json.loads(errors.getvalue())['error']['code'], 'unsupported_output_encoding')
        code, data, errors = self.call('--root', str(root), 'setup')
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(data)['root'], str(root))


if __name__ == '__main__':
    unittest.main()
