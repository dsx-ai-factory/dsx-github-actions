// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { dirname, join } = require('node:path');
const { test } = require('node:test');
const { pathToFileURL } = require('node:url');
const { repositoryUrl } = require('../config.cjs');
const { createRepository } = require('./helpers/repository.cjs');

test('semantic-release retains SSH Git auth when GITHUB_TOKEN is available for its API', async (t) => {
  const repository = await createRepository(t, { defaultBranch: 'main', target: '1.3.0' });
  const { work, git, branch, commit, run } = repository;
  await commit('fix: seed auth fixture');
  await git('push', '--set-upstream', 'origin', 'main');
  await git('switch', '--create', branch);
  await git('push', '--set-upstream', 'origin', branch);
  const sshUrl = repositoryUrl('example/consumer', true);
  // Map only this fixture URL to local Git. The fixture forbids network protocols.
  await git('config', `url.${repository.repositoryUrl}.insteadOf`, sshUrl);
  const authModule = pathToFileURL(join(dirname(require.resolve('semantic-release')), 'lib/get-git-auth-url.js')).href;
  const result = await run(process.execPath, ['--input-type=module', '--eval', `
    import getGitAuthUrl from ${JSON.stringify(authModule)};
    const url = await getGitAuthUrl({
      cwd: process.cwd(), env: process.env,
      branch: { name: ${JSON.stringify(branch)} },
      options: { repositoryUrl: ${JSON.stringify(sshUrl)} },
    });
    process.stdout.write(url);
  `], {
    cwd: work,
    extraEnv: { GITHUB_ACTION: 'semantic-release', GITHUB_TOKEN: 'fixture-api-token-not-a-credential' },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, sshUrl);
  assert.equal(result.stderr, '');
});
