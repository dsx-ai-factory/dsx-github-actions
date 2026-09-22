// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../..');
const scripts = path.join(root, '.github/scripts');
const buildScripts = path.join(root, '.github/actions/docker-build/scripts');
const image = 'ghcr.io/example/app';
const fakeDigest = `sha256:${'f'.repeat(64)}`;

function fixture(t, architectures = ['amd64', 'arm64'], plain = false) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'image-attest-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const layout = path.join(dir, 'oci');
  const reports = path.join(dir, 'reports');
  const bin = path.join(dir, 'bin');
  for (const p of [path.join(layout, 'blobs/sha256'), reports, bin]) fs.mkdirSync(p, { recursive: true });
  const write = (name, value) => fs.writeFileSync(name, JSON.stringify(value));
  function blob(value) {
    const data = JSON.stringify(value);
    const digest = `sha256:${createHash('sha256').update(data).digest('hex')}`;
    fs.writeFileSync(path.join(layout, 'blobs/sha256', digest.slice(7)), data);
    return digest;
  }
  const entries = architectures.map(arch => {
    const config = { os: 'linux', architecture: arch, ...(arch === 'arm64' ? { variant: 'v8' } : {}) };
    const digest = blob({ schemaVersion: 2, config: { digest: blob(config) }, layers: [] });
    return { digest, platform: { os: 'linux', architecture: arch, ...(config.variant ? { variant: config.variant } : {}) } };
  });
  const manifest = plain
    ? JSON.parse(fs.readFileSync(path.join(layout, 'blobs/sha256', entries[0].digest.slice(7))))
    : { schemaVersion: 2, manifests: [...entries, {
        digest: fakeDigest, platform: { os: 'unknown', architecture: 'unknown' },
        annotations: { 'vnd.docker.reference.type': 'attestation-manifest' },
      }] };
  const digest = blob(manifest);
  write(path.join(layout, 'index.json'), { manifests: [{ digest }, { digest }] });
  write(path.join(dir, 'manifest.json'), manifest);
  const platforms = entries.map(entry => ({ platform: `linux/${entry.platform.architecture}`, digest: entry.digest }));
  const metadata = { digest, platforms };
  write(path.join(dir, 'expected.json'), metadata);
  fs.writeFileSync(path.join(bin, 'docker'), `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.COMMANDS, JSON.stringify(args) + '\\n');
const metadata = JSON.parse(fs.readFileSync(process.env.EXPECTED));
if (args[0] === 'buildx') {
  if (args.includes('--raw')) process.stdout.write(fs.readFileSync(process.env.MANIFEST));
  else if (args.includes('--format')) {
    const format = args[args.indexOf('--format') + 1];
    if (format === '{{json .Manifest}}') process.stdout.write(JSON.stringify({digest: process.env.PROMOTED_DIGEST || metadata.digest}));
    else process.stdout.write('Name: fixture\\nDigest: ' + metadata.digest);
  }
  process.exit(0);
}
const reportVolume = args.find(a => a.endsWith(':/reports'));
const reportDir = reportVolume.slice(0, -':/reports'.length);
if (args.includes('syft-test')) {
  const platform = args[args.indexOf('--platform') + 1];
  const key = platform.replaceAll('/', '-');
  const digest = process.env.WRONG_SYFT_DIGEST || metadata.platforms.find(p => p.platform === platform).digest;
  fs.writeFileSync(path.join(reportDir, key + '.syft.json'), JSON.stringify({source: {metadata: {manifestDigest: digest}}}));
  fs.writeFileSync(path.join(reportDir, key + '.spdx.json'), JSON.stringify({spdxVersion: 'SPDX-2.3', SPDXID: 'SPDXRef-DOCUMENT'}));
} else if (args.includes('grype-test')) {
  const source = args.find(a => a.startsWith('sbom:/reports/'));
  const key = path.basename(source).replace('.syft.json', '');
  const syft = JSON.parse(fs.readFileSync(path.join(reportDir, key + '.syft.json')));
  const rc = Number(process.env.GRYPE_EXIT || 0);
  fs.writeFileSync(path.join(reportDir, key + '.grype.json'), JSON.stringify({
    source: {target: {manifestDigest: syft.source.metadata.manifestDigest}},
    matches: rc === 2 ? [{vulnerability: {severity: 'Critical'}}] : []
  }));
  for (const suffix of ['.sarif', '.txt']) fs.writeFileSync(path.join(reportDir, key + suffix), 'report');
  process.exit(rc);
} else process.exit(1);
`, { mode: 0o755 });
  const env = {
    PATH: `${bin}${path.delimiter}${process.env.PATH}`,
    RUNNER_TEMP: dir, SCAN_CACHE: path.join(dir, 'grype-cache'),
    OCI_LAYOUT: layout, EXPECTED_DIGEST: digest, PLATFORMS: platforms.map(p => p.platform).join(','),
    REPORTS: reports, SYFT_IMAGE: 'syft-test', GRYPE_IMAGE: 'grype-test',
    SCAN_REPORTS: reports, SCAN_DIGEST: digest,
    GITHUB_OUTPUT: path.join(dir, 'outputs'), GITHUB_STEP_SUMMARY: path.join(dir, 'summary'),
    IMAGE: image, IMAGE_TAGS: `${image}:v1\n${image}:latest`,
    COMMANDS: path.join(dir, 'commands'), EXPECTED: path.join(dir, 'expected.json'),
    MANIFEST: path.join(dir, 'manifest.json'),
  };
  const run = (script, overrides = {}) => spawnSync('bash', [script], { env: { ...env, ...overrides }, encoding: 'utf8' });
  const commands = () => fs.existsSync(env.COMMANDS)
    ? fs.readFileSync(env.COMMANDS, 'utf8').trim().split('\n').map(JSON.parse) : [];
  const generate = () => {
    const result = run(path.join(buildScripts, 'sbom-oci.sh'));
    assert.equal(result.status, 0, result.stderr);
  };
  const prepare = (overrides = {}) => run(path.join(scripts, 'prepare-image-attestation.sh'), overrides);
  const matrix = () => JSON.parse(fs.readFileSync(env.GITHUB_OUTPUT, 'utf8').trim().split('matrix=')[1]).include;
  return { dir, reports, env, metadata, manifest, write, run, commands, generate, prepare, matrix };
}

