// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { mkdir, writeFile } = require('node:fs/promises');
const { join } = require('node:path');
const { test } = require('node:test');
const { createRepository } = require('./helpers/repository.cjs');

async function runTarget(repository, mode = 'plan') {
  return repository.run(process.execPath, [
    join(__dirname, 'helpers/target-release.mjs'), repository.repositoryUrl,
    repository.branch, await repository.git('rev-parse', 'HEAD'), mode,
  ], { cwd: repository.work, timeout: 30_000 });
}

async function targetPlan(repository, mode = 'plan') {
  const result = await runTarget(repository, mode);
  assert.equal(result.signal, null, result.stderr);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  return JSON.parse(result.stdout);
}

async function releaseRefs(repository) {
  const args = ['for-each-ref', '--format=%(refname) %(objectname)', 'refs/tags', 'refs/notes'];
  return { local: await repository.git(...args), remote: await repository.remoteGit(...args) };
}

async function rejectsPlan(repository, pattern) {
  const before = await releaseRefs(repository);
  // Exercise the publication path too: a rejected plan must never reach publishTag.
  const result = await runTarget(repository, 'publish');
  assert.equal(result.signal, null, result.stderr);
  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`);
  assert.match(JSON.parse(result.stdout).error, pattern);
  assert.deepEqual(await releaseRefs(repository), before);
}

async function tagRelease(repository, version, annotated = false) {
  const tag = `v${version}`;
  await repository.git('tag', ...(annotated ? ['--annotate', '--message', tag] : []), tag);
  if (version.includes('-rc.')) {
    await repository.git('notes', `--ref=semantic-release-${tag}`, 'add',
      '-m', JSON.stringify({ channels: ['rc'] }), tag);
  }
}

async function pushHistory(repository, ...branches) {
  await repository.git('push', 'origin', ...branches, '--tags');
  if (await repository.git('for-each-ref', '--format=%(refname)', 'refs/notes')) {
    await repository.git('push', 'origin', 'refs/notes/*:refs/notes/*');
  }
}

async function stableRepository(t, target = '1.16.0', annotated = false) {
  const repository = await createRepository(t, { defaultBranch: 'master', target });
  const stable = await repository.commit('fix: establish the previous stable release');
  await tagRelease(repository, '1.15.0', annotated);
  await pushHistory(repository, 'master');
  await repository.git('switch', '--create', repository.branch);
  return { ...repository, stable };
}

test('issue 114: a divergent stable release starts a fresh target-line RC lifecycle', async (t) => {
  const repository = await createRepository(t, { defaultBranch: 'master', target: '1.16.0' });
  const { git, remoteGit, commit, branch, assertTags } = repository;
  const olderStable = await commit('fix: establish the older stable release');
  await tagRelease(repository, '1.14.0');
  await commit('feat(api): capability already shipped in 1.15');
  const oldRc = await commit('fix(api): harden the old candidate');
  await tagRelease(repository, '1.15.0-rc.2');
  await git('switch', '--create', 'release/1.15.0');
  const stable = await commit('fix(api): stable-only release branch repair');
  await tagRelease(repository, '1.15.0');
  await git('switch', 'master');
  const feature = await commit('feat(api): capability for the new target');
  await git('switch', '--create', branch);
  await pushHistory(repository, 'master', 'release/1.15.0', branch);
  const initialTags = { 'v1.14.0': olderStable, 'v1.15.0-rc.2': oldRc, 'v1.15.0': stable };

  assert.equal(await git('merge-base', stable, feature), oldRc);
  assert.notEqual(await git('merge-base', stable, feature), stable);
  assert.equal(await remoteGit('rev-parse', 'refs/heads/master'), feature);

  await t.test('dry-run selects stable 1.15, ignores its ancestor RC, and creates no refs', async () => {
    const before = await releaseRefs(repository);
    const plan = await targetPlan(repository);
    assert.equal(plan.version, '1.16.0-rc.1');
    assert.equal(plan.tag, 'v1.16.0-rc.1');
    assert.equal(plan.target, '1.16.0');
    assert.equal(plan.sha, feature);
    assert.equal(plan.reused, false);
    assert.deepEqual(plan.baseline, { tag: 'v1.15.0', version: '1.15.0', sha: stable });
    assert.equal(plan.previous, null);
    assert.deepEqual(plan.commits.map(({ hash }) => hash), [feature]);
    assert.match(plan.notes, /capability for the new target/);
    assert.doesNotMatch(plan.notes, /already shipped|old candidate|stable-only/);
    assert.match(plan.notes, /v1\.15\.0\.\.\.v1\.16\.0-rc\.1/);
    assert.deepEqual(await releaseRefs(repository), before);
    await assertTags(initialTags);
  });

  await t.test('publication creates rc.1 and readable rc channel metadata at origin', async () => {
    const plan = await targetPlan(repository, 'publish');
    assert.equal(plan.version, '1.16.0-rc.1');
    await assertTags({ ...initialTags, 'v1.16.0-rc.1': feature });
    const note = await remoteGit('notes', '--ref=semantic-release-v1.16.0-rc.1', 'show', plan.tag);
    assert.deepEqual(JSON.parse(note), { channels: ['rc'] });
  });

  await t.test('same-commit reruns reuse rc.1 and preserve its notes without pushing', async () => {
    const before = await releaseRefs(repository);
    const plan = await targetPlan(repository, 'publish');
    assert.equal(plan.reused, true);
    assert.equal(plan.version, '1.16.0-rc.1');
    assert.equal(plan.sha, feature);
    assert.match(plan.notes, /capability for the new target/);
    assert.deepEqual(await releaseRefs(repository), before);
  });

  let fix;
  await t.test('a new fix publishes rc.2 using the original minor bump and only delta notes', async () => {
    fix = await commit('fix(api): repair the new candidate');
    await git('push', 'origin', branch);
    const plan = await targetPlan(repository, 'publish');
    assert.equal(plan.version, '1.16.0-rc.2');
    assert.equal(plan.sha, fix);
    assert.equal(plan.baseline.sha, stable);
    assert.deepEqual(plan.previous, { tag: 'v1.16.0-rc.1', version: '1.16.0-rc.1', sha: feature });
    assert.deepEqual(plan.commits.map(({ hash }) => hash), [fix]);
    assert.match(plan.notes, /repair the new candidate/);
    assert.doesNotMatch(plan.notes, /capability for the new target|already shipped|stable-only/);
    assert.match(plan.notes, /v1\.16\.0-rc\.1\.\.\.v1\.16\.0-rc\.2/);
    await assertTags({ ...initialTags, 'v1.16.0-rc.1': feature, 'v1.16.0-rc.2': fix });
    const note = await remoteGit('notes', '--ref=semantic-release-v1.16.0-rc.2', 'show', plan.tag);
    assert.deepEqual(JSON.parse(note), { channels: ['rc'] });
  });

  await t.test('docs-only changes after rc.2 produce no candidate or note', async () => {
    await commit('docs: document the new candidate');
    await git('push', 'origin', branch);
    const before = await releaseRefs(repository);
    assert.equal(await targetPlan(repository, 'publish'), null);
    assert.deepEqual(await releaseRefs(repository), before);
  });

  await t.test('a later breaking change cannot remain on the minor target', async () => {
    await commit('feat(api)!: remove the previous protocol\n\nBREAKING CHANGE: the old protocol is removed');
    await git('push', 'origin', branch);
    await rejectsPlan(repository, /Calculated release 2\.0\.0 does not match release branch target 1\.16\.0/);
  });
  assert.equal(await remoteGit('rev-parse', 'refs/heads/master'), feature);
  assert.equal(await remoteGit('rev-parse', 'refs/heads/release/1.15.0'), stable);
});

test('maintenance 1.15.1 uses stable 1.15.0 even when stable 1.16.0 exists', async (t) => {
  const repository = await stableRepository(t, '1.15.1');
  await repository.git('switch', '--create', 'release/1.16.0');
  const newer = await repository.commit('feat: ship a newer minor release');
  await tagRelease(repository, '1.16.0');
  await repository.git('switch', repository.branch);
  const fix = await repository.commit('fix: backport the maintenance repair');
  await pushHistory(repository, 'release/1.16.0', repository.branch);
  const plan = await targetPlan(repository, 'publish');
  assert.equal(plan.version, '1.15.1-rc.1');
  assert.equal(plan.target, '1.15.1');
  assert.equal(plan.baseline.sha, repository.stable);
  assert.equal(plan.baseline.version, '1.15.0');
  assert.equal(plan.previous, null);
  assert.deepEqual(plan.commits.map(({ hash }) => hash), [fix]);
  assert.match(plan.notes, /backport the maintenance repair/);
  assert.doesNotMatch(plan.notes, /newer minor release/);
  await repository.assertTags({ 'v1.15.0': repository.stable, 'v1.16.0': newer, 'v1.15.1-rc.1': fix });
});

for (const { target, message, expected } of [
  { target: '1.16.0', message: 'fix: patch cannot justify a minor release', expected: '1.15.1' },
  { target: '1.15.1', message: 'feat: feature cannot fit a patch release', expected: '1.16.0' },
  { target: '2.0.0', message: 'feat: feature cannot justify a new major', expected: '1.16.0' },
  { target: '1.17.0', message: 'feat: feature cannot skip a minor', expected: '1.16.0' },
  { target: '1.15.2', message: 'fix: patch cannot skip a patch', expected: '1.15.1' },
  { target: '1.16.0', message: 'feat!: incompatible API\n\nBREAKING CHANGE: remove the API', expected: '2.0.0' },
]) {
  test(`target mismatch rejects ${JSON.stringify(message.split('\n')[0])} on release/${target}`, async (t) => {
    const repository = await stableRepository(t, target);
    await repository.commit(message);
    await pushHistory(repository, repository.branch);
    await rejectsPlan(repository, new RegExp(
      `Calculated release ${expected.replaceAll('.', '\\.')} does not match release branch target ${target.replaceAll('.', '\\.')}`,
    ));
  });
}

test('a target that is already stable cannot publish another RC', async (t) => {
  const repository = await stableRepository(t);
  await repository.commit('feat: complete the target release');
  await tagRelease(repository, '1.16.0');
  await repository.commit('fix: change after the stable release');
  await pushHistory(repository, repository.branch);
  await rejectsPlan(repository, /1\.16\.0 is already stable/);
});

for (const metadata of [null, { channels: ['beta'] }]) {
  test(`a new HEAD cannot bypass the latest RC's ${metadata ? 'wrong' : 'missing'} channel metadata`, async (t) => {
    const repository = await stableRepository(t);
    const previous = await repository.commit('feat: establish an incomplete candidate');
    const tag = 'v1.16.0-rc.1';
    await repository.git('tag', tag);
    if (metadata) {
      await repository.git('notes', `--ref=semantic-release-${tag}`, 'add',
        '-m', JSON.stringify(metadata), tag);
    }
    const head = await repository.commit('fix: advance past the incomplete candidate');
    assert.notEqual(head, previous);
    await pushHistory(repository, repository.branch);
    await rejectsPlan(repository, /Latest RC has no valid channel metadata; repair v1\.16\.0-rc\.1 before publishing/);
  });
}

