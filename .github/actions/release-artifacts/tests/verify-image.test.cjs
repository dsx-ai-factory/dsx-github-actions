// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { dirname, join } = require('node:path');
const { test } = require('node:test');

const script = join(__dirname, '../verify-image.sh');
const imageRef = 'registry.invalid/team/service:v1.2.3-rc.1';
const revision = '0123456789abcdef'.repeat(2) + '01234567';
const revisionLabel = 'org.opencontainers.image.revision';
const ociIndex = 'application/vnd.oci.image.index.v1+json';
const ociManifest = 'application/vnd.oci.image.manifest.v1+json';
const dockerIndex = 'application/vnd.docker.distribution.manifest.list.v2+json';
const dockerManifest = 'application/vnd.docker.distribution.manifest.v2+json';
const digest = (character) => `sha256:${character.repeat(64)}`;
const amd64Digest = digest('a');
const arm64Digest = digest('b');
const childRef = (childDigest, ref = imageRef) => `${ref.split('@')[0]}@${childDigest}`;

function descriptor(platform, childDigest, mediaType = ociManifest) {
  const [os, architecture, variant] = platform.split('/');
  return { mediaType, digest: childDigest, size: 742, platform: {
    os, architecture, ...(variant ? { variant } : {}),
  } };
}

function image(platform, sourceRevision = revision) {
  const [os, architecture, variant] = platform.split('/');
  return {
    architecture, os, ...(variant ? { variant } : {}),
    config: { Labels: { [revisionLabel]: sourceRevision }, Cmd: ['/service'] },
    rootfs: { type: 'layers', diff_ids: [digest('c')] },
    history: [{ created_by: 'COPY /service /service' }],
  };
}

// Shapes from Buildx imagetools: --raw returns OCI/Docker manifests; formatted
// .Image for a child manifest is an OCI config. Root .Image maps are not queried.
// https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/
function fixture() {
  return {
    manifest: { schemaVersion: 2, mediaType: ociIndex, manifests: [
      descriptor('linux/amd64', amd64Digest), descriptor('linux/arm64', arm64Digest),
    ] },
    images: {
      [childRef(amd64Digest)]: { value: image('linux/amd64') },
      [childRef(arm64Digest)]: { value: image('linux/arm64') },
    },
  };
}

function singleFixture(platform = 'linux/amd64') {
  return {
    manifest: {
      schemaVersion: 2, mediaType: ociManifest,
      config: { mediaType: 'application/vnd.oci.image.config.v1+json', digest: digest('c'), size: 400 },
      layers: [{ mediaType: 'application/vnd.oci.image.layer.v1.tar+gzip', digest: digest('d'), size: 600 }],
    },
    images: { [imageRef]: { value: image(platform) } },
  };
}

const fakeDocker = `#!${process.execPath}
const assert = require('node:assert/strict');
const { appendFileSync, readFileSync } = require('node:fs');
const fixture = JSON.parse(readFileSync(process.env.FIXTURE, 'utf8'));
const args = process.argv.slice(2);
appendFileSync(process.env.CALLS, JSON.stringify(args) + '\\n');
assert.deepEqual(args.slice(0, 3), ['buildx', 'imagetools', 'inspect']);
let response;
if (args[3] === '--raw') {
  assert.equal(args.length, 5);
  assert.equal(args[4], fixture.imageRef);
  response = fixture.rawResponse || { value: fixture.manifest };
} else {
  assert.deepEqual(args.slice(3, 5), ['--format', '{{json .Image}}']);
  assert.equal(args.length, 6);
  assert.ok(Object.hasOwn(fixture.images, args[5]), 'unexpected image reference: ' + args[5]);
  response = fixture.images[args[5]];
}
if (response.signal) process.kill(process.ppid, response.signal);
process.stdout.write(response.stdout ?? (JSON.stringify(response.value) ?? ''));
process.stderr.write(response.stderr ?? '');
process.exitCode = response.status ?? 0;
`;

