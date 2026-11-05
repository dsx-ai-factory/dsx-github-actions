// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { planRelease, publishTag } from './plan.mjs';
import { repositoryUrl } from './config.cjs';

const dryRun = process.argv[2] === '--dry-run';
if (!dryRun && process.argv[2] !== '--publish') throw new Error('Use --dry-run or --publish');
const { GITHUB_REF_NAME: branch, GITHUB_SHA: sha, GITHUB_REPOSITORY: repository,
  GITHUB_OUTPUT: output, RC_PLAN_PATH: planPath, RC_NOTES_PATH: notesPath } = process.env;
if (!output || !planPath || !notesPath) throw new Error('GITHUB_OUTPUT, RC_PLAN_PATH and RC_NOTES_PATH are required');
const cwd = process.cwd();
const plan = await planRelease({ cwd, branch, sha, repositoryUrl: repositoryUrl(repository) });
if (dryRun) {
  writeFileSync(planPath, JSON.stringify(plan), { flag: 'wx', mode: 0o600 });
  writeFileSync(notesPath, plan?.notes || '', { flag: 'wx', mode: 0o600 });
} else {
  const expected = JSON.parse(readFileSync(planPath, 'utf8'));
  const identity = (value) => value && JSON.stringify({
    version: value.version, sha: value.sha, reused: value.reused,
    baseline: value.baseline, previous: value.previous,
  });
  if (!expected || expected.reused || identity(expected) !== identity(plan)) {
    throw new Error('RC plan changed since preview; rerun the latest branch head');
  }
  publishTag(cwd, plan);
}
appendFileSync(output, [
  `should-publish=${Boolean(plan)}`,
  `version=${plan?.version || ''}`,
  `tag=${plan?.tag || ''}`,
  `reused-existing-tag=${Boolean(plan?.reused)}`,
  '',
].join('\n'));
