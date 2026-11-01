"""All watcher inputs are created in disposable synthetic projects."""
from pathlib import Path
import os
import tempfile
import subprocess
import sys
import threading
import time
import shutil
import unittest
from unittest.mock import patch

from undoweave.engine import Engine, UndoWeaveError
from undoweave.recorder import Recorder, validate_scopes
from undoweave import recorder as module
from undoweave import engine as core


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='undoweave-observer-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        (self.root / 'src').mkdir()
        self.target = self.root / 'src' / 'app.cfg'
        self.target.write_bytes(b'alpha = 1\n')
        self.engine = Engine(self.root, self.base / 'state')
        self.recorder = Recorder(self.engine, ['src'])
        self.addCleanup(self.recorder.stop)

    def change(self, text):
        self.target.write_bytes(text.encode())
        self.recorder.scan_once()
        self.recorder.scan_once()
        return self.engine.history()[0]

    def test_initial_baseline_is_not_an_edit_and_second_stable_sample_records(self):
        self.recorder.scan_once()
        self.assertEqual(self.engine.history(), [])
        self.target.write_bytes(b'alpha = 2\n')
        self.recorder.scan_once()
        self.assertEqual(self.engine.history(), [])
        self.recorder.scan_once()
        row = self.engine.history()[0]
        self.assertEqual((row['status'], row['actor'], row['source'], row['attribution']),
                         ('observed', 'unknown', 'filesystem_poll', 'unknown'))
        evidence = self.engine.inspect_change(row['id'])
        self.assertIn('-alpha = 1', evidence['diff'])
        self.assertIn('+alpha = 2', evidence['diff'])
        self.assertTrue(evidence['diff'].startswith('--- before/src/app.cfg\n+++ after/src/app.cfg\n'))
        self.assertEqual(evidence['check_evidence'], 'not_captured')
        self.assertFalse(evidence['undo_supported'])
        self.assertNotIn('token', evidence)
        with self.assertRaises(UndoWeaveError) as caught:
            self.engine.preview_undo(row['id'])
        self.assertEqual(caught.exception.code, 'observation_not_undoable')
        self.assertEqual(self.engine.status()['pending_count'], 0)
        self.assertEqual(self.engine.status()['recorded_count'], 0)

    def test_quick_save_and_revert_is_not_invented_history(self):
        self.recorder.scan_once()
        self.target.write_bytes(b'transient\n')
        self.recorder.scan_once()
        self.target.write_bytes(b'alpha = 1\n')
        self.recorder.scan_once()
        self.assertEqual(self.engine.history(), [])
        self.assertIn('miss intermediate', self.recorder.status()['limitations'])

    def test_new_file_deletion_and_deleted_parent_are_distinguished(self):
        self.recorder.scan_once()
        created = self.root / 'src' / 'new.txt'
        created.write_bytes(b'created\n')
        self.recorder.scan_once(); self.recorder.scan_once()
        self.assertEqual(self.engine.history()[0]['before_hash'], 'missing')
        created.unlink()
        self.target.unlink()
        self.target.parent.rmdir()
        self.recorder.scan_once(); self.recorder.scan_once()
        rows = self.engine.history()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r['after_hash'] for r in rows[:2]], ['missing', 'missing'])

    def test_restart_is_a_fresh_baseline_not_offline_history(self):
        self.recorder.scan_once()
        row = self.change('alpha = 2\n')
        self.recorder.stop()
        self.target.write_bytes(b'changed while off\n')
        engine = Engine(self.root, self.base / 'state')
        with Recorder(engine, ['src']) as restarted:
            self.assertEqual(len(engine.history()), 1)
            self.assertEqual(engine.history()[0]['id'], row['id'])
            self.assertEqual(restarted.status()['state'], 'active')
        self.assertEqual(engine.observation_status()['state'], 'stopped')

    def test_known_mcp_writes_and_creation_are_not_duplicated(self):
        self.recorder.scan_once()
        old = self.engine.read('src/app.cfg')
        self.engine.edit('src/app.cfg', 'recorded\n', 'claude', old['sha256'])
        self.engine.edit('src/created.txt', 'recorded create\n', 'codex', 'missing')
        self.recorder.scan_once(); self.recorder.scan_once()
        self.assertEqual([r['status'] for r in self.engine.history()], ['applied', 'applied'])

    def test_mixed_recorded_and_outside_window_keeps_unknown_attribution(self):
        self.recorder.scan_once()
        known = self.engine.edit('src/app.cfg', 'recorded\n', 'claude', self.engine.read('src/app.cfg')['sha256'])
        row = self.change('outside after recorded\n')
        self.assertEqual(row['status'], 'observed')
        self.assertEqual(row['observation']['related_change_ids'], [known['id']])
        self.assertEqual(row['actor'], 'unknown')

    def test_scope_privacy_and_conservative_ignore_are_not_overridden(self):
        (self.root / '.gitignore').write_text('src/local*.cfg\n!src/local-keep.cfg\n', encoding='utf-8')
        (self.root / 'src' / 'local-keep.cfg').write_bytes(b'private canary\n')
        (self.root / 'src' / '.env').write_bytes(b'private canary\n')
        (self.root / 'src' / 'tokens.json').write_bytes(b'private canary\n')
        (self.root / 'other.txt').write_bytes(b'outside scope\n')
        self.recorder.scan_once()
        self.assertEqual(self.recorder.status()['files'], 1)
        self.assertEqual(self.recorder.status()['skipped'], 3)
        self.change('visible\n')
        data = (self.base / 'state' / 'journal.sqlite3').read_bytes()
        self.assertNotIn(b'private canary', data)
        self.assertNotIn(b'outside scope', data)
        self.assertIn('Not full Git ignore semantics', self.recorder.status()['ignore_policy'])

    def test_new_exclusion_is_a_durable_gap_not_a_deletion(self):
        self.recorder.scan_once()
        (self.root / '.gitignore').write_text('src/app.cfg\n', encoding='utf-8')
        with self.assertRaises(UndoWeaveError) as caught:
            self.recorder.scan_once()
        self.assertEqual(caught.exception.code, 'coverage_gap')
        self.assertEqual(self.engine.history(), [])
        self.assertEqual(self.engine.observation_status()['state'], 'error')
        self.assertEqual(self.engine.observation_status()['reason'], 'coverage_gap')

    def test_unreadable_and_incomplete_scans_never_record_deletions(self):
        self.recorder.scan_once()
        original = self.engine._read
        def unreadable(path, **kwargs):
            if path == 'src/app.cfg': raise PermissionError('synthetic private marker')
            return original(path, **kwargs)
        with patch.object(self.engine, '_read', side_effect=unreadable):
            with self.assertRaises(PermissionError): self.recorder.scan_once()
        self.assertEqual(self.engine.history(), [])
        self.assertNotIn('private marker', str(self.recorder.status()))
        self.assertEqual(self.engine.observation_status()['reason'], 'scan_failed')

    def test_file_and_entry_limits_stop_visibly_before_partial_baseline(self):
        with patch.object(module, 'MAX_FILES', 0):
            with self.assertRaises(UndoWeaveError): self.recorder.scan_once()
        self.assertEqual(self.recorder.status()['state'], 'error')
        self.assertEqual(self.engine.history(), [])
        other = Recorder(self.engine, ['src'])
        self.addCleanup(other.stop)
        with patch.object(module, 'MAX_ENTRIES', 1):
            with self.assertRaises(UndoWeaveError): other.scan_once()
        self.assertEqual(other.status()['state'], 'error')

    def test_one_recorder_lease_per_journal_and_stop_releases_it(self):
        self.recorder.scan_once()
        other = Recorder(Engine(self.root, self.base / 'state'), ['src'])
        self.addCleanup(other.stop)
        with self.assertRaises(UndoWeaveError) as caught: other.scan_once()
        self.assertEqual(caught.exception.code, 'observer_busy')
        self.recorder.stop()
        self.assertEqual(other.scan_once()['state'], 'active')

    def test_journal_order_survives_clock_rollback(self):
        self.recorder.scan_once()
        with patch.object(module.time, 'time', return_value=1000):
            first = self.change('first\n')
        with patch.object(module.time, 'time', return_value=900):
            second = self.change('second\n')
        self.assertEqual([r['id'] for r in self.engine.history()], [second['id'], first['id']])
        self.assertLess(second['created'], first['created'])

    def test_storage_limit_does_not_create_pending_or_partial_observation(self):
        self.recorder.scan_once()
        self.target.write_bytes(b'changed\n')
        self.recorder.scan_once()
        with patch.object(self.engine, '_used_storage', return_value=128 * 1024 * 1024):
            with self.assertRaises(UndoWeaveError) as caught: self.recorder.scan_once()
        self.assertEqual(caught.exception.code, 'storage_limit')
        self.assertEqual(self.engine.history(), [])
        self.assertEqual(self.engine.status()['pending_count'], 0)

    def test_exact_file_scope_and_format_only_validation(self):
        self.recorder.stop()
        single = Recorder(self.engine, ['src/app.cfg'])
        self.addCleanup(single.stop)
        self.assertEqual(single.scan_once()['files'], 1)
        for value in ('.', '../x', '.env', 'node_modules', '.codex/config.toml', 'src/tokens.json'):
            with self.subTest(value=value), self.assertRaises(UndoWeaveError): validate_scopes([value])
        self.assertEqual(validate_scopes(['app.cfg']), ('app.cfg',))

    @unittest.skipIf(os.name == 'nt', 'Windows CRT read handles deny delete-sharing; pathname-race simulation covers the check here')
    def test_atomic_save_during_read_rejects_the_superseded_descriptor(self):
        replacement = self.root / 'replacement.tmp'
        replacement.write_bytes(b'new atomic bytes\n')
        inode = self.target.stat().st_ino
        real_fstat = core.os.fstat
        samples = []
        def replace_on_final_stat(fd):
            info = real_fstat(fd)
            if info.st_ino == inode:
                samples.append(info)
                if len(samples) == 2:
                    os.replace(replacement, self.target)
            return info
        with patch.object(core.os, 'fstat', side_effect=replace_on_final_stat):
            with self.assertRaises(UndoWeaveError) as caught:
                self.engine.read('src/app.cfg')
        self.assertEqual(caught.exception.code, 'stale_file')
        self.assertEqual(self.target.read_bytes(), b'new atomic bytes\n')
        self.assertEqual(self.engine.history(), [])

    def test_final_named_identity_is_checked_in_addition_to_descriptor(self):
        replacement = self.root / 'replacement.tmp'
        replacement.write_bytes(b'new atomic bytes\n')
        original = self.engine._path
        calls = []
        def named_path(path, **kwargs):
            target = original(path, **kwargs)
            calls.append(path)
            if len(calls) == 3:
                return replacement  # Simulate the pathname now naming the replacement inode.
            return target
        with patch.object(self.engine, '_path', side_effect=named_path):
            with self.assertRaises(UndoWeaveError) as caught: self.engine.read('src/app.cfg')
        self.assertEqual(caught.exception.code, 'stale_file')
        self.assertEqual(self.target.read_bytes(), b'alpha = 1\n')

    def test_commit_race_discards_candidate_until_two_fresh_samples(self):
        self.recorder.scan_once()
        self.target.write_bytes(b'candidate\n')
        self.recorder.scan_once()
        original = self.engine.append_observation
        def racing(*args, **kwargs):
            self.target.write_bytes(b'later\n')
            return original(*args, **kwargs)
        with patch.object(self.engine, 'append_observation', side_effect=racing):
            self.recorder.scan_once()
        self.assertEqual(self.engine.history(), [])
        self.assertEqual(self.recorder.status()['state'], 'active')
        self.recorder.scan_once(); self.recorder.scan_once()
        self.assertIn('+later', self.engine.inspect_change(self.engine.history()[0]['id'])['diff'])

    def test_inspection_truncates_with_disclosure_and_escapes_controls(self):
        self.recorder.scan_once()
        row = self.change('x' * 20000 + '\u202e\n')
        evidence = self.engine.inspect_change(row['id'])
        self.assertTrue(evidence['diff_truncated'])
        self.assertEqual(len(evidence['diff']), 16000)
        self.assertNotIn('\u202e', evidence['diff'])
        self.assertTrue(evidence['display_escaped'])
        self.assertNotIn('token', evidence)

    def test_unknown_ignore_escape_stops_before_reading_source(self):
        (self.root / '.gitignore').write_text('src/private\\ file.cfg\n', encoding='utf-8')
        original = self.engine._read
        seen = []
        def checked(path, **kwargs):
            seen.append(path)
            return original(path, **kwargs)
        with patch.object(self.engine, '_read', side_effect=checked):
            with self.assertRaises(UndoWeaveError) as caught: self.recorder.scan_once()
        self.assertEqual(caught.exception.code, 'ignore_policy_unsupported')
        self.assertEqual(seen, ['.gitignore'])

    def test_restart_after_real_process_exit_marks_unclean_session(self):
        script = ('import os,sys; from pathlib import Path; from undoweave.engine import Engine; '
                  'from undoweave.recorder import Recorder; '
                  'r=Recorder(Engine(Path(sys.argv[1]),Path(sys.argv[2])),["src"]); '
                  'r.scan_once(); os._exit(0)')
        process = subprocess.run([sys.executable, '-I', '-c', script, str(self.root), str(self.base / 'state')],
                                 capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', 'replace'))
        self.assertEqual(self.engine.observation_status()['state'], 'active')
        self.assertEqual(self.engine.observation_status()['liveness'], 'not_checked')
        self.recorder.scan_once()
        with self.engine._connection() as db:
            states = [row[0] for row in db.execute('SELECT state FROM observation_sessions ORDER BY rowid')]
        self.assertEqual(states, ['interrupted', 'active'])

    def test_bounded_stop_keeps_lease_until_blocked_worker_really_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original = self.recorder._snapshot
        calls = []
        def slow_snapshot():
            calls.append(1)
            if len(calls) > 1:
                entered.set()
                release.wait(10)
            return original()
        self.addCleanup(release.set)
        with patch.object(self.recorder, '_snapshot', side_effect=slow_snapshot):
            self.recorder.interval = .25
            self.recorder.start()
            self.assertTrue(entered.wait(3))
            start = time.monotonic()
            self.assertEqual(self.recorder.stop()['state'], 'stopping')
            self.assertLess(time.monotonic() - start, 3)
            other = Recorder(self.engine, ['src'])
            self.addCleanup(other.stop)
            with self.assertRaises(UndoWeaveError): other.scan_once()
            release.set()
            self.recorder._thread.join(5)
            self.assertFalse(self.recorder._thread.is_alive())
            self.assertEqual(other.scan_once()['state'], 'active')

    def test_source_bytes_aggregate_and_ignore_policy_budgets_are_explicit(self):
        with patch.object(module, 'MAX_BASELINE_BYTES', 1):
            with self.assertRaises(UndoWeaveError) as caught: self.recorder.scan_once()
        self.assertEqual(caught.exception.code, 'observation_limit')
        other = Recorder(self.engine, ['src'])
        self.addCleanup(other.stop)
        (self.root / '.gitignore').write_bytes(b'#' + b'x' * 17000)
        with self.assertRaises(UndoWeaveError): other.scan_once()
        self.assertEqual(other.status()['state'], 'error')
        self.assertEqual(self.engine.history(), [])

    def test_tracked_file_becoming_binary_is_a_gap_and_not_a_deletion(self):
        self.recorder.scan_once()
        self.target.write_bytes(b'\0binary')
        with self.assertRaises(UndoWeaveError): self.recorder.scan_once()
        self.assertEqual(self.recorder.status()['state'], 'error')
        self.assertEqual(self.engine.history(), [])

    def test_hardlink_is_never_read_or_misclassified_as_a_deletion(self):
        self.recorder.scan_once()
        os.link(self.target, self.root / 'outside-link')
        with self.assertRaises(UndoWeaveError) as caught: self.recorder.scan_once()
        self.assertEqual(caught.exception.code, 'coverage_gap')
        self.assertEqual(self.engine.history(), [])

    def test_overlapping_scopes_do_not_double_count_or_duplicate(self):
        self.recorder.stop()
        other = Recorder(self.engine, ['src', 'src/app.cfg'])
        self.addCleanup(other.stop)
        self.assertEqual(other.scan_once()['files'], 1)
        self.target.write_bytes(b'one outside change\n')
        other.scan_once(); other.scan_once()
        self.assertEqual(len(self.engine.history()), 1)

    def test_deleted_paths_are_not_retained_in_an_unbounded_baseline(self):
        self.recorder.scan_once()
        self.target.unlink()
        self.recorder.scan_once(); self.recorder.scan_once()
        self.assertEqual(self.recorder._baseline, {})
        self.target.write_bytes(b'recreated\n')
        self.recorder.scan_once(); self.recorder.scan_once()
        self.assertEqual(len(self.engine.history()), 2)
        self.assertEqual(self.engine.history()[0]['before_hash'], 'missing')

    def test_path_key_collision_cannot_suppress_another_paths_observation(self):
        other_path = self.root / 'different.cfg'
        other_path.write_bytes(b'alpha = 1\n')
        self.recorder.scan_once()
        with patch.object(self.engine, '_path_key', return_value='deliberate-alias-collision'):
            known = self.engine.edit('different.cfg', 'same new bytes\n', 'claude', self.engine.read('different.cfg')['sha256'])
            row = self.change('same new bytes\n')
        self.assertEqual(row['status'], 'observed')
        self.assertNotIn(known['id'], row['observation']['related_change_ids'])

    @unittest.skipUnless(os.name == 'nt', 'Windows byte-range locks')
    def test_empty_lock_owned_before_first_write_returns_controlled_busy(self):
        import msvcrt
        for name in ('session.lock', 'observer.lock'):
            with self.subTest(name=name):
                target = self.engine.state_dir / name
                target.write_bytes(b'')
                fd = os.open(target, os.O_RDWR)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    with self.assertRaises(UndoWeaveError) as caught:
                        with self.engine._file_lock(name, 0): pass
                    self.assertEqual(caught.exception.code, 'workspace_busy')
                    self.assertEqual(target.stat().st_size, 0)
                finally:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    os.close(fd)
                with self.engine._file_lock(name, 0):
                    self.assertEqual(target.stat().st_size, 0)

    def test_pending_and_aborted_inspection_are_labeled_unverified_intents(self):
        change = self.engine.edit('src/app.cfg', 'after\n', 'claude', self.engine.read('src/app.cfg')['sha256'])
        self.assertTrue(self.engine.inspect_change(change['id'])['effect_verified'])
        for status in ('pending', 'aborted'):
            with self.engine._connection() as db:
                db.execute('UPDATE changes SET status=? WHERE id=?', (status, change['id']))
                db.commit()
            evidence = self.engine.inspect_change(change['id'])
            self.assertFalse(evidence['effect_verified'])
            self.assertFalse(evidence['undo_supported'])
            self.assertIn('intent', evidence['evidence_note'])
            self.assertEqual(evidence['status'], status)

    def test_thread_start_failure_closes_session_and_releases_lease(self):
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('synthetic startup failure')):
            with self.assertRaises(UndoWeaveError) as caught: self.recorder.start()
        self.assertEqual(caught.exception.code, 'recorder_start_failed')
        self.assertEqual(self.engine.observation_status()['reason'], 'start_failed')
        self.assertEqual(self.engine.observation_status()['state'], 'error')
        other = Recorder(self.engine, ['src'])
        self.addCleanup(other.stop)
        self.assertEqual(other.scan_once()['state'], 'active')

    def test_explicit_client_config_and_editor_directories_are_protected(self):
        for scope in ('.mcp.json', 'mcp.json', '.vscode/settings.json', '.cursor/mcp.json',
                      '.windsurf/mcp.json', '.continue/config.json', 'src/.mcp.json'):
            with self.subTest(scope=scope), self.assertRaises(UndoWeaveError) as caught:
                validate_scopes([scope])
            self.assertEqual(caught.exception.code, 'protected_path')

    @unittest.skipUnless(shutil.which('git'), 'Git supplies the independent synthetic ignore oracle')
    def test_git_oracle_globstar_case_and_leading_space_never_leak_ignored_content(self):
        environment = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                       'GIT_TERMINAL_PROMPT': '0'}
        empty = self.base / 'empty-template'
        empty.mkdir()
        init = subprocess.run(['git', '-C', str(self.root), 'init', '--quiet', '--template=' + str(empty)],
                              env=environment, capture_output=True, timeout=10)
        self.assertEqual(init.returncode, 0)
        (self.root / '.gitignore').write_text('src/**/local.txt\nsrc/**/deep/**/mixed.txt\n local.py\nsrc/case.txt\n*.py[cod]\n', encoding='utf-8')
        ignored = ['src/local.txt', 'src/nested/local.txt', 'src/deep/mixed.txt',
                   'src/nested/deep/mixed.txt', 'src/deep/nested/mixed.txt',
                   'src/ local.py', 'src/nested/ local.py', 'src/CASE.TXT', 'src/module.pyc']
        for relative in ignored:
            file = self.root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'IGNORED_PRIVATE_ORACLE_BEFORE\n')
        oracle = subprocess.run(['git', '-C', str(self.root), '-c', 'core.ignorecase=true',
                                 'check-ignore', '--no-index', '--stdin', '-z'],
                                input=b'\0'.join(value.encode('utf-8') for value in ignored) + b'\0', env=environment,
                                capture_output=True, timeout=10)
        self.assertEqual(oracle.returncode, 0, oracle.stderr.decode('utf-8', 'replace'))
        self.assertEqual(set(oracle.stdout.decode('utf-8').rstrip('\0').split('\0')), set(ignored))
        (self.root / 'src' / 'module.py').write_bytes(b'ordinary source\n')
        self.recorder.scan_once()
        self.assertEqual(self.recorder.status()['files'], 2)
        for relative in ignored:
            (self.root / relative).write_bytes(b'IGNORED_PRIVATE_ORACLE_AFTER\n')
        self.recorder.scan_once(); self.recorder.scan_once()
        self.assertEqual(self.engine.history(), [])
        self.assertNotIn(b'IGNORED_PRIVATE_ORACLE', self.engine.db_path.read_bytes())

    def test_save_after_file_read_before_census_end_is_inside_observed_interval(self):
        other = self.root / 'src' / 'z.cfg'
        other.write_bytes(b'alpha = 1\n')
        with os.scandir(self.target.parent) as entries:
            ordered = [entry.name for entry in entries]
        first, later = ('src/' + name for name in ordered)
        clock, phase, policy_calls = [0], [True], [0]
        original = self.engine._read
        def timed_read(path, **kwargs):
            text = original(path, **kwargs)
            if phase[0] and path == first:
                clock[0] = 5
            elif phase[0] and path == later:
                (self.root / first).write_bytes(b'saved at 10\n')
                clock[0] = 10
            elif phase[0] and path == '.gitignore':
                policy_calls[0] += 1
                if policy_calls[0] == 2:
                    clock[0], phase[0] = 30, False
            return text
        with patch.object(module.time, 'time', side_effect=lambda: clock[0]), patch.object(self.engine, '_read', side_effect=timed_read):
            self.recorder.scan_once()
            clock[0] = 40
            self.recorder.scan_once()
            clock[0] = 50
            self.recorder.scan_once()
        row = self.engine.history()[0]
        self.assertEqual(row['path'], first)
        self.assertLessEqual(row['observation']['observed_from'], 10)
        self.assertGreaterEqual(row['observation']['observed_to'], 10)


if __name__ == '__main__':
    unittest.main()