for (const matchingHead of [false, true]) {
  test(`latest detached RC fails closed${matchingHead ? ' even when an earlier RC matches HEAD' : ''}`, async (t) => {
    const repository = await stableRepository(t);
    const earlier = await repository.commit('feat: establish the target candidate');
    await tagRelease(repository, '1.16.0-rc.1');
    await repository.git('switch', '--create', 'detached-candidate');
    await repository.commit('fix: changes on a detached candidate branch');
    await tagRelease(repository, '1.16.0-rc.2');
    await repository.git('switch', repository.branch);
    if (!matchingHead) await repository.commit('fix: changes on the active candidate branch');
    else assert.equal(await repository.git('rev-parse', 'HEAD'), earlier);
    await pushHistory(repository, repository.branch, 'detached-candidate');
    await rejectsPlan(repository, /latest target-line RC is not an ancestor/);
  });
}

for (const advancedHead of [false, true]) {
  test(`inverted RC numbering rejects ${advancedHead ? 'a new HEAD beyond rc.1' : 'reuse when rc.1 matches HEAD'}`, async (t) => {
    const repository = await stableRepository(t);
    const rc2 = await repository.commit('feat: establish the higher-numbered candidate');
    await tagRelease(repository, '1.16.0-rc.2');
    const rc1 = await repository.commit('fix: incorrectly number a later candidate');
    await tagRelease(repository, '1.16.0-rc.1');
    assert.equal(await repository.git('merge-base', rc2, rc1), rc2);
    if (advancedHead) await repository.commit('fix: advance beyond the inverted candidates');
    else assert.equal(await repository.git('rev-parse', 'HEAD'), rc1);
    await pushHistory(repository, repository.branch);
    await rejectsPlan(repository, /RC sequence does not follow commit ancestry/);
  });
}

