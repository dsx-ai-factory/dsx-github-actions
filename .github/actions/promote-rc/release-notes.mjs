// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { generateNotes } from '@semantic-release/release-notes-generator';
import { releaseNotesOptions, repositoryUrl } from '../release-candidate/config.cjs';

const input = JSON.parse(readFileSync(0, 'utf8'));
const notes = await generateNotes(releaseNotesOptions, {
  cwd: fileURLToPath(new URL('.', import.meta.url)),
  options: { repositoryUrl: repositoryUrl(input.repository) },
  commits: input.commits,
  lastRelease: { gitTag: input.previousTag },
  nextRelease: {
    version: input.version,
    gitTag: `v${input.version}`,
    gitHead: input.commit,
  },
});
process.stdout.write(notes);
