// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { execFileSync } from 'node:child_process';
import { realpath } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { planRelease, publishTag } from '../../plan.mjs';

const [repositoryUrl, branch, sha, mode] = process.argv.slice(2);
const cwd = process.cwd();
const remote = await realpath(fileURLToPath(repositoryUrl));
if (remote !== await realpath(resolve(cwd, '../origin.git'))) {
  throw new Error('Only the fixture sibling bare origin is allowed');
}
for (const args of [['remote', 'get-url', 'origin'], ['remote', 'get-url', '--push', 'origin']]) {
  const url = execFileSync('git', args, { cwd, encoding: 'utf8', timeout: 5_000 }).trim();
  if (await realpath(fileURLToPath(url)) !== remote) {
    throw new Error('The fixture origin must use the sibling bare repository');
  }
}
if (!['plan', 'publish'].includes(mode)) throw new Error('Expected plan or publish');

try {
  const plan = await planRelease({ cwd, branch, sha, repositoryUrl });
  if (mode === 'publish') await publishTag(cwd, plan);
  process.stdout.write(JSON.stringify(plan));
} catch (error) {
  process.stdout.write(JSON.stringify({ error: error.message }));
  process.exitCode = 1;
}
