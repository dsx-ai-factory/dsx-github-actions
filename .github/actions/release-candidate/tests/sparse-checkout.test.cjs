// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { access, mkdir, readFile, writeFile } = require('node:fs/promises');
const { join } = require('node:path');
const { test } = require('node:test');
const { createConfig } = require('../config.cjs');
const { createRepository } = require('./helpers/repository.cjs');

for (const defaultBranch of ['master', 'main']) {
  test(`non-cone /.github/ checkout isolates consumer config on ${defaultBranch}`, async (t) => {
    const target = '1.3.0';
    const repository = await createRepository(t, { defaultBranch, target });
    const { work, git, remoteGit, commit, run, release, branch, assertTags } = repository;
    const consumerPackage = JSON.stringify({
      name: 'consumer-fixture',
      private: true,
      release: { extends: './consumer-package-config-must-not-load.cjs' },
    });
    const consumerConfig = JSON.stringify({
      extends: './consumer-release-config-must-not-load.cjs',
      branches: ['consumer-only'],
      tagFormat: 'consumer-${version}',
    });
    await mkdir(join(work, '.github'));
    await writeFile(join(work, '.github/fixture.txt'), 'sparse checkout fixture\n');
    await writeFile(join(work, 'package.json'), consumerPackage);
    await writeFile(join(work, '.releaserc.json'), consumerConfig);
    await git('add', '.github/fixture.txt', 'package.json', '.releaserc.json');
    const stable = await commit('fix: establish stable release');
    await git('tag', 'v1.2.9');
    await git('push', '--set-upstream', 'origin', defaultBranch, '--tags');
    await git('switch', '--create', branch);
    const head = await commit('feat: add sparse release support');
    await git('push', '--set-upstream', 'origin', branch);
    const tree = await git('rev-parse', 'HEAD^{tree}');
    const indexEntries = await git('ls-files', '--stage');

    await git('sparse-checkout', 'set', '--no-cone', '/.github/');
    assert.equal(await git('config', '--get', 'core.sparseCheckoutCone'), 'false');
    assert.equal(await readFile(join(work, '.git/info/sparse-checkout'), 'utf8'), '/.github/\n');
    await access(join(work, '.github/fixture.txt'));
    for (const file of ['package.json', '.releaserc.json']) {
      await assert.rejects(access(join(work, file)), { code: 'ENOENT' });
    }

    const prepare = await run(process.execPath, [join(__dirname, '../config.cjs')], {
      cwd: work,
      extraEnv: {
        GITHUB_REF_NAME: branch,
        RC_DEFAULT_BRANCH: defaultBranch,
        GITHUB_REPOSITORY: 'example/consumer',
      },
    });
    assert.equal(prepare.status, 0, prepare.stderr);
    const generated = JSON.parse(await readFile(join(work, '.releaserc.json'), 'utf8'));
    assert.deepEqual(generated, {
      ...createConfig(branch, defaultBranch),
      repositoryUrl: 'https://github.com/example/consumer.git',
    });

    const result = await release();
    assert.equal(result.nextRelease.version, `${target}-rc.1`);
    assert.equal(result.nextRelease.gitTag, `v${target}-rc.1`);
    assert.equal(result.nextRelease.channel, 'rc');
    assert.match(result.nextRelease.notes, /add sparse release support/);
    await assertTags({ 'v1.2.9': stable, [`v${target}-rc.1`]: head });

    // The disposable worktree config must not alter the tracked consumer blobs.
    assert.equal(await git('rev-parse', 'HEAD^{tree}'), tree);
    assert.equal(await git('ls-files', '--stage'), indexEntries);
    assert.equal(await git('diff', '--cached', '--exit-code'), '');
    for (const [file, content] of [['package.json', consumerPackage], ['.releaserc.json', consumerConfig]]) {
      assert.equal(await git('show', `HEAD:${file}`), content);
      assert.equal(await remoteGit('show', `${branch}:${file}`), content);
    }
    await assert.rejects(access(join(work, 'package.json')), { code: 'ENOENT' });
  });
}