function run(t, data, { args = [imageRef, revision], useRunnerTemp = true } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'verify-image-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const bin = join(root, 'bin');
  const home = join(root, 'home');
  const runnerTemp = join(root, 'runner temp');
  const fallbackTemp = join(root, 'fallback temp');
  for (const path of [bin, home, runnerTemp, fallbackTemp]) mkdirSync(path);
  writeFileSync(join(bin, 'docker'), fakeDocker, { mode: 0o755 });
  const fixturePath = join(root, 'fixture.json');
  const callsPath = join(root, 'calls.jsonl');
  writeFileSync(fixturePath, JSON.stringify({ ...data, imageRef: args[0] }));
  writeFileSync(callsPath, '');

  // No inherited credentials, Docker settings, shell startup hooks, or registry
  // access. The executable boundary accepts only the read-only fixture commands.
  const result = spawnSync('bash', [script, ...args], {
    cwd: root,
    env: {
      PATH: `${bin}:${dirname(process.execPath)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`,
      HOME: home, DOCKER_CONFIG: home, TMPDIR: fallbackTemp,
      ...(useRunnerTemp ? { RUNNER_TEMP: runnerTemp } : {}),
      FIXTURE: fixturePath, CALLS: callsPath,
    },
    encoding: 'utf8', timeout: 10_000,
  });
  assert.ifError(result.error);
  assert.equal(result.signal, null, result.stderr);
  assert.doesNotMatch(result.stderr, /\bAssertionError\b/, 'fake Docker boundary must accept every invocation');
  assert.deepEqual(readdirSync(runnerTemp), [], 'RUNNER_TEMP must be cleaned');
  assert.deepEqual(readdirSync(fallbackTemp), [], 'TMPDIR must be cleaned');
  const calls = readFileSync(callsPath, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
  return { ...result, calls };
}

function failure(result, status = 1) {
  assert.equal(result.status, status, result.stderr);
  assert.doesNotMatch(result.stdout, /Verified/);
}

test('verifies each default platform by its own immutable child digest', (t) => {
  const result = run(t, fixture());
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Verified .*linux\/amd64,linux\/arm64/);
  assert.deepEqual(result.calls, [
    ['buildx', 'imagetools', 'inspect', '--raw', imageRef],
    ['buildx', 'imagetools', 'inspect', '--format', '{{json .Image}}', childRef(amd64Digest)],
    ['buildx', 'imagetools', 'inspect', '--format', '{{json .Image}}', childRef(arm64Digest)],
  ]);
});

test('supports Docker schema 2 manifest lists and a requested platform subset', (t) => {
  const data = fixture();
  data.manifest.mediaType = dockerIndex;
  data.manifest.manifests.forEach((item) => { item.mediaType = dockerManifest; });
  data.images[childRef(arm64Digest)].value.config.Labels[revisionLabel] = 'unrelated-revision';
  const result = run(t, data, { args: [imageRef, revision, 'linux/amd64'] });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.calls.length, 2);
});

test('ignores attestations and extra platforms rather than counting their revisions', (t) => {
  const data = fixture();
  data.manifest.manifests.push(
    { ...descriptor('unknown/unknown', digest('c')), annotations: {
      'vnd.docker.reference.type': 'attestation-manifest',
      'vnd.docker.reference.digest': amd64Digest,
    } },
    descriptor('windows/amd64', digest('d')),
  );
  const result = run(t, data);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.calls.length, 3);
});

for (const platform of ['linux/amd64', 'linux/arm64', 'linux/arm/v7', 'windows/amd64']) {
  test(`supports a single image config for requested ${platform}`, (t) => {
    const result = run(t, singleFixture(platform), { args: [imageRef, revision, platform] });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.calls.length, 2);
  });
}

test('a single image cannot satisfy the default two-platform contract', (t) => {
  const result = run(t, singleFixture());
  failure(result);
  assert.equal(result.calls.length, 2, 'a mutable tag must not be re-read for each platform');
});

test('a single arm64 config supports both implicit and explicit v8 without re-reading the tag', (t) => {
  const result = run(t, singleFixture('linux/arm64'), {
    args: [imageRef, revision, 'linux/arm64,linux/arm64/v8'],
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.calls.length, 2);
});

test('supports a Docker schema 2 single manifest', (t) => {
  const data = singleFixture();
  data.manifest.mediaType = dockerManifest;
  data.manifest.config.mediaType = 'application/vnd.docker.container.image.v1+json';
  data.manifest.layers[0].mediaType = 'application/vnd.docker.image.rootfs.diff.tar.gzip';
  const result = run(t, data, { args: [imageRef, revision, 'linux/amd64'] });
  assert.equal(result.status, 0, result.stderr);
});

test('rejects a config variant that differs from the requested manifest platform', (t) => {
  const data = fixture();
  data.manifest.manifests[0] = descriptor('linux/arm/v7', amd64Digest);
  data.images[childRef(amd64Digest)].value = image('linux/arm/v6');
  failure(run(t, data, { args: [imageRef, revision, 'linux/arm/v7'] }));
});

test('supports platform variants and the implicit arm64 v8 variant', (t) => {
  const data = fixture();
  data.manifest.manifests[0] = descriptor('linux/arm/v7', amd64Digest);
  data.images[childRef(amd64Digest)].value = image('linux/arm/v7');
  const result = run(t, data, { args: [imageRef, revision, 'linux/arm/v7,linux/arm64/v8'] });
  assert.equal(result.status, 0, result.stderr);
});

test('rejects a missing requested variant', (t) => {
  failure(run(t, fixture(), { args: [imageRef, revision, 'linux/arm64/v9'] }));
});

test('replaces an existing root digest when inspecting child descriptors', (t) => {
  const ref = `${imageRef}@${digest('f')}`;
  const result = run(t, fixture(), { args: [ref, revision] });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.calls[1][5], childRef(amd64Digest));
});

