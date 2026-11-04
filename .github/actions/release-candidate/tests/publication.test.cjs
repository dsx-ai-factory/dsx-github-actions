// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { mkdir, readFile, writeFile } = require('node:fs/promises');
const { dirname, join, delimiter } = require('node:path');
const { test } = require('node:test');
const { createRepository } = require('./helpers/repository.cjs');

const publisher = join(__dirname, '../publish.mjs');

async function setup(t) {
  const r = await createRepository(t, { defaultBranch: 'main', target: '1.3.0' });
  await r.commit('fix: baseline');
  await r.git('tag', 'v1.2.9');
  await r.git('push', 'origin', 'main', '--tags');
  await r.git('switch', '-c', r.branch);
  const sha = await r.commit('feat: new capability');
  await r.git('push', 'origin', r.branch);
  const env = {
    GITHUB_REF_NAME: r.branch, GITHUB_SHA: sha, GITHUB_REPOSITORY: 'example/consumer',
    GITHUB_OUTPUT: join(r.root, 'output'), RC_PLAN_PATH: join(r.root, 'plan.json'),
    RC_NOTES_PATH: join(r.root, 'notes.md'),
  };
  const cli = (mode, extraEnv = {}) => r.run(process.execPath, [publisher, mode], {
    cwd: r.work, extraEnv: { ...env, ...extraEnv },
  });
  const refs = () => r.remoteGit('for-each-ref', '--format=%(refname) %(objectname)');
  return { ...r, sha, env, cli, refs };
}

test('the production CLI previews without writes, publishes atomically, and reuses a verified RC', async (t) => {
  const r = await setup(t);
  const before = await r.refs();
  let result = await r.cli('--dry-run');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(await r.refs(), before);
  const plan = JSON.parse(await readFile(r.env.RC_PLAN_PATH, 'utf8'));
  assert.equal(plan.version, '1.3.0-rc.1');
  assert.match(await readFile(r.env.RC_NOTES_PATH, 'utf8'), /new capability/);
  assert.match(await readFile(r.env.GITHUB_OUTPUT, 'utf8'), /should-publish=true\nversion=1.3.0-rc.1/);
  result = await r.cli('--publish');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(await r.remoteGit('rev-parse', 'v1.3.0-rc.1'), r.sha);
  result = await r.verifyExistingTag('1.3.0-rc.1', r.sha);
  assert.equal(result.status, 0, result.stderr);
  const published = await r.refs();
  result = await r.cli('--dry-run', {
    RC_PLAN_PATH: join(r.root, 'retry.json'), RC_NOTES_PATH: join(r.root, 'retry.md'),
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(await readFile(join(r.root, 'retry.json'), 'utf8')).reused, true);
  assert.equal(await readFile(join(r.root, 'retry.md'), 'utf8'), await readFile(r.env.RC_NOTES_PATH, 'utf8'));
  assert.equal(await r.refs(), published);
});

for (const change of ['head', 'stable', 'missing-preview', 'tampered-preview']) {
  test(`publication fails without changing remote refs after ${change}`, async (t) => {
    const r = await setup(t);
    if (change !== 'missing-preview') {
      const result = await r.cli('--dry-run');
      assert.equal(result.status, 0, result.stderr);
    }
    if (change === 'head') {
      await r.commit('fix: newer branch head');
      await r.git('push', 'origin', r.branch);
    } else if (change === 'stable') {
      await r.git('tag', 'v1.3.0');
      await r.git('push', 'origin', 'refs/tags/v1.3.0');
    } else if (change === 'tampered-preview') {
      const plan = JSON.parse(await readFile(r.env.RC_PLAN_PATH, 'utf8'));
      plan.version = '1.3.0-rc.99';
      await writeFile(r.env.RC_PLAN_PATH, JSON.stringify(plan));
    }
    const before = await r.refs();
    const result = await r.cli('--publish');
    assert.notEqual(result.status, 0);
    assert.equal(await r.refs(), before);
  });
}

for (const state of ['missing', 'existing', 'draft', 'stable', 'forbidden']) {
  test(`prerelease verification handles ${state} without replacing an existing release`, async (t) => {
    const r = await setup(t);
    assert.equal((await r.cli('--dry-run')).status, 0);
    assert.equal((await r.cli('--publish')).status, 0);
    const bin = join(r.root, 'bin');
    await mkdir(bin);
    // Mock only GitHub HTTP; source tagging and channel verification use the real bare repository.
    await writeFile(join(bin, 'gh'), `#!${process.execPath}
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.TEST_GH_LOG, JSON.stringify(args) + '\\n');
if (args[0] === 'release') {
  fs.writeFileSync(process.env.TEST_GH_CREATED, 'yes');
} else if (process.env.TEST_GH_STATE === 'forbidden') {
  console.error('gh: Forbidden (HTTP 403)'); process.exit(1);
} else if (process.env.TEST_GH_STATE === 'missing' && !fs.existsSync(process.env.TEST_GH_CREATED)) {
  console.error('gh: Not Found (HTTP 404)'); process.exit(1);
} else {
  console.log(JSON.stringify({tag_name: process.env.RC_TAG,
    draft: process.env.TEST_GH_STATE === 'draft', prerelease: process.env.TEST_GH_STATE !== 'stable',
    html_url: 'https://github.com/example/consumer/releases/tag/' + process.env.RC_TAG}));
}
`, { mode: 0o755 });
    const log = join(r.root, 'gh.log');
    const result = await r.run(r.bash, [join(__dirname, '../verify-prerelease.sh')], {
      cwd: r.work,
      extraEnv: {
        ...r.env, RC_TAG: 'v1.3.0-rc.1', RC_VERSION: '1.3.0-rc.1',
        GITHUB_STEP_SUMMARY: join(r.root, 'summary'),
        PATH: [bin, dirname(process.execPath), '/opt/homebrew/bin', '/usr/bin', '/bin'].join(delimiter),
        TEST_GH_STATE: state, TEST_GH_LOG: log, TEST_GH_CREATED: join(r.root, 'created'),
      },
    });
    assert.equal(result.status === 0, ['missing', 'existing'].includes(state), result.stderr);
    const calls = (await readFile(log, 'utf8')).trim().split('\n').map(JSON.parse);
    const creates = calls.filter((args) => args[0] === 'release');
    assert.equal(creates.length, state === 'missing' ? 1 : 0);
    if (state === 'missing') {
      assert.ok(creates[0].includes('--verify-tag'));
      assert.ok(creates[0].includes('--notes-file'));
      assert.ok(creates[0].includes(r.env.RC_NOTES_PATH));
      assert.ok(!creates[0].includes('--generate-notes'));
    }
  });
}
