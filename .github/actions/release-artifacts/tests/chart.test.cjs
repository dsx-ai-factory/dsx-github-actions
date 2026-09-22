// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const prepareScript = path.resolve(__dirname, '../prepare-chart.cjs');
const verifyScript = path.resolve(__dirname, '../verify-chart.sh');
const version = '2.8.0-rc.3';
const revision = 'abcdef0123'.repeat(4);

function available(name, args, pattern) {
  for (const directory of (process.env.PATH || '').split(path.delimiter)) {
    const executable = path.join(directory, name);
    const result = spawnSync(executable, args, { encoding: 'utf8' });
    if (result.status === 0 && pattern.test(result.stdout)) return executable;
  }
}

const yq = available('yq', ['--version'], /mikefarah\/yq.*version v4\./);
const helm = available('helm', ['version', '--short'], /v3\./);

function success(result) {
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null, result.stderr);
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /fake-secret-never-log/);
}

function failure(result, message) {
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null, result.stderr);
  assert.notEqual(result.status, 0, result.stderr);
  if (message) assert.match(result.stderr, message);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /fake-secret-never-log/);
}

function snapshot(directory) {
  const result = {};
  function visit(entry) {
    const stat = fs.lstatSync(entry);
    const key = path.relative(directory, entry);
    if (stat.isSymbolicLink()) result[key] = ['link', fs.readlinkSync(entry)];
    else if (stat.isDirectory()) {
      result[key] = ['directory', stat.mode, stat.mtimeMs];
      for (const child of fs.readdirSync(entry)) visit(path.join(entry, child));
    } else result[key] = ['file', stat.mode, stat.mtimeMs, fs.readFileSync(entry, 'utf8')];
  }
  visit(directory);
  return result;
}