test('uses TMPDIR when RUNNER_TEMP is not provided', (t) => {
  const result = run(t, fixture(), { useRunnerTemp: false });
  assert.equal(result.status, 0, result.stderr);
});

for (const [name, change] of [
  ['missing platform', (data) => { data.manifest.manifests.pop(); }],
  ['wrong OS', (data) => { data.manifest.manifests[1].platform.os = 'windows'; }],
  ['attestation pretending to be a requested platform', (data) => {
    data.manifest.manifests[1].annotations = { 'vnd.docker.reference.type': 'attestation-manifest' };
  }],
  ['wrong revision', (data) => { data.images[childRef(arm64Digest)].value.config.Labels[revisionLabel] = 'old'; }],
  ['missing revision', (data) => { delete data.images[childRef(arm64Digest)].value.config.Labels[revisionLabel]; }],
  ['non-string revision', (data) => { data.images[childRef(arm64Digest)].value.config.Labels[revisionLabel] = 123; }],
  ['missing labels', (data) => { delete data.images[childRef(arm64Digest)].value.config.Labels; }],
  ['wrong child architecture', (data) => { data.images[childRef(arm64Digest)].value.architecture = 'amd64'; }],
  ['wrong child OS', (data) => { data.images[childRef(arm64Digest)].value.os = 'windows'; }],
  ['matching labels nested in unrelated config objects', (data) => {
    const value = data.images[childRef(arm64Digest)].value;
    delete value.config.Labels;
    value.history = [image('linux/arm64'), image('linux/amd64')];
  }],
  ['an unrelated platform config in place of the required revision', (data) => {
    data.images[childRef(arm64Digest)].value = {
      'linux/amd64': image('linux/amd64'), 'windows/amd64': image('windows/amd64'),
    };
  }],
  ['one good revision hiding a duplicate platform with a bad revision', (data) => {
    data.manifest.manifests.push(descriptor('linux/amd64', digest('d')));
    data.images[childRef(digest('d'))] = { value: image('linux/amd64', 'old') };
  }],
]) {
  test(`rejects ${name}`, (t) => {
    const data = fixture();
    change(data);
    failure(run(t, data));
  });
}

test('unrelated nested labels do not invalidate otherwise matching platform revisions', (t) => {
  const data = fixture();
  data.images[childRef(arm64Digest)].value.history.push(image('windows/amd64', 'unrelated'));
  const result = run(t, data);
  assert.equal(result.status, 0, result.stderr);
});

test('verifies all duplicate descriptors when they match the requested platforms', (t) => {
  const data = fixture();
  data.manifest.manifests.push(descriptor('linux/amd64', digest('d')));
  data.images[childRef(digest('d'))] = { value: image('linux/amd64') };
  const result = run(t, data);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.calls.length, 4);
});

for (const stderr of [
  `ERROR: ${imageRef}: not found\n`,
  `ERROR: ${imageRef}: manifest unknown\n`,
  'ERROR: manifest unknown: manifest unknown\n',
  'MANIFEST_UNKNOWN: manifest unknown\n',
]) {
  test(`returns 3 only for definite root-manifest absence: ${stderr.trim()}`, (t) => {
    const data = fixture();
    data.rawResponse = { status: 1, stderr };
    const result = run(t, data);
    failure(result, 3);
    assert.equal(result.calls.length, 1);
    assert.match(result.stderr, /not found|manifest unknown/);
  });
}