test('annotated stable and RC tags use peeled commits and numeric RC ordering', async (t) => {
  const repository = await stableRepository(t, '1.16.0', true);
  await repository.commit('feat: introduce the annotated candidate');
  await tagRelease(repository, '1.16.0-rc.2', true);
  const previous = await repository.commit('fix: harden the annotated candidate');
  await tagRelease(repository, '1.16.0-rc.10', true);
  const fix = await repository.commit('fix: repair the next annotated candidate');
  await pushHistory(repository, repository.branch);
  const before = await releaseRefs(repository);
  const plan = await targetPlan(repository);
  assert.equal(plan.version, '1.16.0-rc.11');
  assert.equal(plan.sha, fix);
  assert.equal(plan.baseline.sha, repository.stable);
  assert.equal(plan.previous.sha, previous);
  assert.equal(plan.previous.version, '1.16.0-rc.10');
  assert.notEqual(await repository.git('rev-parse', 'v1.15.0'), repository.stable);
  assert.notEqual(await repository.git('rev-parse', 'v1.16.0-rc.10'), previous);
  assert.deepEqual(plan.commits.map(({ hash }) => hash), [fix]);
  assert.deepEqual(await releaseRefs(repository), before);
});

test('first release starts at 1.0.0-rc.1 without a stable baseline', async (t) => {
  const repository = await createRepository(t, { defaultBranch: 'main', target: '1.0.0' });
  await repository.commit('docs: initialize the repository');
  await repository.git('switch', '--create', repository.branch);
  const feature = await repository.commit('feat: first public capability');
  await pushHistory(repository, 'main', repository.branch);
  const before = await releaseRefs(repository);
  const plan = await targetPlan(repository);
  assert.equal(plan.version, '1.0.0-rc.1');
  assert.equal(plan.target, '1.0.0');
  assert.equal(plan.sha, feature);
  assert.equal(plan.baseline, null);
  assert.equal(plan.previous, null);
  assert.match(plan.notes, /first public capability/);
  assert.doesNotMatch(plan.notes, /initialize the repository/);
  assert.deepEqual(await releaseRefs(repository), before);
  await targetPlan(repository, 'publish');
  await repository.assertTags({ 'v1.0.0-rc.1': feature });
});