function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'rc-chart-test-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'product checkout');
  const bin = path.join(root, 'bin');
  const temporary = path.join(root, 'tmp');
  const home = path.join(root, 'home');
  const log = path.join(root, 'processes.jsonl');
  for (const directory of [source, bin, temporary, home]) fs.mkdirSync(directory);
  const env = {
    PATH: `${bin}${path.delimiter}${process.env.PATH}`,
    HOME: home, TMPDIR: temporary, RUNNER_TEMP: temporary, LC_ALL: 'C',
    HELM_CACHE_HOME: path.join(home, 'cache'), HELM_CONFIG_HOME: path.join(home, 'config'),
    HELM_DATA_HOME: path.join(home, 'data'), HELM_REPOSITORY_CONFIG: path.join(home, 'repositories.yaml'),
    HELM_REPOSITORY_CACHE: path.join(home, 'cache/repository'),
    TEST_LOG: log, TEST_REAL_HELM: helm || '', TEST_SECRET: 'fake-secret-never-log',
  };
  const config = { name: 'example-service', path: 'products/service chart' };

  function executable(name, content) {
    fs.writeFileSync(path.join(bin, name), `#!${process.execPath}\n${content}`, { mode: 0o755 });
  }
  if (yq) fs.symlinkSync(yq, path.join(bin, 'yq'));
  executable('sleep', `require('node:fs').appendFileSync(process.env.TEST_LOG,
    JSON.stringify({command: 'sleep', args: process.argv.slice(2)}) + '\\n');`);
  executable('helm', `
    const fs = require('node:fs');
    const path = require('node:path');
    const { execFileSync, spawnSync } = require('node:child_process');
    const env = process.env;
    const args = process.argv.slice(2);
    const previous = fs.existsSync(env.TEST_LOG)
      ? fs.readFileSync(env.TEST_LOG, 'utf8').trim().split('\\n').map(JSON.parse) : [];
    fs.appendFileSync(env.TEST_LOG, JSON.stringify({command: 'helm', args, cwd: process.cwd(),
      repositoryConfig: env.HELM_REPOSITORY_CONFIG}) + '\\n');
    function fail() { console.log(env.TEST_SECRET); console.error(env.TEST_ERROR || env.TEST_SECRET); process.exit(1); }
    function failures(count) { return count === 'always' || Number(count) >= attempt; }
    let attempt = previous.filter(item => item.command === 'helm' && item.args[0] === args[0]).length + 1;
    if (args[0] === 'dependency') {
      if (env.TEST_DEPENDENCY_FAILURE) fail();
      process.exit(0);
    }
    if (args[0] === 'repo' && args[1] === 'update') {
      if (JSON.stringify(args) !== JSON.stringify(['repo', 'update', 'helm-repo-ngc', '--fail-on-repo-update-fail'])) fail();
      if (failures(env.TEST_REPO_FAILURES)) fail();
      if (env.TEST_REPO_WARNING) console.error(env.TEST_SECRET);
    } else if (args[0] === 'search' && args[1] === 'repo') {
      if (JSON.stringify(args) !== JSON.stringify(['search', 'repo', 'helm-repo-ngc/' + env.CHART_NAME,
        '--versions', '--devel', '--output', 'json'])) fail();
      if (env.TEST_SEARCH_FAILURE) fail();
      if (env.TEST_SEARCH_WARNING) console.error(env.TEST_SECRET);
      if (env.TEST_REAL_SEARCH) {
        const result = spawnSync(env.TEST_REAL_HELM, args, {encoding: 'utf8'});
        process.stdout.write(result.stdout || '');
        process.stderr.write(result.stderr || '');
        process.exit(result.status === null ? 1 : result.status);
      }
      process.stdout.write(env.TEST_SEARCH_RESULTS === undefined
        ? JSON.stringify([{name: 'helm-repo-ngc/' + env.CHART_NAME, version: env.RELEASE_VERSION}])
        : env.TEST_SEARCH_RESULTS);
    } else if (args[0] === 'pull') {
      if (args[1] !== 'helm-repo-ngc/' + env.CHART_NAME || args[2] !== '--version' || args[3] !== env.RELEASE_VERSION) fail();
      const destination = args[args.indexOf('--destination') + 1];
      const archive = path.join(destination, env.CHART_NAME + '-' + env.RELEASE_VERSION + '.tgz');
      if (fs.existsSync(archive)) fail();
      if (failures(env.TEST_PULL_FAILURES)) { fs.writeFileSync(archive, 'partial'); fail(); }
      if (!env.TEST_NO_ARCHIVE) fs.copyFileSync(env.TEST_ARCHIVE, archive);
    } else if (args[0] === 'show' && args[1] === 'chart') {
      if (env.TEST_SHOW_FAILURE) fail();
      if (env.TEST_REAL_HELM && !env.TEST_SHOW_OVERRIDE) {
        try { process.stdout.write(execFileSync(env.TEST_REAL_HELM, args, {stdio: ['pipe', 'pipe', 'pipe']})); }
        catch { fail(); }
      } else process.stdout.write(fs.readFileSync(env.TEST_METADATA));
    } else fail();
  `);

  function write(relative, content) {
    const file = path.join(source, relative);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
    return file;
  }
  function chart(relative, name, extra = '') {
    return write(`${relative}/Chart.yaml`, `# Preserve this comment.\napiVersion: v2\nname: ${name}\nversion: 0.1.0\nappVersion: "old-app"\ndescription: "Preserve: chart metadata"\nannotations:\n  example.com/owner: platform\n${extra}`);
  }
  chart(config.path, config.name);
  write(`${config.path}/values.yaml`, 'replicaCount: 2\n');

  function run(command, args, extraEnv = {}, cwd = source) {
    return spawnSync(command, args, { cwd, env: { ...env, ...extraEnv }, encoding: 'utf8', timeout: 20_000 });
  }
  function prepare({ validateOnly = false, chartConfig = config, extraEnv = {}, args } = {}) {
    return run(process.execPath, [prepareScript, ...(args || (validateOnly ? ['--validate-only'] : []))], {
      ...(validateOnly ? {} : { RELEASE_VERSION: version, EXPECTED_REVISION: revision }),
      RC_CHART: JSON.stringify(chartConfig), ...extraEnv,
    });
  }
  function events() {
    return fs.existsSync(log) ? fs.readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse) : [];
  }
  function metadata(relative = config.path) {
    const result = run(yq, ['eval', '-o=json', '.', path.join(source, relative, 'Chart.yaml')]);
    success(result);
    return JSON.parse(result.stdout);
  }
  function localDependency(repository = 'file://../../shared/worker', name = 'example-worker') {
    chart('shared/worker', 'example-worker');
    chart(config.path, config.name, `dependencies:\n  - name: ${name}\n    version: "0.1.0"\n    repository: ${JSON.stringify(repository)}\n`);
    config.localDependencies = [{ name: 'example-worker', path: 'shared/worker' }];
  }
  function useRealHelm() {
    fs.unlinkSync(path.join(bin, 'helm'));
    fs.symlinkSync(helm, path.join(bin, 'helm'));
  }
  function publishedArtifact(changes = {}, raw) {
    const artifact = path.join(root, 'artifact');
    fs.mkdirSync(artifact);
    const data = {
      apiVersion: 'v2', name: config.name, version, appVersion: version,
      annotations: { 'dsx.nvidia.com/source-revision': revision }, ...changes,
    };
    const file = path.join(artifact, 'Chart.yaml');
    fs.writeFileSync(file, JSON.stringify(data));
    let archive = path.join(root, 'fixture.tgz');
    if (helm) {
      success(run(helm, ['package', artifact, '--destination', root]));
      archive = path.join(root, `${data.name}-${data.version}.tgz`);
    } else fs.writeFileSync(archive, 'Explicit fake archive; helm is unavailable.');
    if (raw !== undefined) fs.writeFileSync(file, raw);
    return { TEST_ARCHIVE: archive, TEST_METADATA: file, ...(raw === undefined ? {} : { TEST_SHOW_OVERRIDE: 'true' }) };
  }
  function verify(artifact, extraEnv = {}, args = [], scriptArgs = []) {
    return run('bash', [...args, verifyScript, ...scriptArgs], {
      CHART_NAME: config.name, RELEASE_VERSION: version, EXPECTED_REVISION: revision,
      ...artifact, ...extraEnv,
    });
  }
  function checkExisting(artifact, extraEnv = {}) {
    return verify(artifact, extraEnv, [], ['--check-existing']);
  }
  return { root, source, bin, env, temporary, config, chart, write, run, prepare, events, metadata,
    localDependency, useRealHelm, publishedArtifact, verify, checkExisting, executable };
}

