// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { test } from 'node:test';

function notes(messages, previousTag = 'v1.14.0') {
  return execFileSync(process.execPath, [new URL('../release-notes.mjs', import.meta.url).pathname], {
    input: JSON.stringify({
      repository: 'example/product', version: '1.15.0', previousTag,
      commit: 'f'.repeat(40),
      commits: messages.map((message, index) => ({
        hash: index.toString(16).padStart(40, 'a'), message,
      })),
    }),
    encoding: 'utf8',
  });
}

test('renders Conventional Commits, breaking changes, and stable comparison links', () => {
  const result = notes([
    'feat(identity): add workload support (#42)',
    'fix(auth): refresh expired credentials',
    'feat!: require new configuration\n\nBREAKING CHANGE: replace the legacy config.',
    'docs: internal documentation update',
    'chore: release preparation',
  ]);
  for (const text of ['Features', 'Bug Fixes', 'BREAKING CHANGES', 'add workload support',
    'refresh expired credentials', 'replace the legacy config.',
    'https://github.com/example/product/compare/v1.14.0...v1.15.0',
    'https://github.com/example/product/issues/42', 'https://github.com/example/product/commit/']) {
    assert.ok(result.includes(text), `missing ${text}: ${result}`);
  }
  assert.ok(!result.includes('internal documentation update'));
  assert.ok(!result.includes('release preparation'));
});

test('first release and empty visible changes still have a stable version heading', () => {
  assert.match(notes(['feat: initial feature'], null), /initial feature/);
  assert.match(notes(['chore: metadata only']), /1\.15\.0/);
});

test('omits reverted features and fixes while keeping unrelated changes', () => {
  for (const type of ['feat', 'fix']) {
    for (const previousTag of ['v1.14.0', null]) {
      const original = `${type}: temporary change`;
      const result = notes([
        `Revert "${original}"\n\nThis reverts commit ${'1'.padStart(40, 'a')}.`,
        original,
        'feat: retained feature',
        'fix: retained fix',
      ], previousTag);
      assert.ok(!result.includes('temporary change'), result);
      assert.ok(result.includes('retained feature'), result);
      assert.ok(result.includes('retained fix'), result);
    }
  }
});

test('keeps a revert when the original change is outside the release range', () => {
  const result = notes([
    `Revert "feat: previously released feature"\n\nThis reverts commit ${'b'.repeat(40)}.`,
    'fix: retained fix',
  ]);
  assert.ok(result.includes('### Reverts'), result);
  assert.ok(result.includes('previously released feature'), result);
  assert.ok(result.includes('retained fix'), result);
});

test('keeps features and fixes that are reverted and reapplied', () => {
  for (const type of ['feat', 'fix']) {
    for (const previousTag of ['v1.14.0', null]) {
      const original = `${type}: restored change`;
      const result = notes([
        `Reapply "${original}"\n\nThis reverts commit ${'1'.padStart(40, 'a')}.`,
        `Revert "${original}"\n\nThis reverts commit ${'2'.padStart(40, 'a')}.`,
        original,
        'fix: retained fix',
      ], previousTag);
      assert.ok(result.includes('restored change'), result);
      assert.ok(result.includes('retained fix'), result);
      assert.ok(!result.includes('### Reverts'), result);
    }
  }
});

test('omits a reapplied feature when it is reverted again', () => {
  const result = notes([
    `Revert "Reapply "feat: temporary change""\n\nThis reverts commit ${'1'.padStart(40, 'a')}.`,
    `Reapply "feat: temporary change"\n\nThis reverts commit ${'2'.padStart(40, 'a')}.`,
    `Revert "feat: temporary change"\n\nThis reverts commit ${'3'.padStart(40, 'a')}.`,
    'feat: temporary change',
    'fix: retained fix',
  ]);
  assert.ok(!result.includes('temporary change'), result);
  assert.ok(result.includes('retained fix'), result);
});

test('keeps standalone reapplications and conventional revert messages', () => {
  for (const prefix of ['Reapply', 'revert:']) {
    const result = notes([
      `${prefix} "feat: previously released feature"\n\nThis reverts commit ${'b'.repeat(40)}.`,
    ]);
    assert.ok(result.includes('### Reverts'), result);
    assert.ok(result.includes('previously released feature'), result);
  }
});
