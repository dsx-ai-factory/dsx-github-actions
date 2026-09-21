// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { join } = require('node:path');
const { test } = require('node:test');
const { createRepository } = require('./helpers/repository.cjs');

for (const defaultBranch of ['master', 'main']) {
  test(`source guard checks the actual local Git source on ${defaultBranch}`, async (t) => {
    const repository = await createRepository(t, { defaultBranch, target: '2.8.0' });
    const { root, work, git, remoteGit, commit, run, branch, repositoryUrl } = repository;
    await commit('fix: establish stable release');
    await git('push', '--set-upstream', 'origin', defaultBranch);
    await git('switch', '--create', branch);
    const head = await commit('feat: add release capability');
    await git('push', '--set-upstream', 'origin', branch);
    const expectedEnv = {
      GITHUB_REF_NAME: branch,
      GITHUB_EVENT_NAME: 'push',
      GITHUB_REF: `refs/heads/${branch}`,
      GITHUB_SHA: head,
      RC_REF_PROTECTED: 'true',
    };
    const script = join(__dirname, '../check-source.sh');
    const check = (extraEnv = {}, cwd = work) => run(repository.bash, [script], {
      cwd, extraEnv: { ...expectedEnv, ...extraEnv }, timeout: 10_000,
    });
    const refs = await remoteGit('show-ref');

    await t.test('accepts the current protected release branch push', async () => {
      const result = await check();
      assert.equal(result.signal, null, result.stderr);
      assert.equal(result.status, 0, result.stderr);
    });

    for (const [name, extraEnv, message] of [
      ['pull request', { GITHUB_EVENT_NAME: 'pull_request' }],
      ['manual dispatch', { GITHUB_EVENT_NAME: 'workflow_dispatch' }],
      ['missing event', { GITHUB_EVENT_NAME: '' }],
      ['tag ref', { GITHUB_REF: 'refs/tags/v2.8.0-rc.1' }],
      ['wrong branch ref', { GITHUB_REF: 'refs/heads/release/2.9.0' }],
      ['default branch', { GITHUB_REF_NAME: defaultBranch, GITHUB_REF: `refs/heads/${defaultBranch}` }],
      ['noncanonical target', { GITHUB_REF_NAME: 'release/02.8.0', GITHUB_REF: 'refs/heads/release/02.8.0' }],
      ['unprotected branch', { RC_REF_PROTECTED: 'false' }, /Protect the release branch/],
      ['missing protection', { RC_REF_PROTECTED: '' }, /Protect the release branch/],
      ['wrong checkout SHA', { GITHUB_SHA: '0'.repeat(40) }],
      ['missing SHA', { GITHUB_SHA: '' }],
    ]) {
      await t.test(`rejects ${name}`, async () => {
        const result = await check(extraEnv);
        assert.equal(result.signal, null, result.stderr);
        assert.notEqual(result.status, 0, result.stderr);
        if (message) assert.match(result.stderr, message);
        assert.equal(await remoteGit('show-ref'), refs);
      });
    }

    await t.test('rejects a shallow checkout', async () => {
      const shallow = join(root, 'shallow');
      await git('clone', '--depth=1', '--branch', branch, '--template=', repositoryUrl, shallow);
      const result = await check({}, shallow);
      assert.equal(result.signal, null, result.stderr);
      assert.equal(result.status, 1, result.stderr);
      assert.equal(await remoteGit('show-ref'), refs);
    });

    await t.test('rejects a stale event even when the checkout matches the event SHA', async () => {
      const latest = await commit('fix: advance release branch');
      await git('push', 'origin', branch);
      await git('switch', '--detach', head);
      const result = await check();
      assert.equal(result.signal, null, result.stderr);
      assert.equal(result.status, 1, result.stderr);
      assert.match(result.stderr, /not the current release-branch head/);
      assert.equal(await remoteGit('rev-parse', `refs/heads/${branch}`), latest);
      assert.equal(await git('rev-parse', 'HEAD'), head);
      assert.equal(await remoteGit('tag', '--list'), '');
    });
  });
}