test('chart preparation with actual Mike Farah yq', { skip: !yq && 'Mike Farah yq v4 is not installed' }, async (t) => {
  await t.test('stamps only root metadata, preserves unrelated YAML, defaults localDependencies to []', (t) => {
    const f = fixture(t);
    const values = fs.readFileSync(path.join(f.source, f.config.path, 'values.yaml'), 'utf8');
    success(f.prepare());
    const data = f.metadata();
    assert.equal(data.version, version);
    assert.equal(data.appVersion, version);
    assert.equal(data.annotations['dsx.nvidia.com/source-revision'], revision);
    assert.equal(data.annotations['example.com/owner'], 'platform');
    assert.equal(data.description, 'Preserve: chart metadata');
    assert.match(fs.readFileSync(path.join(f.source, f.config.path, 'Chart.yaml'), 'utf8'), /# Preserve this comment\./);
    assert.equal(fs.readFileSync(path.join(f.source, f.config.path, 'values.yaml'), 'utf8'), values);
    assert.deepEqual(f.events(), []);
    success(f.prepare());
    assert.deepEqual(f.metadata(), data);
  });

  await t.test('validate-only validates local dependencies without release env, writes, or Helm calls', (t) => {
    const f = fixture(t);
    f.localDependency();
    const before = snapshot(f.source);
    success(f.prepare({ validateOnly: true }));
    assert.deepEqual(snapshot(f.source), before);
    assert.deepEqual(f.events(), []);
  });

  await t.test('stamps declared nested values, preserving unrelated values and comments', (t) => {
    const f = fixture(t);
    const file = f.write(`${f.config.path}/values.yaml`,
      '# Keep this comment.\nwidget:\n  widget-server:\n    image:\n      tag: old\n      repository: example/widget\nother: unchanged\n');
    f.config.versionValuePaths = [['widget', 'widget-server', 'image', 'tag']];
    f.config.lintValues = { endpoint: 'https://example.invalid', enabled: false };
    const before = snapshot(f.source);
    success(f.prepare({ validateOnly: true }));
    assert.deepEqual(snapshot(f.source), before);
    success(f.prepare());
    const values = () => JSON.parse(f.run(yq, ['eval', '-o=json', '.', file]).stdout);
    assert.deepEqual(values(), {
      widget: { 'widget-server': { image: { tag: version, repository: 'example/widget' } } },
      other: 'unchanged',
    });
    assert.match(fs.readFileSync(file, 'utf8'), /# Keep this comment\./);
    success(f.prepare());
    assert.equal(values().widget['widget-server'].image.tag, version);
  });

  const invalidValues = [
    ['null paths', { versionValuePaths: null }],
    ['nonarray paths', { versionValuePaths: '.widget.image.tag' }],
    ['too many paths', { versionValuePaths: Array(33).fill(['widget', 'image', 'tag']) }],
    ['string path', { versionValuePaths: ['.widget.image.tag'] }],
    ['empty path', { versionValuePaths: [[]] }],
    ['numeric key', { versionValuePaths: [['widget', 0]] }],
    ['expression key', { versionValuePaths: [['widget | env(SECRET)']] }],
    ['newline key', { versionValuePaths: [['widget\n']] }],
    ['duplicate path', { versionValuePaths: [['widget', 'image', 'tag'], ['widget', 'image', 'tag']] }],
    ['missing leaf', { versionValuePaths: [['widget', 'image', 'typo']] }],
    ['missing parent', { versionValuePaths: [['missing', 'image', 'tag']] }],
    ['object leaf', { versionValuePaths: [['widget', 'image']] }],
    ['numeric leaf', { versionValuePaths: [['replicas']] }],
    ['null lint values', { lintValues: null }],
    ['array lint values', { lintValues: [] }],
    ['string lint values', { lintValues: 'endpoint=example' }],
    ['oversized lint values', { lintValues: { endpoint: 'x'.repeat(65536) } }],
  ];
  for (const [name, options] of invalidValues) {
    await t.test(`rejects ${name} in preflight and preparation without writes`, (t) => {
      const f = fixture(t);
      f.write(`${f.config.path}/values.yaml`, 'widget:\n  image:\n    tag: old\nreplicas: 2\n');
      const chartConfig = { ...f.config, ...options };
      const before = snapshot(f.source);
      failure(f.prepare({ chartConfig, validateOnly: true }), /versionValuePaths|lintValues/);
      failure(f.prepare({ chartConfig }), /versionValuePaths|lintValues/);
      const output = path.join(f.root, 'output');
      failure(f.run(process.execPath, [path.resolve(__dirname, '../plan.cjs')], {
        RC_CHARTS: JSON.stringify([{ ...chartConfig, path: '.' }]),
        RC_NGC_PATH: 'example/components-dev', GITHUB_OUTPUT: output,
      }, path.join(f.source, f.config.path)), /Chart declaration failed validation/);
      assert.equal(fs.existsSync(output), false);
      assert.deepEqual(snapshot(f.source), before);
      assert.deepEqual(f.events(), []);
    });
  }

  await t.test('plans valid chart overrides without changing source values', (t) => {
    const f = fixture(t);
    const chart = { name: f.config.name, path: '.',
      versionValuePaths: [['image', 'tag']], lintValues: { endpoint: 'https://example.invalid' } };
    f.write(`${f.config.path}/values.yaml`, 'image:\n  tag: old\n');
    const before = snapshot(f.source);
    const output = path.join(f.root, 'output');
    success(f.run(process.execPath, [path.resolve(__dirname, '../plan.cjs')], {
      RC_CHARTS: JSON.stringify([chart]), RC_NGC_PATH: 'example/components-dev', GITHUB_OUTPUT: output,
    }, path.join(f.source, f.config.path)));
    const line = fs.readFileSync(output, 'utf8').split('\n').find(value => value.startsWith('charts='));
    assert.deepEqual(JSON.parse(line.slice('charts='.length)), [{ ...chart, localDependencies: [] }]);
    assert.deepEqual(snapshot(f.source), before);
  });

  await t.test('real Helm lint uses temporary values without changing the packaged defaults', {
    skip: !helm && 'Helm v3 is not installed',
  }, (t) => {
    const f = fixture(t);
    f.useRealHelm();
    f.write(`${f.config.path}/values.yaml`, 'endpoint: ""\nimage:\n  tag: old\n');
    f.write(`${f.config.path}/values.schema.json`, JSON.stringify({ type: 'object', properties: {
      endpoint: { type: 'string', minLength: 1 },
    } }));
    f.config.versionValuePaths = [['image', 'tag']];
    success(f.prepare());
    const lint = lintValues => f.run('bash', [path.resolve(__dirname, '../../helm-shared/scripts/utils.sh'), 'helm_lint'], {
      CC_HELM_CHART_PATH: f.config.path, CC_HELM_LINT: 'true', CC_HELM_LINT_VALUES: lintValues,
    });
    const before = snapshot(f.source);
    failure(lint('{}'));
    assert.deepEqual(fs.readdirSync(f.temporary), []);
    success(lint(JSON.stringify({ endpoint: 'https://example.invalid', image: { tag: 'lint-only' } })));
    assert.deepEqual(fs.readdirSync(f.temporary), []);
    for (const invalid of ['[]', 'null', '"fake-secret-never-log"', '{fake-secret-never-log']) {
      const result = lint(invalid);
      failure(result);
      assert.match(result.stdout + result.stderr, /JSON mapping/);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    }
    assert.deepEqual(snapshot(f.source), before);
    success(f.run('bash', [path.resolve(__dirname, '../../helm-shared/scripts/utils.sh'), 'helm_package'], {
      CC_HELM_CHART_PATH: f.config.path, CC_HELM_CHART_VERSION: version, CC_HELM_CHART_APP_VERSION: version,
      CC_HELM_PACKAGE_DIR: f.root,
    }));
    const archive = path.join(f.root, `${f.config.name}-${version}.tgz`);
    const shown = f.run(helm, ['show', 'values', archive]);
    success(shown);
    const file = path.join(f.root, 'packaged-values.yaml');
    fs.writeFileSync(file, shown.stdout);
    const parsed = f.run(yq, ['eval', '-o=json', '.', file]);
    success(parsed);
    assert.deepEqual(JSON.parse(parsed.stdout), { endpoint: '', image: { tag: version } });
  });

  await t.test('supports root checkout charts, empty dependencies, and absent annotations', (t) => {
    const f = fixture(t);
    f.config = { name: 'example.service_v2', path: '.' };
    f.write('Chart.yaml', 'apiVersion: v2\nname: example.service_v2\nversion: 0.1.0\ndependencies: []\n');
    const before = snapshot(f.source);
    success(f.prepare({ validateOnly: true, chartConfig: f.config }));
    assert.deepEqual(snapshot(f.source), before);
    success(f.prepare({ chartConfig: f.config }));
    assert.equal(f.metadata('.').annotations['dsx.nvidia.com/source-revision'], revision);
    assert.deepEqual(f.events(), []);
  });

  await t.test('aligns only explicitly supplied local dependencies and resolves from the declared chart directory', (t) => {
    const f = fixture(t);
    f.localDependency();
    f.chart('shared/other', 'unmanaged-library');
    fs.appendFileSync(path.join(f.source, f.config.path, 'Chart.yaml'),
      '  - name: unmanaged-library\n    version: "0.1.0"\n    repository: file://../../shared/other\n');
    f.chart('shared/managed', 'managed-library');
    fs.appendFileSync(path.join(f.source, f.config.path, 'Chart.yaml'),
      '  - name: managed-library\n    version: "0.1.0"\n    repository: file://../../shared/managed\n');
    f.config.localDependencies.push({ name: 'managed-library', path: 'shared/managed' });
    const unmanaged = fs.readFileSync(path.join(f.source, 'shared/other/Chart.yaml'), 'utf8');
    success(f.prepare());
    assert.equal(f.metadata().dependencies[0].version, version);
    assert.equal(f.metadata().dependencies[1].version, '0.1.0');
    assert.equal(f.metadata().dependencies[2].version, version);
    assert.equal(f.metadata('shared/managed').version, version);
    assert.equal(f.metadata('shared/managed').appVersion, version);
    assert.equal(f.metadata('shared/worker').version, version);
    assert.equal(f.metadata('shared/worker').appVersion, version);
    assert.equal(f.metadata('shared/worker').annotations['dsx.nvidia.com/source-revision'], undefined);
    assert.equal(fs.readFileSync(path.join(f.source, 'shared/other/Chart.yaml'), 'utf8'), unmanaged);
    assert.deepEqual(f.events().map(({ args, cwd }) => ({ args, cwd })), [
      { args: ['dependency', 'update', '.'], cwd: path.join(f.source, f.config.path) },
    ]);
  });

  await t.test('accepts internal symlinks when their real dependency targets agree', (t) => {
    const f = fixture(t);
    f.localDependency('file://../../worker-link');
    fs.symlinkSync('shared/worker', path.join(f.source, 'worker-link'));
    success(f.prepare({ validateOnly: true }));
    success(f.prepare());
    assert.equal(f.metadata('shared/worker').version, version);
  });

  const invalid = [
    ['newline in name', (f) => { f.config.name += '\n'; }, /valid chart name/],
    ['wrong root name', (f) => { f.config.name = 'another-service'; }, /name does not match/],
    ['wrong local chart name', (f) => { f.chart('shared/worker', 'wrong-worker'); }, /name does not match/],
    ['wrong parent dependency name', (f) => { f.localDependency('file://../../shared/worker', 'wrong-parent-name'); }, /name does not match/],
    ['wrong local path with the same chart name', (f) => { f.chart('shared/other', 'example-worker'); f.config.localDependencies[0].path = 'shared/other'; }, /file:\/\/ resolution/],
    ['non-file repository for supplied local dependency', (f) => { f.localDependency('https://example.invalid/charts'); }, /file:\/\/ resolution/],
    ['missing parent dependency', (f) => { f.chart(f.config.path, f.config.name); }, /exactly one parent/],
    ['duplicate supplied dependency', (f) => { f.config.localDependencies.push({ ...f.config.localDependencies[0] }); }, /duplicate name/],
    ['duplicate parent dependency', (f) => { fs.appendFileSync(path.join(f.source, f.config.path, 'Chart.yaml'), '  - name: example-worker\n    version: 0.1.0\n    repository: file://../../shared/worker\n'); }, /exactly one parent/],
    ['invalid localDependencies type', (f) => { f.config.localDependencies = null; }, /must be an array/],
    ['missing local path', (f) => { delete f.config.localDependencies[0].path; }, /checkout-relative path/],
    ['absolute local path', (f) => { f.config.localDependencies[0].path = path.join(f.source, 'shared/worker'); }, /checkout-relative path/],
    ['missing root path', (f) => { delete f.config.path; }, /checkout-relative path/],
    ['absolute root path', (f) => { f.config.path = path.join(f.source, f.config.path); }, /checkout-relative path/],
    ['nonexistent root chart', (f) => { f.config.path = 'not-a-chart'; }, /existing path/],
    ['chart path is a file', (f) => { f.config.path += '/Chart.yaml'; }, /must be a directory/],
    ['missing Chart.yaml', (f) => { fs.unlinkSync(path.join(f.source, f.config.path, 'Chart.yaml')); }, /existing path/],
    ['empty file repository path', (f) => { f.localDependency('file://'); }, /checkout-relative path/],
    ['broken content symlink', (f) => { fs.symlinkSync('nonexistent', path.join(f.source, f.config.path, 'broken')); }, /existing path/],
    ['invalid YAML', (f) => { f.write(`${f.config.path}/Chart.yaml`, 'name: [broken\n'); }, /valid YAML document/],
    ['multiple YAML documents', (f) => { fs.appendFileSync(path.join(f.source, f.config.path, 'Chart.yaml'), '\n---\nname: hidden\n'); }, /valid YAML document/],
    ['nonmapping YAML', (f) => { f.write(`${f.config.path}/Chart.yaml`, '- invalid\n'); }, /valid chart name/],
    ['invalid annotations', (f) => { f.write(`${f.config.path}/Chart.yaml`, `name: ${f.config.name}\nannotations: []\n`); }, /annotations must be a mapping/],
    ['invalid dependency shape', (f) => { f.write(`${f.config.path}/Chart.yaml`, `name: ${f.config.name}\ndependencies: wrong\n`); }, /dependencies must be an array/],
  ];
  for (const [name, change, message] of invalid) {
    await t.test(`rejects ${name} in both modes before mutation`, (t) => {
      const f = fixture(t);
      f.localDependency();
      change(f);
      const before = snapshot(f.source);
      failure(f.prepare({ validateOnly: true }), message);
      failure(f.prepare(), message);
      assert.deepEqual(snapshot(f.source), before);
      assert.deepEqual(f.events(), []);
    });
  }

  for (const kind of ['root traversal', 'local traversal', 'file repository traversal', 'undeclared file repository traversal',
    'root symlink', 'Chart.yaml symlink', 'local symlink', 'template symlink', 'lock symlink', 'nested file repository']) {
    await t.test(`rejects checkout escape through ${kind}`, (t) => {
      const f = fixture(t);
      f.localDependency();
      const outside = path.join(f.root, 'outside');
      fs.mkdirSync(outside);
      fs.writeFileSync(path.join(outside, 'Chart.yaml'), 'apiVersion: v2\nname: example-worker\nversion: 0.1.0\n');
      const original = snapshot(outside);
      switch (kind) {
        case 'root traversal': f.config.path = '../outside'; break;
        case 'local traversal': f.config.localDependencies[0].path = '../outside'; break;
        case 'file repository traversal': f.localDependency('file://../../../outside'); break;
        case 'undeclared file repository traversal': f.localDependency('file://../../../outside'); delete f.config.localDependencies; break;
        case 'root symlink': fs.symlinkSync(outside, path.join(f.source, 'external')); f.config.path = 'external'; break;
        case 'Chart.yaml symlink': {
          const file = path.join(f.source, f.config.path, 'Chart.yaml');
          fs.unlinkSync(file); fs.symlinkSync(path.join(outside, 'Chart.yaml'), file); break;
        }
        case 'local symlink': fs.symlinkSync(outside, path.join(f.source, 'external')); f.config.localDependencies[0].path = 'external'; break;
        case 'template symlink': fs.symlinkSync(outside, path.join(f.source, f.config.path, 'templates')); break;
        case 'lock symlink': fs.symlinkSync(path.join(outside, 'Chart.yaml'), path.join(f.source, f.config.path, 'Chart.lock')); break;
        case 'nested file repository': f.chart('shared/worker', 'example-worker', 'dependencies:\n  - name: example-worker\n    version: 0.1.0\n    repository: file://../../../outside\n'); break;
      }
      const before = snapshot(f.source);
      failure(f.prepare({ validateOnly: true }), /escapes the checkout/);
      failure(f.prepare(), /escapes the checkout/);
      assert.deepEqual(snapshot(f.source), before);
      assert.deepEqual(snapshot(outside), original);
      assert.deepEqual(f.events(), []);
    });
  }

  for (const kind of ['directory symlink', 'file dependency']) {
    await t.test(`rejects ${kind} cycles`, (t) => {
      const f = fixture(t);
      if (kind === 'directory symlink') fs.symlinkSync('.', path.join(f.source, f.config.path, 'loop'));
      else f.chart(f.config.path, f.config.name, `dependencies:\n  - name: ${f.config.name}\n    version: 0.1.0\n    repository: file://.\n`);
      failure(f.prepare({ validateOnly: true }), /cycle/);
      assert.deepEqual(f.events(), []);
    });
  }

  await t.test('runtime requires valid release variables without writing on error', (t) => {
    const f = fixture(t);
    const before = snapshot(f.source);
    for (const extraEnv of [{ RELEASE_VERSION: '' }, { RELEASE_VERSION: '../unsafe' },
      { RELEASE_VERSION: '2.8.0' }, { RELEASE_VERSION: `${version}\n` },
      { EXPECTED_REVISION: '' }, { EXPECTED_REVISION: 'shortsha' }, { EXPECTED_REVISION: `${revision}\n` }]) {
      failure(f.prepare({ extraEnv }), /RELEASE_VERSION|EXPECTED_REVISION/);
      assert.deepEqual(snapshot(f.source), before);
    }
    failure(f.prepare({ extraEnv: { RC_CHART: 'not-json' } }), /JSON object/);
    failure(f.prepare({ args: ['--unknown'] }), /Usage/);
    assert.deepEqual(f.events(), []);
  });

  await t.test('dependency resolution failure exits nonzero without printing tool diagnostics', (t) => {
    const f = fixture(t);
    f.localDependency();
    failure(f.prepare({ extraEnv: { TEST_DEPENDENCY_FAILURE: 'true' } }), /helm failed/);
    assert.equal(f.events().length, 1);
  });

  await t.test('yq failure exits nonzero without printing tool diagnostics', (t) => {
    const f = fixture(t);
    fs.unlinkSync(path.join(f.bin, 'yq'));
    f.executable('yq', 'console.error(process.env.TEST_SECRET); process.exit(1);');
    const before = snapshot(f.source);
    failure(f.prepare(), /readable by yq/);
    assert.deepEqual(snapshot(f.source), before);
    assert.deepEqual(f.events(), []);
  });

  await t.test('yq write failure prevents Helm dependency update and hides tool diagnostics', (t) => {
    const f = fixture(t);
    f.localDependency();
    fs.unlinkSync(path.join(f.bin, 'yq'));
    f.executable('yq', `
      const { execFileSync } = require('node:child_process');
      const args = process.argv.slice(2);
      if (args.includes('-i')) { console.error(process.env.TEST_SECRET); process.exit(1); }
      process.stdout.write(execFileSync(${JSON.stringify(yq)}, args));
    `);
    const before = snapshot(f.source);
    failure(f.prepare(), /yq failed/);
    assert.deepEqual(snapshot(f.source), before);
    assert.deepEqual(f.events(), []);
  });

  await t.test('real Helm dependency update and packaging contain aligned metadata', {
    skip: !helm && 'Helm v3 is not installed',
  }, (t) => {
    const f = fixture(t);
    f.localDependency();
    f.useRealHelm();
    success(f.prepare());
    success(f.run(helm, ['package', f.config.path, '--destination', f.root]));
    for (const [file, name] of [
      [path.join(f.root, `${f.config.name}-${version}.tgz`), f.config.name],
      [path.join(f.source, f.config.path, `charts/example-worker-${version}.tgz`), 'example-worker'],
    ]) {
      const shown = f.run(helm, ['show', 'chart', file]);
      success(shown);
      const metadataFile = path.join(f.root, 'shown.yaml');
      fs.writeFileSync(metadataFile, shown.stdout);
      const parsed = f.run(yq, ['eval', '-o=json', '.', metadataFile]);
      success(parsed);
      const data = JSON.parse(parsed.stdout);
      assert.equal(data.name, name);
      assert.equal(data.version, version);
      assert.equal(data.appVersion, version);
      if (name === f.config.name) assert.equal(data.annotations['dsx.nvidia.com/source-revision'], revision);
    }
  });
});

test('check-existing chart mode is fail-closed and read-only', {
  skip: !yq && 'Mike Farah yq v4 is not installed',
}, async (t) => {
  await t.test('returns 0 only after the indexed artifact metadata is verified', (t) => {
    const f = fixture(t);
    const before = snapshot(f.source);
    success(f.checkExisting(f.publishedArtifact()));
    assert.deepEqual(f.events().map(({ args }) => args.slice(0, 2)), [
      ['repo', 'update'], ['search', 'repo'], ['pull', 'helm-repo-ngc/example-service'], ['show', 'chart'],
    ]);
    assert.deepEqual(snapshot(f.source), before);
    assert.deepEqual(fs.readdirSync(f.temporary), []);
  });

  for (const [name, rows] of [
    ['empty index result', []],
    ['different version', [{ name: 'helm-repo-ngc/example-service', version: '2.8.0-rc.2' }]],
    ['substring names and other repositories', [
      { name: 'helm-repo-ngc/example-service-extra', version },
      { name: 'other/helm-repo-ngc/example-service', version },
    ]],
  ]) {
    await t.test(`returns 3 only after a fresh index confirms absence: ${name}`, (t) => {
      const f = fixture(t);
      const before = snapshot(f.source);
      const result = f.checkExisting({}, { TEST_SEARCH_RESULTS: JSON.stringify(rows) });
      assert.equal(result.status, 3, result.stderr);
      assert.equal(result.signal, null);
      assert.match(result.stdout, /absent from the fresh NGC index/);
      assert.deepEqual(f.events().map(({ args }) => args.slice(0, 2)), [['repo', 'update'], ['search', 'repo']]);
      assert.deepEqual(snapshot(f.source), before);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    });
  }

  const matchingRow = { name: 'helm-repo-ngc/example-service', version };
  for (const [name, options, expectedCalls] of [
    ['index authentication failure', { TEST_REPO_FAILURES: 'always', TEST_ERROR: '401 Unauthorized' }, 1],
    ['index network failure', { TEST_REPO_FAILURES: 'always', TEST_ERROR: 'connection refused' }, 1],
    ['index refresh warns about discarded entries', { TEST_REPO_WARNING: 'true', TEST_SEARCH_RESULTS: '[]' }, 1],
    ['search command failure', { TEST_SEARCH_FAILURE: 'true' }, 2],
    ['search warns about corrupt cache but exits zero', { TEST_SEARCH_WARNING: 'true', TEST_SEARCH_RESULTS: '[]' }, 2],
    ['invalid JSON', { TEST_SEARCH_RESULTS: 'bad json' }, 2],
    ['empty search output', { TEST_SEARCH_RESULTS: '' }, 2],
    ['non-array output', { TEST_SEARCH_RESULTS: '{}' }, 2],
    ['invalid row', { TEST_SEARCH_RESULTS: '[null]' }, 2],
    ['missing version', { TEST_SEARCH_RESULTS: '[{"name":"helm-repo-ngc/example-service"}]' }, 2],
    ['duplicate version rows', { TEST_SEARCH_RESULTS: JSON.stringify([matchingRow, matchingRow]) }, 2],
    ['indexed artifact returns HTTP 404', { TEST_PULL_FAILURES: 'always', TEST_ERROR: '404 Not Found' }, 3],
    ['indexed artifact rejects authentication', { TEST_PULL_FAILURES: 'always', TEST_ERROR: '401 Unauthorized' }, 3],
    ['missing downloaded archive', { TEST_NO_ARCHIVE: 'true' }, 3],
    ['corrupt archive', { TEST_SHOW_FAILURE: 'true' }, 4],
  ]) {
    await t.test(`${name} returns 1, never missing or a prepare/publish operation`, (t) => {
      const f = fixture(t);
      const before = snapshot(f.source);
      const result = f.checkExisting(f.publishedArtifact(), options);
      failure(result);
      assert.equal(result.status, 1, result.stderr);
      const events = f.events();
      assert.equal(events.length, expectedCalls);
      assert.ok(events.every(({ command, args }) => command === 'helm' &&
        ['repo', 'search', 'pull', 'show'].includes(args[0])));
      assert.deepEqual(snapshot(f.source), before);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    });
  }

  await t.test('mismatched existing metadata returns 1 without source changes', (t) => {
    const f = fixture(t);
    const before = snapshot(f.source);
    const result = f.checkExisting(f.publishedArtifact({ annotations: {} }));
    failure(result, /metadata does not match/);
    assert.equal(result.status, 1);
    assert.equal(f.events().length, 4);
    assert.deepEqual(snapshot(f.source), before);
    assert.deepEqual(fs.readdirSync(f.temporary), []);
  });

  await t.test('actual Helm search detects an RC index entry only when prereleases are included', {
    skip: !helm && 'Helm v3 is not installed',
  }, (t) => {
    const f = fixture(t);
    fs.mkdirSync(f.env.HELM_REPOSITORY_CACHE, { recursive: true });
    fs.writeFileSync(f.env.HELM_REPOSITORY_CONFIG, JSON.stringify({
      apiVersion: 'v1', repositories: [{ name: 'helm-repo-ngc', url: 'https://example.invalid/charts' }],
    }));
    fs.writeFileSync(path.join(f.env.HELM_REPOSITORY_CACHE, 'helm-repo-ngc-index.yaml'), JSON.stringify({
      apiVersion: 'v1', entries: { [f.config.name]: [{
        apiVersion: 'v2', name: f.config.name, version, appVersion: version,
        urls: [`https://example.invalid/charts/${f.config.name}-${version}.tgz`],
      }] },
    }));
    const defaultSearch = f.run(helm, ['search', 'repo', `helm-repo-ngc/${f.config.name}`, '--versions', '--output', 'json']);
    success(defaultSearch);
    assert.deepEqual(JSON.parse(defaultSearch.stdout), []);
    success(f.checkExisting(f.publishedArtifact(), { TEST_REAL_SEARCH: 'true' }));
    assert.ok(f.events().find(({ args }) => args[0] === 'search').args.includes('--devel'));
    assert.deepEqual(fs.readdirSync(f.temporary), []);
  });

  await t.test('unknown arguments fail before repository access', (t) => {
    const f = fixture(t);
    const result = f.verify({}, {}, [], ['--unknown']);
    failure(result, /Usage/);
    assert.equal(result.status, 1);
    assert.deepEqual(f.events(), []);
  });
});

test('chart registry verification with explicit Helm registry process fakes', {
  skip: !yq && 'Mike Farah yq v4 is not installed',
}, async (t) => {
  await t.test('verifies an existing matching package with inherited repository authentication and cleans up', (t) => {
    const f = fixture(t);
    const before = snapshot(f.source);
    success(f.verify(f.publishedArtifact(), {}, ['-x']));
    const calls = f.events();
    assert.deepEqual(calls.map(({ args }) => args.slice(0, 2)), [
      ['repo', 'update'], ['pull', 'helm-repo-ngc/example-service'], ['show', 'chart'],
    ]);
    assert.ok(calls.every((call) => call.repositoryConfig === f.env.HELM_REPOSITORY_CONFIG));
    assert.deepEqual(fs.readdirSync(f.temporary), []);
    assert.deepEqual(snapshot(f.source), before);
  });

  for (const [name, changes] of [
    ['name', { name: 'wrong-chart' }], ['version', { version: '2.8.0-rc.2' }],
    ['appVersion', { appVersion: '2.8.0-rc.2' }],
    ['revision', { annotations: { 'dsx.nvidia.com/source-revision': '0'.repeat(40) } }],
    ['missing revision', { annotations: {} }],
  ]) {
    await t.test(`rejects an existing artifact with mismatched ${name} without retrying`, (t) => {
      const f = fixture(t);
      failure(f.verify(f.publishedArtifact(changes)), /metadata does not match/);
      assert.equal(f.events().length, 3);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    });
  }

  for (const [name, options, expectedRepos, expectedPulls, expectedSleeps, ok] of [
    ['repository update recovers', { TEST_REPO_FAILURES: '2' }, 3, 1, 2, true],
    ['pull recovers and clears partial archives', { TEST_PULL_FAILURES: '2' }, 3, 3, 2, true],
    ['last allowed attempt succeeds', { TEST_PULL_FAILURES: '5' }, 6, 6, 5, true],
    ['repository update exhausts retries', { TEST_REPO_FAILURES: 'always' }, 6, 0, 5, false],
    ['pull exhausts retries', { TEST_PULL_FAILURES: 'always' }, 6, 6, 5, false],
  ]) {
    await t.test(name, (t) => {
      const f = fixture(t);
      const result = f.verify(f.publishedArtifact(), options);
      if (ok) success(result);
      else failure(result, /after 6 attempts/);
      assert.doesNotMatch(`${result.stdout}${result.stderr}`, /fake-secret-never-log/);
      const events = f.events();
      assert.equal(events.filter((entry) => entry.args[0] === 'repo').length, expectedRepos);
      assert.equal(events.filter((entry) => entry.args[0] === 'pull').length, expectedPulls);
      assert.deepEqual(events.filter((entry) => entry.command === 'sleep').map((entry) => entry.args),
        Array.from({ length: expectedSleeps }, () => ['10']));
      assert.equal(events.filter((entry) => entry.args[0] === 'show').length, ok ? 1 : 0);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    });
  }

  for (const [name, options, message] of [
    ['missing archive', { TEST_NO_ARCHIVE: 'true' }, /expected chart archive/],
    ['helm show error', { TEST_SHOW_FAILURE: 'true' }, /Unable to read/],
  ]) {
    await t.test(`rejects ${name} and cleans up`, (t) => {
      const f = fixture(t);
      failure(f.verify(f.publishedArtifact(), options), message);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
      assert.equal(f.events().filter((entry) => entry.command === 'sleep').length, 0);
    });
  }

  for (const raw of ['name: [invalid\n', '', `name: wrong\n---\nname: example-service\nversion: ${version}\nappVersion: ${version}\nannotations:\n  dsx.nvidia.com/source-revision: ${revision}\n`]) {
    await t.test('rejects invalid, empty, or multi-document metadata and cleans up', (t) => {
      const f = fixture(t);
      failure(f.verify(f.publishedArtifact({}, raw)), /metadata does not match/);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    });
  }

  await t.test('yq process errors are fatal and never print raw diagnostics', (t) => {
    const f = fixture(t);
    const artifact = f.publishedArtifact();
    fs.unlinkSync(path.join(f.bin, 'yq'));
    f.executable('yq', 'console.error(process.env.TEST_SECRET); process.exit(1);');
    failure(f.verify(artifact), /metadata does not match/);
    assert.deepEqual(fs.readdirSync(f.temporary), []);
  });

  await t.test('rejects missing or unsafe inputs before any Helm operation', (t) => {
    const f = fixture(t);
    for (const options of [{ CHART_NAME: '' }, { CHART_NAME: '../escape' }, { RELEASE_VERSION: '' },
      { RELEASE_VERSION: '--unsafe' }, { EXPECTED_REVISION: '' }, { EXPECTED_REVISION: 'short' }]) {
      failure(f.verify({}, options), /must be/);
      assert.deepEqual(f.events(), []);
      assert.deepEqual(fs.readdirSync(f.temporary), []);
    }
  });
});
