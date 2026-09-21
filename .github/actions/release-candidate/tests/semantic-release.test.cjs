// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createRepository } = require('./helpers/repository.cjs');

for (const defaultBranch of ['master', 'main']) {
  for (const [baseline, target] of [['1.2.9', '1.3.0'], ['2.7.9', '2.8.0']]) {
    test(`real RC lifecycle: ${defaultBranch}, release/${target}`, async (t) => {
      const repository = await createRepository(t, { defaultBranch, target });
      const { git, remoteGit, commit, release, runRelease, verifyExistingTag, assertTags, branch } = repository;
      const stable = await commit('fix: establish stable release');
      await git('tag', `v${baseline}`);
      await git('tag', `v${baseline.split('.')[0]}`);
      await git('push', '--set-upstream', 'origin', defaultBranch, '--tags');
      const stableTags = { [`v${baseline}`]: stable, [`v${baseline.split('.')[0]}`]: stable };
      let defaultHead;

      await t.test('docs on the default branch do not release or move stable tags', async () => {
        defaultHead = await commit('docs: clarify stable documentation');
        await git('push', 'origin', defaultBranch);
        assert.equal(await release(), false);
        await assertTags(stableTags);
      });

      await git('switch', '--create', branch);
      const feature = await commit('feat(api): add release candidate capability');
      await git('push', '--set-upstream', 'origin', branch);

      await t.test('feature dry-run computes rc.1 without creating a tag', async () => {
        const result = await release(true);
        assert.equal(result.lastRelease.version, baseline);
        assert.equal(result.lastRelease.gitHead, stable);
        assert.equal(result.nextRelease.version, `${target}-rc.1`);
        assert.equal(result.nextRelease.gitTag, `v${target}-rc.1`);
        assert.equal(result.nextRelease.channel, 'rc');
        assert.equal(result.nextRelease.type, 'minor');
        assert.equal(result.nextRelease.gitHead, feature);
        assert.match(result.nextRelease.notes, /add release candidate capability/);
        assert.doesNotMatch(result.nextRelease.notes, /clarify stable documentation/);
        await assertTags(stableTags);
      });

      const rc1Tags = { ...stableTags, [`v${target}-rc.1`]: feature };
      await t.test('the actual verifyRelease hook rejects a mismatched workflow SHA before tagging', async () => {
        const result = await runRelease(false, { GITHUB_SHA: stable });
        assert.equal(result.signal, null, result.stderr);
        assert.equal(result.status, 1, result.stderr);
        assert.deepEqual(JSON.parse(result.stdout).errors, [{
          message: 'RC source commit does not match this workflow run',
          pluginName: require.resolve('../validate-version.cjs'),
        }]);
        await assertTags(stableTags);
        assert.equal(await remoteGit('rev-parse', `refs/heads/${branch}`), feature);
      });

      await t.test('actual publication pushes rc.1 and rc channel notes to the bare origin', async () => {
        const result = await release();
        assert.equal(result.nextRelease.version, `${target}-rc.1`);
        assert.equal(result.nextRelease.gitTag, `v${target}-rc.1`);
        assert.equal(result.nextRelease.channel, 'rc');
        const note = await remoteGit('notes', `--ref=semantic-release-v${target}-rc.1`, 'show', `v${target}-rc.1`);
        assert.deepEqual(JSON.parse(note), { channels: ['rc'] });
        await assertTags(rc1Tags);
      });

      await t.test('existing tag verifies with notes fetched from origin and helper-relative validation', async () => {
        await git('update-ref', '-d', `refs/notes/semantic-release-v${target}-rc.1`);
        const result = await verifyExistingTag(`${target}-rc.1`, feature);
        assert.equal(result.signal, null, result.stderr);
        assert.equal(result.status, 0, result.stderr);
        await assertTags(rc1Tags);
      });

      await t.test('rerunning the published commit is a no-op', async () => {
        assert.equal(await release(), false);
        await assertTags(rc1Tags);
      });

      const fix = await commit('fix(api): correct release candidate behavior');
      await git('push', 'origin', branch);
      const rc2Tags = { ...rc1Tags, [`v${target}-rc.2`]: fix };
      await t.test('a subsequent fix publishes rc.2 on the same version base', async () => {
        const result = await release();
        assert.equal(result.lastRelease.version, `${target}-rc.1`);
        assert.equal(result.nextRelease.version, `${target}-rc.2`);
        assert.equal(result.nextRelease.gitTag, `v${target}-rc.2`);
        assert.equal(result.nextRelease.channel, 'rc');
        assert.equal(result.nextRelease.type, 'patch');
        assert.equal(result.nextRelease.gitHead, fix);
        assert.match(result.nextRelease.notes, /correct release candidate behavior/);
        assert.doesNotMatch(result.nextRelease.notes, /add release candidate capability/);
        await assertTags(rc2Tags);
      });

      await t.test('docs after rc.2 do not release or move any existing tag', async () => {
        await commit('docs: clarify release candidate usage');
        await git('push', 'origin', branch);
        assert.equal(await release(), false);
        await assertTags(rc2Tags);
        assert.equal(await remoteGit('rev-parse', `refs/heads/${defaultBranch}`), defaultHead);
      });

      await t.test('the actual verifyRelease hook rejects a version mismatch before tagging', async () => {
        await commit('feat(api)!: break compatibility');
        await git('push', 'origin', branch);
        const result = await runRelease();
        assert.equal(result.signal, null, result.stderr);
        assert.equal(result.status, 1, result.stderr);
        assert.deepEqual(JSON.parse(result.stdout).errors, [{
          message: 'Calculated RC version does not match the release branch target',
          pluginName: require.resolve('../validate-version.cjs'),
        }]);
        await assertTags(rc2Tags);
        assert.equal(await remoteGit('rev-parse', `refs/heads/${defaultBranch}`), defaultHead);
      });

      await t.test('a matching-head manual RC tag without notes fails closed with a repair message', async () => {
        const head = await git('rev-parse', 'HEAD');
        await git('tag', `v${target}-rc.3`);
        await git('push', 'origin', `refs/tags/v${target}-rc.3`);
        const manualTags = { ...rc2Tags, [`v${target}-rc.3`]: head };
        const result = await verifyExistingTag(`${target}-rc.3`, head);
        assert.equal(result.signal, null, result.stderr);
        assert.equal(result.status, 1, result.stderr);
        assert.match(result.stderr, /Existing RC has no readable channel metadata/);
        assert.match(result.stderr, /maintainer must repair the original release/);
        assert.match(result.stderr, /No tag was changed/);
        await assertTags(manualTags);
      });

      await t.test('existing RC verification rejects incorrect source SHA, tag, and version', async () => {
        for (const extraEnv of [
          { GITHUB_SHA: stable },
          { RC_TAG: `v${target}-rc.1` },
          { GITHUB_REF_NAME: 'release/9.0.0' },
        ]) {
          const result = await verifyExistingTag(`${target}-rc.2`, fix, extraEnv);
          assert.equal(result.signal, null, result.stderr);
          assert.equal(result.status, 1, result.stderr);
        }
      });

      await t.test('non-rc and malformed channel notes fail closed without changing tags', async () => {
        const head = await git('rev-parse', 'HEAD');
        const manualTag = `v${target}-rc.3`;
        for (const note of ['{"channels":["beta"]}', '{"channels":"rc"}', 'not json']) {
          await remoteGit('notes', `--ref=semantic-release-${manualTag}`, 'add', '--force', '-m', note, manualTag);
          const result = await verifyExistingTag(`${target}-rc.3`, head);
          assert.equal(result.signal, null, result.stderr);
          assert.equal(result.status, 1, result.stderr);
          assert.match(result.stderr, /Existing RC channel metadata is invalid/);
          await assertTags({ ...rc2Tags, [manualTag]: head });
        }
        assert.equal(await remoteGit('rev-parse', `refs/heads/${defaultBranch}`), defaultHead);
      });
    });
  }
}
