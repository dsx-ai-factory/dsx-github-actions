// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { appendFile, mkdir } = require('node:fs/promises');
const { join } = require('node:path');
const { pathToFileURL } = require('node:url');
const { createFixture } = require('./fixture.cjs');

async function createRepository(t, { defaultBranch, target }) {
  const fixture = await createFixture(t);
  const work = join(fixture.root, 'work');
  const remote = join(fixture.root, 'origin.git');
  const repositoryUrl = pathToFileURL(remote).href;
  const branch = `release/${target}`;
  await mkdir(work);
  await fixture.git(fixture.root, 'init', '--bare', `--initial-branch=${defaultBranch}`, '--template=', remote);
  await fixture.git(work, 'init', `--initial-branch=${defaultBranch}`, '--template=');

  // Generated repositories have no inherited hooks, helpers, signing, or submodules.
  const gitConfig = `
[user]
  name = RC Test
  email = rc-test@example.invalid
[core]
  hooksPath = /dev/null
[credential]
  helper =
[commit]
  gpgSign = false
[tag]
  gpgSign = false
[protocol]
  allow = never
[protocol "file"]
  allow = always
[submodule]
  recurse = false
[fetch]
  recurseSubmodules = false
[push]
  recurseSubmodules = no
[gc]
  auto = 0
[maintenance]
  auto = false
`;
  await appendFile(join(work, '.git/config'), gitConfig);
  await appendFile(join(remote, 'config'), gitConfig);
  const git = (...args) => fixture.git(work, ...args);
  const remoteGit = (...args) => fixture.git(remote, ...args);
  await git('remote', 'add', 'origin', repositoryUrl);

  async function commit(message) {
    await git('commit', '--allow-empty', '-m', message);
    return git('rev-parse', 'HEAD');
  }

  function runRelease(dryRun = false, extraEnv = {}) {
    return fixture.run(process.execPath, [
      join(__dirname, 'release.mjs'), repositoryUrl, branch, defaultBranch, String(dryRun),
    ], { cwd: work, extraEnv, timeout: 30_000 });
  }

  async function release(dryRun = false) {
    const result = await runRelease(dryRun);
    assert.equal(result.signal, null, result.stderr);
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  }

  function verifyExistingTag(version, sha, extraEnv = {}) {
    return fixture.run(fixture.bash, [join(__dirname, '../../verify-existing-tag.sh')], {
      cwd: work,
      extraEnv: {
        RC_TAG: `v${version}`,
        RC_VERSION: version,
        GITHUB_SHA: sha,
        GITHUB_REF_NAME: branch,
        ...extraEnv,
      },
      timeout: 10_000,
    });
  }

  async function assertTags(expected) {
    for (const execute of [git, remoteGit]) {
      const refs = await execute('for-each-ref', '--format=%(refname:short) %(objectname)', 'refs/tags');
      const observed = Object.fromEntries(refs.split('\n').filter(Boolean).map((ref) => ref.split(' ')));
      assert.deepEqual(observed, expected);
    }
  }

  return { ...fixture, work, remote, repositoryUrl, branch, git, remoteGit, commit,
    runRelease, release, verifyExistingTag, assertTags };
}

module.exports = { createRepository };