for (const stderr of [
  'ERROR: unauthorized: authentication required\n',
  'ERROR: denied: requested access to the resource is denied\n',
  'ERROR: pull access denied, repository does not exist or may require authorization\n',
  'ERROR: failed to fetch anonymous token: unexpected status: 404 Not Found\n',
  'ERROR: unexpected status from HEAD request: 503 Service Unavailable\n',
  'ERROR: unexpected status from HEAD request: 429 Too Many Requests\n',
  'ERROR: unexpected status from HEAD request: 404 Not Found\n',
  'ERROR: dial tcp: lookup registry.invalid: no such host\n',
  'ERROR: context deadline exceeded\n',
  'ERROR: tls: failed to verify certificate\n',
  'ERROR: docker-credential-helper: executable file not found in $PATH\n',
  'ERROR: manifest unknown: authorization failed\n',
  `ERROR: ${imageRef}: not found\nERROR: unauthorized\n`,
  `ERROR: ${imageRef}: not found: i/o timeout\n`,
  'ERROR: another/repository:tag: not found\n',
  '',
]) {
  test(`does not treat operational errors as missing: ${stderr.trim() || '(empty stderr)'}`, (t) => {
    const data = fixture();
    // Even Docker status 3 is not allowed to leak into the missing-image result.
    data.rawResponse = { status: 3, stderr };
    const result = run(t, data);
    failure(result);
    assert.equal(result.calls.length, 1);
  });
}

for (const stderr of [
  `ERROR: ${childRef(arm64Digest)}: not found\n`,
  'ERROR: manifest unknown: manifest unknown\n',
  'ERROR: blob unknown to registry\n',
  'ERROR: unauthorized: authentication required\n',
  'ERROR: context deadline exceeded\n',
]) {
  test(`child/config inspection failure is never root absence: ${stderr.trim()}`, (t) => {
    const data = fixture();
    data.images[childRef(arm64Digest)] = { status: 3, stderr };
    failure(run(t, data));
  });
}

for (const stage of ['manifest', 'image']) {
  for (const [name, stdout] of [
    ['empty response', ''], ['invalid JSON', '{'], ['null', 'null'],
    ['array', '[]'], ['empty object', '{}'], ['string', '"unexpected"'],
    ['multiple JSON documents', '{}\n'],
  ]) {
    test(`rejects ${stage} ${name}`, (t) => {
      const data = fixture();
      const valid = stage === 'manifest' ? data.manifest : data.images[childRef(arm64Digest)].value;
      const response = { stdout: name === 'multiple JSON documents' ? stdout + JSON.stringify(valid) : stdout };
      if (stage === 'manifest') data.rawResponse = response;
      else data.images[childRef(arm64Digest)] = response;
      failure(run(t, data));
    });
  }
}

for (const [name, change] of [
  ['schema version', (manifest) => { manifest.schemaVersion = 1; }],
  ['media type', (manifest) => { manifest.mediaType = 'text/html'; }],
  ['empty manifest list', (manifest) => { manifest.manifests = []; }],
  ['missing manifests', (manifest) => { delete manifest.manifests; }],
  ['object instead of manifests array', (manifest) => { manifest.manifests = {}; }],
  ['missing digest', (manifest) => { delete manifest.manifests[1].digest; }],
  ['invalid digest', (manifest) => { manifest.manifests[1].digest = 'not-a-digest'; }],
  ['negative descriptor size', (manifest) => { manifest.manifests[1].size = -1; }],
]) {
  test(`rejects corrupt manifest ${name}`, (t) => {
    const data = fixture();
    change(data.manifest);
    const result = run(t, data);
    failure(result);
    assert.equal(result.calls.length, 1);
  });
}

for (const args of [
  [], [imageRef], [imageRef, revision, 'linux/amd64', 'extra'],
  ['', revision], [imageRef, ''], ['--help', revision],
  [imageRef, revision, ''], [imageRef, revision, 'linux/amd64,'],
  [imageRef, revision, ',linux/amd64'], [imageRef, revision, 'linux/amd64,,linux/arm64'],
  [imageRef, revision, 'amd64'], [imageRef, revision, 'linux/amd64, linux/arm64'],
]) {
  test(`rejects invalid arguments before Docker is called: ${JSON.stringify(args)}`, (t) => {
    const result = run(t, fixture(), { args });
    failure(result, 64);
    assert.deepEqual(result.calls, []);
  });
}

test('cleans temporary files when interrupted during Docker inspection', (t) => {
  const data = fixture();
  data.images[childRef(amd64Digest)].signal = 'SIGTERM';
  failure(run(t, data), 143);
});