test('an untagged repository cannot skip its first 1.0.0 release', async (t) => {
  const repository = await createRepository(t, { defaultBranch: 'main', target: '1.1.0' });
  await repository.commit('feat: first public capability');
  await repository.git('switch', '--create', repository.branch);
  await pushHistory(repository, 'main', repository.branch);
  await rejectsPlan(repository, /first releases must target 1\.0\.0/);
});

test('docs alone on a fresh target produce no RC before the first candidate', async (t) => {
  const repository = await stableRepository(t);
  await repository.commit('docs: describe the next release');
  await pushHistory(repository, repository.branch);
  const before = await releaseRefs(repository);
  assert.equal(await targetPlan(repository, 'publish'), null);
  assert.deepEqual(await releaseRefs(repository), before);
});

test('a reverted feature leaves no cumulative release even when the RC delta is a revert', async (t) => {
  const repository = await stableRepository(t);
  await writeFile(join(repository.work, 'capability.txt'), 'candidate capability\n');
  await repository.git('add', 'capability.txt');
  const feature = await repository.commit('feat: add a reversible capability');
  await pushHistory(repository, repository.branch);
  assert.equal((await targetPlan(repository, 'publish')).version, '1.16.0-rc.1');
  await repository.git('revert', '--no-edit', feature);
  await repository.git('push', 'origin', repository.branch);
  assert.match(await repository.git('log', '-1', '--format=%B'), new RegExp(`This reverts commit ${feature}`));
  const before = await releaseRefs(repository);
  assert.equal(await targetPlan(repository, 'publish'), null);
  assert.deepEqual(await releaseRefs(repository), before);
});