test('SBOM generation works without running Grype', t => {
  const f = fixture(t);
  f.generate();
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.reports, 'image.json'))), f.metadata);
  assert.equal(f.commands().filter(c => c.includes('syft-test')).length, 2);
  assert.equal(f.commands().filter(c => c.includes('grype-test')).length, 0);
});

for (const [name, arches, plain, subjects] of [
  ['single manifest', ['amd64'], true, 1],
  ['single-platform index', ['arm64'], false, 2],
  ['multi-platform index with BuildKit metadata', ['amd64', 'arm64'], false, 3],
]) {
  test(`${name}: SBOMs bind to children, provenance includes the root once`, t => {
    const f = fixture(t, arches, plain);
    f.generate();
    const result = f.prepare();
    assert.equal(result.status, 0, result.stderr);
    const matrix = f.matrix();
    assert.equal(matrix.length, subjects);
    assert.equal(matrix.filter(row => row.digest === f.metadata.digest).length, 1);
    for (const p of f.metadata.platforms) {
      const row = matrix.find(row => row.platform === p.platform);
      assert.equal(row.digest, p.digest);
      assert.equal(row.sbom, `${p.platform.replaceAll('/', '-')}.spdx.json`);
    }
  });
}

test('arm64/v8 input is normalized consistently', t => {
  const f = fixture(t, ['arm64']);
  f.generate();
  assert.equal(f.prepare({ PLATFORMS: 'linux/arm64/v8' }).status, 0);
});

