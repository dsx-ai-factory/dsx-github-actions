// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { join } = require('node:path');
const { test } = require('node:test');
const { createFixture } = require('./helpers/fixture.cjs');
const { validateVersion, verifyRelease } = require('../validate-version.cjs');

const branch = 'release/1.3.0';
const version = '1.3.0-rc.1';

test('accepts positive RC sequences for arbitrary canonical release targets', () => {
  for (const target of ['0.0.1', '1.3.0', '2.8.0', '10.20.30']) {
    for (const sequence of [1, 2, 10, 1234]) {
      assert.doesNotThrow(() => validateVersion(`release/${target}`, `${target}-rc.${sequence}`));
    }
  }
});

test('rejects malformed branches and branches that do not match the version', () => {
  for (const candidate of [
    undefined, '', 'master', 'main', 'release/1.2.9', 'release/1.3.1',
    'release/2.8.0', 'refs/heads/release/1.3.0', 'release/1.3.0/extra',
    'Release/1.3.0', ' release/1.3.0', 'release/1.3.0 ', 'release/1.3.0\n',
  ]) {
    assert.throws(() => validateVersion(candidate, version), JSON.stringify(candidate));
  }
  for (const target of ['01.3.0', '1.03.0', '1.3.00', '1.3', 'v1.3.0', '1.3.0-rc.1']) {
    assert.throws(() => validateVersion(`release/${target}`, `${target}-rc.1`), target);
  }
});

test('rejects mismatched versions, nonpositive sequences, and noncanonical input', () => {
  for (const candidate of [
    undefined, '', '1.3.0', 'v1.3.0-rc.1', '1.2.9-rc.1', '1.3.1-rc.1',
    '1.4.0-rc.1', '2.0.0-rc.1', '01.3.0-rc.1', '1.03.0-rc.1', '1.3.00-rc.1',
    '1.3.0-beta.1', '1.3.0-RC.1', '1.3.0-rc', '1.3.0-rc.', '1.3.0-rc.0',
    '1.3.0-rc.01', '1.3.0-rc.00', '1.3.0-rc.-1', '1.3.0-rc.+1',
    '1.3.0-rc.1.2', '1.3.0-rc.1e2', '1.3.0-rc.1+build', '1.3.0-rc.1suffix',
    ' 1.3.0-rc.1', '1.3.0-rc.1 ', '1.3.0-rc.1\n', '1.3.0-rc.1\r\n',
  ]) {
    assert.throws(() => validateVersion(branch, candidate), JSON.stringify(candidate));
  }
});

test('verifyRelease validates the semantic-release branch and calculated version', () => {
  for (const target of ['1.3.0', '2.8.0']) {
    assert.doesNotThrow(() => verifyRelease({}, {
      branch: { name: `release/${target}` },
      nextRelease: { version: `${target}-rc.1` },
    }));
    assert.throws(() => verifyRelease({}, {
      branch: { name: `release/${target}` },
      nextRelease: { version: '3.0.0-rc.1' },
    }));
  }
});

test('verifyRelease checks the source commit when the workflow SHA is provided', () => {
  const gitHead = '1'.repeat(40);
  const context = { branch: { name: branch }, nextRelease: { version, gitHead } };
  assert.doesNotThrow(() => verifyRelease({}, { ...context, env: { GITHUB_SHA: gitHead } }));
  assert.doesNotThrow(() => verifyRelease({}, context));
  assert.throws(() => verifyRelease({}, { ...context, env: { GITHUB_SHA: '2'.repeat(40) } }),
    /RC source commit does not match this workflow run/);
});

test('CLI reads GITHUB_REF_NAME and RC_VERSION and fails closed', async (t) => {
  const fixture = await createFixture(t);
  const cli = join(__dirname, '../validate-version.cjs');
  const cases = [
    { name: 'valid RC', branch, version, status: 0 },
    { name: 'multidigit RC', branch, version: '1.3.0-rc.10', status: 0 },
    { name: 'second target', branch: 'release/2.8.0', version: '2.8.0-rc.1', status: 0 },
    { name: 'missing branch', version, status: 1 },
    { name: 'missing version', branch, status: 1 },
    { name: 'missing both inputs', status: 1 },
    { name: 'master branch', branch: 'master', version, status: 1 },
    { name: 'main branch', branch: 'main', version, status: 1 },
    { name: 'wrong version base', branch, version: '2.8.0-rc.1', status: 1 },
    { name: 'stable version', branch, version: '1.3.0', status: 1 },
    { name: 'zero sequence', branch, version: '1.3.0-rc.0', status: 1 },
    { name: 'leading zero sequence', branch, version: '1.3.0-rc.01', status: 1 },
    { name: 'trailing newline', branch, version: '1.3.0-rc.1\n', status: 1 },
  ];
  for (const example of cases) {
    await t.test(example.name, async () => {
      const extraEnv = {};
      if (example.branch !== undefined) extraEnv.GITHUB_REF_NAME = example.branch;
      if (example.version !== undefined) extraEnv.RC_VERSION = example.version;
      const result = await fixture.run(process.execPath, [cli], { extraEnv, timeout: 5_000 });
      assert.equal(result.signal, null, result.stderr);
      assert.equal(result.status, example.status, result.stderr);
    });
  }
});
