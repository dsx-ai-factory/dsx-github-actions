// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { generateNotes } from '@semantic-release/release-notes-generator';
import { releaseNotesOptions, repositoryUrl } from '../release-candidate/config.cjs';

const input = JSON.parse(readFileSync(0, 'utf8'));
const repository = repositoryUrl(input.repository).replace(/\.git$/, '');
const notes = await generateNotes({
  ...releaseNotesOptions,
  parserOpts: {
    // Git's Reapply subject differs from the reverted commit's header; match its hash.
    revertPattern: /^(?:Revert|revert:|Reapply)\s"?[\s\S]+?"?\s*This reverts commit (\w*)\./i,
    revertCorrespondence: ['hash'],
  },
}, {
  cwd: fileURLToPath(new URL('.', import.meta.url)),
  options: { repositoryUrl: repository },
  commits: input.commits,
  lastRelease: { gitTag: input.previousTag },
  nextRelease: {
    version: input.version,
    gitTag: `v${input.version}`,
    gitHead: input.commit,
  },
});
// A merge revert can undo branch commits as well as its target merge. Hash-only
// cancellation cannot determine that set, so retain an explicit warning instead.
const mergeReverts = input.commits.flatMap(({ hash, message }) => {
  const match = /^This reverts commit ([0-9a-f]{40}), reversing\r?\nchanges made to [0-9a-f]{40}\./m.exec(message);
  if (!match) return [];
  const merge = match[1];
  return [`* [${hash.slice(0, 7)}](${repository}/commit/${hash}) (reverts merge [${merge.slice(0, 7)}](${repository}/commit/${merge}))`];
});
const warning = mergeReverts.length ? [
  '\n### Merge revert warning\n',
  'This release history includes merge reverts. Features or fixes listed above may have been undone; review these revert commits:\n',
  ...mergeReverts,
  '',
].join('\n') : '';
process.stdout.write(notes + warning);
