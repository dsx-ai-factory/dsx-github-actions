// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { isAbsolute } = require('node:path');
const { test } = require('node:test');
const { createConfig } = require('../config.cjs');

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
