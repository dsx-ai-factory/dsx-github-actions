// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { analyzeCommits } from '@semantic-release/commit-analyzer';
import { generateNotes } from '@semantic-release/release-notes-generator';
import semver from 'semver';
import { releaseTarget, validateVersion } from './validate-version.cjs';
import { releaseNotesOptions } from './config.cjs';

const logger = { log: (...args) => console.error(...args) };
const parserOptions = {
  ...releaseNotesOptions,
  parserOpts: {
    revertPattern: /^(?:Revert|revert:|Reapply)\s"?[\s\S]+?"?\s*This reverts commit (\w*)\./i,
    revertCorrespondence: ['hash'],
  },
};

export function git(cwd, ...args) {
  return execFileSync('git', args, {
    cwd, encoding: 'utf8', timeout: 30_000, maxBuffer: 16 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trimEnd();
}

function ancestor(cwd, base, head) {
  try {
    git(cwd, 'merge-base', '--is-ancestor', base, head);
    return true;
  } catch (error) {
    if (error.status === 1) return false;
    throw error;
  }
}

function commitsSince(cwd, base, head) {
  const fields = git(cwd, 'log', '-z', '--format=%H%x00%B', ...(base ? [`${base}..${head}`] : [head]), '--').split('\0');
  const commits = [];
  for (let i = 0; i + 1 < fields.length; i += 2) {
    commits.push({ hash: fields[i], message: fields[i + 1] });
  }
  return commits;
}

function releaseTags(cwd) {
  // Remote refs are authoritative. A stale or locally fabricated tag cannot allocate a version.
  const refs = git(cwd, 'ls-remote', '--tags', 'origin');
  const objects = new Map(refs.split('\n').filter(Boolean).map((line) => {
    const [sha, ref] = line.split('\t');
    return [ref, sha];
  }));
  return [...objects].flatMap(([ref, object]) => {
    if (!ref.startsWith('refs/tags/v') || ref.endsWith('^{}')) return [];
    const version = ref.slice('refs/tags/v'.length);
    if (semver.valid(version) !== version || semver.parse(version).build.length) return [];
    const sha = objects.get(`${ref}^{}`) || object;
    if (!/^[0-9a-f]{40,64}$/.test(sha)) throw new Error('Invalid remote tag object');
    if (git(cwd, 'rev-parse', '--verify', `${ref}^{commit}`) !== sha) {
      throw new Error(`Local and remote tag disagree: ${ref}`);
    }
    return [{ tag: ref.slice('refs/tags/'.length), version, sha }];
  }).sort((a, b) => semver.rcompare(a.version, b.version));
}

export async function planRelease({ cwd, branch, sha, repositoryUrl }) {
  const target = releaseTarget(branch);
  if (!/^[0-9a-f]{40,64}$/.test(sha) || git(cwd, 'rev-parse', 'HEAD') !== sha) {
    throw new Error('RC source commit does not match this workflow run');
  }
  if (git(cwd, 'rev-parse', '--is-shallow-repository') !== 'false') {
    throw new Error('RC publishing requires full source history');
  }
  const remoteHead = git(cwd, 'ls-remote', '--exit-code', 'origin', `refs/heads/${branch}`).split('\t')[0];
  if (remoteHead !== sha) throw new Error('This run is not the current release-branch head');
  git(cwd, 'fetch', '--tags', '--no-recurse-submodules', 'origin');
  const tags = releaseTags(cwd);
  const candidates = tags.filter(({ version }) => {
    try { validateVersion(branch, version); return true; } catch { return false; }
  });
  const previous = candidates[0];
  if (previous && !ancestor(cwd, previous.sha, sha)) {
    throw new Error('The latest target-line RC is not an ancestor; repair the release branch history');
  }
  for (let i = 1; i < candidates.length; i++) {
    if (!ancestor(cwd, candidates[i].sha, candidates[i - 1].sha)) {
      throw new Error('RC sequence does not follow commit ancestry; repair the release branch history');
    }
  }
  if (previous) {
    const ref = `refs/notes/semantic-release-${previous.tag}`;
    try {
      git(cwd, 'fetch', '--no-tags', 'origin', `+${ref}:${ref}`);
      const note = JSON.parse(git(cwd, 'notes', `--ref=${ref}`, 'show', previous.tag));
      if (!Array.isArray(note.channels) || !note.channels.includes('rc')) throw new Error('missing rc channel');
    } catch (error) {
      throw new Error(`Latest RC has no valid channel metadata; repair ${previous.tag} before publishing`, { cause: error });
    }
  }
  const stable = tags.filter(({ version }) => !semver.prerelease(version));
  const baseline = stable.find(({ version }) => semver.lt(version, target));
  const context = { cwd: fileURLToPath(new URL('.', import.meta.url)), logger };
  const notesFor = (version, commits, lastTag) => generateNotes(parserOptions, {
    ...context, options: { repositoryUrl }, commits,
    lastRelease: { gitTag: lastTag },
    nextRelease: { version, gitTag: `v${version}`, gitHead: sha },
  });
  const existing = candidates.filter((tag) => tag.sha === sha);
  if (existing.length > 1) throw new Error('Multiple target-line RC tags point at this commit');
  if (existing.length === 1) {
    if (existing[0].tag !== previous.tag) throw new Error('Only the latest target-line RC can be reused');
    const last = candidates[1] || baseline;
    const commits = commitsSince(cwd, last?.sha, sha);
    return { ...existing[0], reused: true, target, baseline: baseline || null, previous: last || null,
      commits, notes: await notesFor(existing[0].version, commits, last?.tag) };
  }
  if (tags.some(({ version }) => version === target)) {
    throw new Error(`${target} is already stable; use a new release branch`);
  }
  if (!baseline && (stable.length || target !== '1.0.0')) {
    throw new Error('A preceding stable release is required (first releases must target 1.0.0)');
  }
  if (baseline) git(cwd, 'merge-base', baseline.sha, sha);
  const commits = commitsSince(cwd, previous?.sha || baseline?.sha, sha);
  const type = await analyzeCommits(parserOptions, { ...context, commits });
  if (!type) return null;
  // Include the full release's changes so an RC fix doesn't erase its original minor/major bump.
  const releaseType = previous
    ? await analyzeCommits(parserOptions, { ...context, commits: commitsSince(cwd, baseline?.sha, sha) })
    : type;
  if (!releaseType) return null;
  const expected = baseline ? semver.inc(baseline.version, releaseType) : '1.0.0';
  if (target !== expected) {
    throw new Error(`Calculated release ${expected} does not match release branch target ${target}`);
  }
  const sequence = previous ? Number(semver.prerelease(previous.version)[1]) + 1 : 1;
  if (!Number.isSafeInteger(sequence)) throw new Error('RC sequence exceeds the supported integer range');
  const version = `${target}-rc.${sequence}`;
  validateVersion(branch, version);
  const tag = `v${version}`;
  const notes = await notesFor(version, commits, previous?.tag || baseline?.tag);
  logger.log('RC plan: baseline=%s previous=%s type=%s target=%s tag=%s source=%s',
    baseline?.tag || 'first-release', previous?.tag || 'none', releaseType, target, tag, sha);
  return { version, tag, sha, target, reused: false, baseline: baseline || null,
    previous: previous || null, commits, notes };
}

export function publishTag(cwd, plan) {
  if (!plan || plan.reused) return;
  validateVersion(`release/${plan.target}`, plan.version);
  if (plan.tag !== `v${plan.version}` || git(cwd, 'rev-parse', 'HEAD') !== plan.sha) {
    throw new Error('Invalid RC publication plan');
  }
  const refs = git(cwd, 'ls-remote', 'origin', `refs/heads/release/${plan.target}`, `refs/tags/v${plan.target}`);
  if (refs.includes(`refs/tags/v${plan.target}`) ||
      !refs.split('\n').includes(`${plan.sha}\trefs/heads/release/${plan.target}`)) {
    throw new Error('Release branch or stable tag changed before publishing');
  }
  git(cwd, '-c', 'tag.gpgSign=false', 'tag', plan.tag, plan.sha);
  const note = `refs/notes/semantic-release-${plan.tag}`;
  git(cwd, '-c', 'user.name=semantic-release-bot', '-c', 'user.email=semantic-release-bot@martynus.net',
    '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
    'notes', `--ref=${note}`, 'add', '-m', JSON.stringify({ channels: ['rc'] }), plan.tag);
  git(cwd, 'push', '--atomic', 'origin', `refs/tags/${plan.tag}`, note);
}
