// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const { isAbsolute, join } = require('node:path');
const { test } = require('node:test');
const { createConfig, repositoryUrl } = require('../config.cjs');
const { createFixture } = require('./helpers/fixture.cjs');

for (const defaultBranch of ['master', 'main']) {
  for (const target of ['1.3.0', '2.8.0']) {
    test(`shared config for ${defaultBranch} and release/${target}`, () => {
      const branch = `release/${target}`;
      const config = createConfig(branch, defaultBranch);
      assert.deepEqual(config.branches, [
        defaultBranch,
        { name: branch, channel: 'rc', prerelease: 'rc' },
      ]);
      assert.equal(config.tagFormat, 'v${version}');
      assert.deepEqual(config.plugins, [
        ['@semantic-release/commit-analyzer', { preset: 'conventionalcommits' }],
        ['@semantic-release/release-notes-generator', { preset: 'conventionalcommits' }],
        require.resolve('../validate-version.cjs'),
        ['@semantic-release/github', {
          successComment: false,
          failComment: false,
          releasedLabels: false,
        }],
      ]);
      assert.equal(isAbsolute(config.plugins[2]), true);
    });
  }
}

test('Git transport defaults to canonical HTTPS and supports canonical SSH', () => {
  assert.equal(repositoryUrl('example/consumer'), 'https://github.com/example/consumer.git');
  assert.equal(repositoryUrl('example/consumer', false), 'https://github.com/example/consumer.git');
  assert.equal(repositoryUrl('example/consumer', true), 'git@github.com:example/consumer.git');
  for (const repository of ['', 'https://other.invalid/repo', 'owner/repo/extra', 'owner/repo\n']) {
    for (const ssh of [false, true]) assert.throws(() => repositoryUrl(repository, ssh));
  }
});

for (const flag of [undefined, 'false', 'true']) {
  test(`config CLI selects transport for RC_GIT_SSH=${flag}`, async (t) => {
    const { root, run } = await createFixture(t);
    const result = await run(process.execPath, [join(__dirname, '../config.cjs')], {
      extraEnv: {
        GITHUB_REF_NAME: 'release/1.3.0',
        RC_DEFAULT_BRANCH: 'main',
        GITHUB_REPOSITORY: 'example/consumer',
        ...(flag === undefined ? {} : { RC_GIT_SSH: flag }),
      },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '');
    assert.equal(result.stderr, '');
    const config = JSON.parse(await readFile(join(root, '.releaserc.json'), 'utf8'));
    assert.deepEqual(config, {
      ...createConfig('release/1.3.0', 'main'),
      repositoryUrl: repositoryUrl('example/consumer', flag === 'true'),
    });
  });
}
