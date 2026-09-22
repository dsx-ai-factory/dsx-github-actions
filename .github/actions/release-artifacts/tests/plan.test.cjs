// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { plan } = require('../plan.cjs');

function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rc-plan-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'service'));
  fs.writeFileSync(path.join(root, 'service/Dockerfile'), 'FROM scratch\n');
  return root;
}

const image = { name: 'example-image', context: 'service', dockerfile: 'service/Dockerfile' };
const inputs = overrides => ({
  RC_IMAGES: JSON.stringify([image]),
  RC_CHARTS: '[]',
  RC_NGC_PATH: 'example/components-dev',
  ...overrides,
});

test('plans a product image without chart dependencies', t => {
  assert.deepEqual(plan(inputs(), fixture(t)), { images: [image], charts: [] });
});

test('serializes literal Docker arguments and preserves the target without shell evaluation', t => {
  const configured = { ...image, target: 'widget-runtime', buildArgs: {
    BINARY: 'widget', FLAGS: '-X example=value -tags=a,b', EMPTY: '', LITERAL: '$(touch nope)',
  } };
  assert.deepEqual(plan(inputs({ RC_IMAGES: JSON.stringify([configured]) }), fixture(t)).images, [{
    ...image, target: 'widget-runtime',
    buildArgs: 'BINARY=widget\nFLAGS=-X example=value -tags=a,b\nEMPTY=\nLITERAL=$(touch nope)',
  }]);
  assert.deepEqual(plan(inputs({ RC_IMAGES: JSON.stringify([{ ...image, buildArgs: {} }]) }), fixture(t)).images,
    [{ ...image, buildArgs: '' }]);
});

test('rejects invalid Docker options before emitting any workflow outputs', t => {
  const root = fixture(t);
  const output = path.join(root, 'output');
  const invalid = [
    { target: null }, { target: '' }, { target: false }, { target: 'widget\n' },
    { target: '--push' }, { target: 'a'.repeat(129) }, { target: 'widget runtime' },
    { buildArgs: null }, { buildArgs: [] }, { buildArgs: 'BINARY=widget' },
    { buildArgs: Object.fromEntries(Array.from({ length: 33 }, (_, i) => [`ARG_${i}`, 'value'])) },
    { buildArgs: { BINARY: false } }, { buildArgs: { BINARY: 1 } },
    { buildArgs: { BINARY: ['widget'] } }, { buildArgs: { BINARY: null } },
    { buildArgs: { 'BINARY\n': 'widget' } }, { buildArgs: { 'A=B': 'widget' } },
    { buildArgs: { BINARY: 'widget\nINJECTED=1' } }, { buildArgs: { BINARY: 'widget\r' } },
    { buildArgs: { BINARY: 'widget\0' } }, { buildArgs: { BINARY: ' widget' } },
    { buildArgs: { BINARY: 'widget ' } }, { buildArgs: { BINARY: 'x'.repeat(4097) } },
    { buildArgs: { BINARY: 'fake-secret-never-log\n' } },
  ];
  for (const options of invalid) {
    const result = spawnSync(process.execPath, [path.resolve(__dirname, '../plan.cjs')], {
      cwd: root, encoding: 'utf8',
      env: { ...process.env, ...inputs({ RC_IMAGES: JSON.stringify([{ ...image, ...options }]) }), GITHUB_OUTPUT: output },
    });
    assert.equal(result.status, 1, JSON.stringify(options));
    assert.equal(fs.existsSync(output), false);
    assert.doesNotMatch(result.stdout + result.stderr, /fake-secret-never-log/);
  }
});

test('rejects malformed JSON without logging input content', t => {
  assert.throws(() => plan(inputs({ RC_IMAGES: '{"fake-secret-never-log"' }), fixture(t)),
    { message: 'images must be valid JSON' });
});

test('rejects invalid declarations before producing a plan', t => {
  const root = fixture(t);
  for (const overrides of [
    { RC_IMAGES: '{' },
    { RC_IMAGES: '{}' },
    { RC_IMAGES: '[]' },
    { RC_IMAGES: '[null]' },
    { RC_IMAGES: JSON.stringify([{ ...image, script: 'anything' }]) },
    { RC_IMAGES: JSON.stringify([{ ...image, name: 'image:latest' }]) },
    { RC_IMAGES: JSON.stringify([image, image]) },
    { RC_IMAGES: JSON.stringify(Array(33).fill(image)) },
    { RC_CHARTS: JSON.stringify([{ name: 'chart', path: 'service', typo: true }]) },
    { RC_NGC_PATH: 'example/../other' },
    { RC_NGC_PATH: 'https://example.invalid/team' },
  ]) {
    assert.throws(() => plan(inputs(overrides), root), JSON.stringify(overrides));
  }
});

test('rejects missing, escaped, and incorrectly typed source paths', t => {
  const root = fixture(t);
  for (const overrides of [
    { context: '../escape' },
    { context: '/tmp' },
    { context: '-option' },
    { context: 'service/Dockerfile' },
    { context: 'missing' },
    { dockerfile: 'service' },
    { dockerfile: 'service/$(echo unsafe)' },
  ]) {
    assert.throws(() => plan(inputs({ RC_IMAGES: JSON.stringify([{ ...image, ...overrides }]) }), root));
  }
  fs.symlinkSync(os.tmpdir(), path.join(root, 'outside'));
  assert.throws(() => plan(inputs({ RC_IMAGES: JSON.stringify([{ ...image, context: 'outside' }]) }), root), /escapes/);
});

test('writes bounded GitHub matrix outputs only after successful validation', t => {
  const root = fixture(t);
  const output = path.join(root, 'output');
  const command = overrides => spawnSync(process.execPath, [path.resolve(__dirname, '../plan.cjs')], {
    cwd: root,
    env: { ...process.env, ...inputs(overrides), GITHUB_OUTPUT: output },
    encoding: 'utf8',
  });
  assert.equal(command({ RC_IMAGES: '[]' }).status, 1);
  assert.equal(fs.existsSync(output), false);
  assert.equal(command({}).status, 0);
  const lines = Object.fromEntries(fs.readFileSync(output, 'utf8').trim().split('\n').map(line => {
    const index = line.indexOf('=');
    return [line.slice(0, index), line.slice(index + 1)];
  }));
  assert.deepEqual(JSON.parse(lines.images), [image]);
  assert.deepEqual(JSON.parse(lines.charts), []);
  assert.equal(lines['image-count'], '1');
  assert.equal(lines['chart-count'], '0');
});
