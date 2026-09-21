// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

function fail(message) {
  throw new Error(message);
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function chartName(value) {
  return typeof value === 'string' && /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/.exec(value)?.[0] === value;
}

function run(command, args, options = {}) {
  try {
    return execFileSync(command, args, {
      encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], ...options,
    });
  } catch {
    // Helm errors can contain authenticated repository URLs; never echo them.
    fail(`${command} failed while preparing the chart`);
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args.length > 1 || (args.length === 1 && args[0] !== '--validate-only')) {
    fail('Usage: node prepare-chart.cjs [--validate-only]');
  }
  const validateOnly = args[0] === '--validate-only';
  let config;
  try {
    config = JSON.parse(process.env.RC_CHART);
  } catch {
    fail('RC_CHART must be a JSON object');
  }
  if (!object(config) || !chartName(config.name)) fail('RC_CHART requires a valid chart name');
  const localDependencies = config.localDependencies === undefined ? [] : config.localDependencies;
  if (!Array.isArray(localDependencies)) fail('localDependencies must be an array');

  const checkout = fs.realpathSync(process.cwd());
  function inside(candidate) {
    const relative = path.relative(checkout, candidate);
    return relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
  }

  function resolveInside(value, base, label, relativeOnly = false) {
    if (typeof value !== 'string' || !value.trim() || /[\x00-\x1f\x7f]/.test(value) ||
        (relativeOnly && path.isAbsolute(value))) {
      fail(`${label} must be a checkout-relative path`);
    }
    const candidate = path.resolve(base, value);
    if (!inside(candidate)) fail(`${label} escapes the checkout`);
    // Check every ancestor, not just the final realpath, before reading YAML.
    let current = checkout;
    for (const part of path.relative(checkout, candidate).split(path.sep).filter(Boolean)) {
      current = path.join(current, part);
      let real;
      try {
        real = fs.realpathSync(current);
      } catch {
        fail(`${label} does not resolve to an existing path`);
      }
      if (!inside(real)) fail(`${label} symlink escapes the checkout`);
    }
    return fs.realpathSync(candidate);
  }

  const scanned = new Set();
  function checkTree(entry, ancestors = new Set()) {
    const real = resolveInside(entry, checkout, 'Chart content');
    const stat = fs.statSync(real);
    if (stat.isFile()) return;
    if (!stat.isDirectory()) fail('Chart content must contain only files and directories');
    if (ancestors.has(real)) fail('Chart content contains a directory symlink cycle');
    if (scanned.has(real)) return;
    const next = new Set([...ancestors, real]);
    for (const name of fs.readdirSync(real)) checkTree(path.join(real, name), next);
    scanned.add(real);
  }

  const charts = new Map();
  const visiting = new Set();
  function readChart(directory, expectedName) {
    if (!fs.statSync(directory).isDirectory()) fail('Chart path must be a directory');
    if (visiting.has(directory)) fail('Local chart dependencies contain a cycle');
    let chart = charts.get(directory);
    if (!chart) {
      checkTree(directory);
      const file = resolveInside('Chart.yaml', directory, 'Chart.yaml');
      if (!fs.statSync(file).isFile()) fail('Chart.yaml must be a regular file');
      let metadata;
      try {
        metadata = JSON.parse(run('yq', ['eval', '-o=json', '.', file]));
      } catch {
        fail('Chart.yaml must be one valid YAML document readable by yq');
      }
      if (!object(metadata) || !chartName(metadata.name)) fail('Chart.yaml requires a valid chart name');
      if (metadata.annotations !== undefined && metadata.annotations !== null && !object(metadata.annotations)) {
        fail('Chart annotations must be a mapping');
      }
      const dependencies = metadata.dependencies === undefined ? [] : metadata.dependencies;
      if (!Array.isArray(dependencies)) fail('Chart dependencies must be an array');
      chart = { directory, file, metadata, dependencies, local: new Map() };
      charts.set(directory, chart);
      visiting.add(directory);
      for (const [index, dependency] of dependencies.entries()) {
        if (!object(dependency) || !chartName(dependency.name) ||
            (dependency.repository !== undefined && typeof dependency.repository !== 'string')) {
          fail('Chart dependency requires a valid name and repository');
        }
        if (dependency.repository?.startsWith('file://')) {
          const target = resolveInside(dependency.repository.slice(7), directory, 'file:// dependency');
          chart.local.set(index, readChart(target, dependency.name));
        }
      }
      visiting.delete(directory);
    }
    if (chart.metadata.name !== expectedName) fail('Declared chart name does not match Chart.yaml');
    return chart;
  }

  const rootPath = resolveInside(config.path, checkout, 'RC_CHART.path', true);
  const root = readChart(rootPath, config.name);
  const alignments = [];
  const names = new Set();
  const directories = new Set([root.directory]);
  for (const dependency of localDependencies) {
    if (!object(dependency) || !chartName(dependency.name)) fail('localDependencies requires valid names');
    if (names.has(dependency.name)) fail('localDependencies contains a duplicate name');
    names.add(dependency.name);
    const directory = resolveInside(dependency.path, checkout, 'localDependencies.path', true);
    if (directories.has(directory)) fail('localDependencies contains a duplicate or root chart path');
    directories.add(directory);
    const chart = readChart(directory, dependency.name);
    const matches = root.dependencies.flatMap((entry, index) => entry.name === dependency.name ? [index] : []);
    if (matches.length !== 1) fail('Local dependency must match exactly one parent dependency');
    const index = matches[0];
    if (root.local.get(index)?.directory !== directory) {
      fail('Local dependency path does not match the parent file:// resolution');
    }
    alignments.push({ chart, index });
  }

  if (validateOnly) return;
  const version = process.env.RELEASE_VERSION;
  const revision = process.env.EXPECTED_REVISION;
  if (!version || /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*$/.exec(version)?.[0] !== version) {
    fail('RELEASE_VERSION must be a canonical X.Y.Z-rc.N version');
  }
  if (!revision || /^[0-9a-f]{40}$/.exec(revision)?.[0] !== revision) fail('EXPECTED_REVISION must be a full Git commit SHA');

  // Complete all structural validation before the first write or Helm invocation.
  const stampVersion = '.version = strenv(RELEASE_VERSION) | .appVersion = strenv(RELEASE_VERSION)';
  for (const { chart } of alignments) run('yq', ['eval', '-i', stampVersion, chart.file]);
  const stampRoot = [
    stampVersion,
    '.annotations = (.annotations // {})',
    '.annotations."dsx.nvidia.com/source-revision" = strenv(EXPECTED_REVISION)',
    ...alignments.map(({ index }) => `.dependencies[${index}].version = strenv(RELEASE_VERSION)`),
  ].join(' | ');
  run('yq', ['eval', '-i', stampRoot, root.file]);
  if (root.dependencies.length) {
    run('helm', ['dependency', 'update', '.'], { cwd: root.directory });
  }
}

try {
  main();
} catch (error) {
  // Filesystem errors may expose paths; only report our deliberate diagnostics.
  console.error(`Chart preparation failed: ${error.code ? 'unable to access chart files' : error.message}`);
  process.exitCode = 1;
}
