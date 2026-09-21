// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, realpath } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import semanticRelease from 'semantic-release';

const require = createRequire(import.meta.url);
const validator = require.resolve('../../validate-version.cjs');
const dependencyRequire = createRequire(require.resolve('semantic-release'));
const localPlugins = new Set([
  '@semantic-release/commit-analyzer',
  '@semantic-release/release-notes-generator',
]);

const [repositoryUrl, branch, defaultBranch, dryRun] = process.argv.slice(2);
const remote = fileURLToPath(repositoryUrl);
if (await realpath(remote) !== await realpath(resolve(process.cwd(), '../origin.git'))) {
  throw new Error('Only the fixture sibling bare origin is allowed');
}
const configPath = join(process.cwd(), '.releaserc.json');
if (!existsSync(configPath)) {
  execFileSync(process.execPath, [require.resolve('../../config.cjs')], {
    env: {
      ...process.env,
      GITHUB_REF_NAME: branch,
      RC_DEFAULT_BRANCH: defaultBranch,
      GITHUB_REPOSITORY: 'example/consumer',
    },
    stdio: 'pipe',
  });
}
const config = JSON.parse(await readFile(configPath, 'utf8'));

// Exercise normal config discovery; only plugin resolution and publishing differ.
const plugins = config.plugins
  .filter((entry) => (Array.isArray(entry) ? entry[0] : entry) !== '@semantic-release/github')
  .map((entry) => {
    const name = Array.isArray(entry) ? entry[0] : entry;
    if (name === validator) return entry;
    if (!localPlugins.has(name)) throw new Error(`Not a local fixture plugin: ${name}`);
    const resolved = dependencyRequire.resolve(name);
    return Array.isArray(entry) ? [resolved, ...entry.slice(1)] : resolved;
  });

try {
  const result = await semanticRelease(
    { plugins, repositoryUrl, ci: false, dryRun: dryRun === 'true' },
    { cwd: process.cwd(), env: process.env, stdout: process.stderr, stderr: process.stderr },
  );
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  const errors = error.errors || [error];
  process.stdout.write(JSON.stringify({
    errors: errors.map(({ message, pluginName }) => ({ message, pluginName })),
  }));
  process.exitCode = 1;
}