for (const [name, change] of [
  ['missing platform', f => { f.env.PLATFORMS += ',linux/arm/v7'; }],
  ['unexpected platform', f => { f.env.PLATFORMS = 'linux/amd64'; }],
  ['duplicate platform', f => { f.env.PLATFORMS += ',linux/amd64'; }],
  ['wrong root digest', f => { f.env.EXPECTED_DIGEST = fakeDigest; }],
  ['different registry child digest', f => { f.manifest.manifests[0].digest = fakeDigest; f.write(f.env.MANIFEST, f.manifest); }],
  ['wrong SBOM subject', f => { f.write(path.join(f.reports, 'linux-amd64.syft.json'), {source: {metadata: {manifestDigest: fakeDigest}}}); }],
  ['missing SPDX file', f => { fs.unlinkSync(path.join(f.reports, 'linux-amd64.spdx.json')); }],
  ['malformed SPDX file', f => { f.write(path.join(f.reports, 'linux-amd64.spdx.json'), {}); }],
  ['empty platform evidence', f => { f.write(path.join(f.reports, 'image.json'), {digest: f.metadata.digest, platforms: []}); }],
]) {
  test(`pre-signing validation rejects ${name}`, t => {
    const f = fixture(t);
    f.generate();
    change(f);
    assert.notEqual(f.prepare().status, 0);
    assert.ok(!fs.existsSync(f.env.GITHUB_OUTPUT));
  });
}

test('SBOM generation rejects an incomplete OCI export before invoking Syft', t => {
  const f = fixture(t, ['amd64']);
  const result = f.run(path.join(buildScripts, 'sbom-oci.sh'), { PLATFORMS: 'linux/amd64,linux/arm64' });
  assert.notEqual(result.status, 0);
  assert.equal(f.commands().length, 0);
});

test('SBOM generation rejects a tool result for a different digest', t => {
  const f = fixture(t);
  assert.notEqual(f.run(path.join(buildScripts, 'sbom-oci.sh'), { WRONG_SYFT_DIGEST: fakeDigest }).status, 0);
  assert.ok(!fs.existsSync(path.join(f.reports, 'image.json')));
});

for (const [name, rc, critical, success] of [
  ['clean', '0', 'true', true],
  ['critical blocked', '2', 'true', false],
  ['critical allowed', '2', 'false', true],
  ['tool failure always blocks', '1', 'false', false],
]) {
  test(`scan policy remains unchanged: ${name}`, t => {
    const f = fixture(t);
    f.generate();
    const result = f.run(path.join(buildScripts, 'scan-oci.sh'), { GRYPE_EXIT: rc, SCAN_FAIL_ON_CRITICAL: critical });
    assert.equal(result.status === 0, success, result.stderr);
    assert.equal(f.commands().filter(c => c.includes('syft-test')).length, 2, 'scan must reuse SBOMs');
    if (rc !== '1') assert.equal(f.commands().filter(c => c.includes('grype-test')).length, 2);
  });
}

test('promotion preserves single-manifest digests', t => {
  const f = fixture(t, ['amd64'], true);
  const result = f.run(path.join(scripts, 'promote-attested-image.sh'));
  assert.equal(result.status, 0, result.stderr);
  const create = f.commands().find(c => c.includes('create'));
  assert.ok(create.includes('--prefer-index=false'));
  assert.equal(create.at(-1), `${image}@${f.metadata.digest}`);
  assert.equal(f.commands().filter(c => c.includes('inspect')).length, 2);
});

test('all release tags are validated before the first registry write', t => {
  const f = fixture(t);
  const result = f.run(path.join(scripts, 'promote-attested-image.sh'), { IMAGE_TAGS: `${image}:v1\nghcr.io/other/app:latest` });
  assert.notEqual(result.status, 0);
  assert.equal(f.commands().length, 0);
});

test('promotion fails if a release tag resolves to another digest', t => {
  const f = fixture(t);
  assert.notEqual(f.run(path.join(scripts, 'promote-attested-image.sh'), { PROMOTED_DIGEST: fakeDigest }).status, 0);
});
