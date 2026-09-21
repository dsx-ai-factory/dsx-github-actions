// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const namePattern = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

function array(value, label) {
  const entries = JSON.parse(value || '[]');
  if (!Array.isArray(entries) || entries.length > 32) {
    throw new Error(`${label} must be an array with at most 32 entries`);
  }
  return entries;
}

function object(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) throw new Error(`Unknown ${label} field: ${key}`);
  }
}

function sourcePath(value, directory, root) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_./-]+$/.test(value) ||
      path.isAbsolute(value) || value.split('/').includes('..') || value.startsWith('-')) {
    throw new Error(`Invalid repository-relative path: ${value}`);
  }
  const resolved = fs.realpathSync(path.resolve(root, value));
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Path escapes the source checkout: ${value}`);
  }
  const stat = fs.statSync(resolved);
  if (directory ? !stat.isDirectory() : !stat.isFile()) {
    throw new Error(`Wrong source path type: ${value}`);
  }
  return value;
}

function uniqueNames(entries, label) {
  const names = new Set();
  for (const entry of entries) {
    if (typeof entry.name !== 'string' || !namePattern.test(entry.name) || names.has(entry.name)) {
      throw new Error(`${label} names must be valid and unique`);
    }
    names.add(entry.name);
  }
}

function plan(env = process.env, root = fs.realpathSync(process.cwd())) {
  const namespace = env.RC_NGC_PATH || '';
  if (!/^[a-z0-9][a-z0-9_-]*\/[a-z0-9][a-z0-9_-]*$/.test(namespace)) {
    throw new Error('ngc-path must be an organization/team path');
  }
  const images = array(env.RC_IMAGES, 'images').map(image => {
    object(image, ['name', 'context', 'dockerfile'], 'image');
    return {
      name: image.name,
      context: sourcePath(image.context, true, root),
      dockerfile: sourcePath(image.dockerfile, false, root),
    };
  });
  const charts = array(env.RC_CHARTS, 'charts').map(chart => {
    object(chart, ['name', 'path', 'localDependencies'], 'chart');
    const dependencies = chart.localDependencies ?? [];
    if (!Array.isArray(dependencies) || dependencies.length > 32) {
      throw new Error('localDependencies must be an array with at most 32 entries');
    }
    const localDependencies = dependencies.map(dependency => {
      object(dependency, ['name', 'path'], 'local dependency');
      return { name: dependency.name, path: sourcePath(dependency.path, true, root) };
    });
    uniqueNames(localDependencies, 'Local dependency');
    return { name: chart.name, path: sourcePath(chart.path, true, root), localDependencies };
  });
  uniqueNames(images, 'Image');
  uniqueNames(charts, 'Chart');
  if (images.length + charts.length === 0) throw new Error('Declare at least one image or chart');
  for (const chart of charts) {
    execFileSync(process.execPath, [path.join(__dirname, 'prepare-chart.cjs'), '--validate-only'], {
      cwd: root,
      env: { ...process.env, RC_CHART: JSON.stringify(chart) },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  }
  return { images, charts };
}

if (require.main === module) {
  try {
    const result = plan();
    const outputs = {
      images: JSON.stringify(result.images),
      charts: JSON.stringify(result.charts),
      'image-count': result.images.length,
      'chart-count': result.charts.length,
    };
    fs.appendFileSync(process.env.GITHUB_OUTPUT,
      Object.entries(outputs).map(([key, value]) => `${key}=${value}\n`).join(''));
  } catch (error) {
    console.error(`Artifact plan failed: ${error.message}`);
    process.exitCode = 1;
  }
}

module.exports = { plan };