test('unrelated older RC lines do not supply a baseline, sequence, or ancestry constraint', async (t) => {
  const repository = await stableRepository(t);
  const feature = await repository.commit('feat: start the active release line');
  await repository.git('switch', '--orphan', 'retired-release-line');
  await repository.commit('feat!: unrelated retired candidate history');
  await tagRelease(repository, '1.15.0-rc.99');
  await repository.git('switch', repository.branch);
  await pushHistory(repository, repository.branch, 'retired-release-line');
  const before = await releaseRefs(repository);
  const plan = await targetPlan(repository);
  assert.equal(plan.version, '1.16.0-rc.1');
  assert.equal(plan.sha, feature);
  assert.equal(plan.baseline.version, '1.15.0');
  assert.equal(plan.previous, null);
  assert.deepEqual(plan.commits.map(({ hash }) => hash), [feature]);
  assert.doesNotMatch(plan.notes, /retired candidate history/);
  assert.deepEqual(await releaseRefs(repository), before);
});

for (const rejectedNamespace of ['tags', 'notes']) {
  test(`atomic publication leaves neither remote ref when the ${rejectedNamespace} update is rejected`, async (t) => {
    const repository = await stableRepository(t);
    const feature = await repository.commit('feat: candidate rejected by the server');
    await pushHistory(repository, repository.branch);
    const plan = await targetPlan(repository);
    assert.equal(plan.version, '1.16.0-rc.1');
    assert.equal(plan.sha, feature);
    const hooks = join(repository.remote, 'hooks');
    await mkdir(hooks, { recursive: true });
    // Reject just one ref: a non-atomic push would still publish the other one.
    await writeFile(join(hooks, 'update'), [
      '#!/bin/sh', `case "$1" in refs/${rejectedNamespace}/*)`,
      '  echo "fixture rejects this ref" >&2', '  exit 1 ;;', 'esac', 'exit 0', '',
    ].join('\n'), { mode: 0o755 });
    await repository.remoteGit('config', 'core.hooksPath', hooks);
    const before = (await releaseRefs(repository)).remote;
    const result = await runTarget(repository, 'publish');
    assert.equal(result.signal, null, result.stderr);
    assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`);
    assert.match(JSON.parse(result.stdout).error, /fixture rejects this ref/);
    assert.equal((await releaseRefs(repository)).remote, before);
    assert.equal(await repository.remoteGit('for-each-ref', '--format=%(refname)',
      `refs/tags/${plan.tag}`, `refs/notes/semantic-release-${plan.tag}`), '');
    assert.equal(await repository.remoteGit('rev-parse', `refs/heads/${repository.branch}`), feature);
  });
}
