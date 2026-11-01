// Adapter to an unchanged, separately checked-out upstream implementation.
// No upstream code is copied into this repository and no npm packages are needed.
import { mkdtemp, mkdir, readFile, writeFile, unlink, rm, realpath } from 'node:fs/promises';
import { dirname, join, resolve, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const [source, fixturePath] = process.argv.slice(2);
if (!source || !fixturePath) throw new Error('Usage: node pi_rewind_runner.mjs CHECKOUT/src/core.ts fixtures.json');
const { createCheckpoint, restoreCheckpoint } = await import(pathToFileURL(resolve(source)).href);
const fixtures = JSON.parse(await readFile(fixturePath, 'utf8'));
const outer = await mkdtemp(join(tmpdir(), 'undoweave-comparison-'));
const realOuter = await realpath(outer);
const realTemp = await realpath(tmpdir());
if (dirname(realOuter).toLowerCase() !== realTemp.toLowerCase() || !basename(realOuter).startsWith('undoweave-comparison-')) {
  throw new Error('Temporary-directory containment check failed');
}

function git(root, args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) throw new Error(`git ${args[0]} failed: ${result.stderr}`);
  return result.stdout.trim();
}
async function change(root, edit) {
  const target = join(root, edit.path);
  if (edit.content === null) await unlink(target);
  else await writeFile(target, edit.content, 'utf8');
}
async function capture(root, fixture) {
  const paths = new Set([...Object.keys(fixture.initial), fixture.edit.path, ...fixture.later.map(x => x.path)]);
  const files = {};
  for (const name of paths) {
    try { files[name] = await readFile(join(root, name), 'utf8'); }
    catch (error) { if (error.code === 'ENOENT') files[name] = null; else throw error; }
  }
  return files;
}
function equalFiles(actual, expected) {
  const all = new Set([...Object.keys(actual), ...Object.keys(expected)]);
  return [...all].every(name => (actual[name] ?? null) === (expected[name] ?? null));
}

const results = [];
try {
  for (const fixture of fixtures) {
    const root = join(outer, fixture.id);
    await mkdir(root);
    for (const [path, content] of Object.entries(fixture.initial)) await writeFile(join(root, path), content, 'utf8');
    if (!fixture.no_git) {
      git(root, ['init', '-q']);
      const hookDir = join(outer, `${fixture.id}-empty-hooks`);
      await mkdir(hookDir);
      git(root, ['config', 'core.hooksPath', hookDir]);
      git(root, ['config', 'core.autocrlf', 'false']);
      git(root, ['config', 'commit.gpgsign', 'false']);
      git(root, ['config', 'user.name', 'Disposable comparison']);
      git(root, ['config', 'user.email', 'comparison@example.invalid']);
      git(root, ['add', '--all']);
      git(root, ['commit', '-q', '-m', 'synthetic fixture']);
    }
    const opts = { root, id: `before-a-${fixture.id}`, sessionId: 'disposable-comparison', trigger: 'turn', turnIndex: 0 };
    let checkpoint;
    try { checkpoint = await createCheckpoint(opts); }
    catch (error) {
      results.push({id: fixture.id, status: fixture.no_git ? 'unsupported_no_git' : 'error', error: String(error.message), selective_goal_met: false});
      continue;
    }
    await change(root, fixture.edit);
    for (const later of fixture.later) await change(root, later);
    const beforeRestore = await capture(root, fixture);
    // The Pi UI takes a recovery checkpoint before restore. Model that safety
    // net explicitly so the comparison does not imply the prior state is lost.
    const recovery = await createCheckpoint({...opts, id: `recovery-${fixture.id}`, trigger: 'before-restore', turnIndex: 1});
    await restoreCheckpoint(root, checkpoint);
    const actual = await capture(root, fixture);
    const recoveryAvailable = !!recovery.worktreeTreeSha;
    results.push({
      id: fixture.id,
      status: 'restored_checkpoint',
      actual,
      project_unchanged: equalFiles(actual, beforeRestore),
      selective_goal_met: fixture.expected_status === 'ready' && equalFiles(actual, fixture.expected),
      recovery_checkpoint_saved: recoveryAvailable
    });
  }
  process.stdout.write(JSON.stringify(results));
} finally {
  // Only this exact mkdtemp-generated directory may be recursively removed.
  if ((await realpath(outer)) !== realOuter) throw new Error('Temporary directory changed during comparison');
  await rm(outer, {recursive: true, force: true});
}
