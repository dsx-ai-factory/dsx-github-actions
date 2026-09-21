// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdir, mkdtemp, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { delimiter, dirname, join } = require('node:path');

async function createFixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'shared-rc-test-'));
  const active = new Set();
  t.after(async () => {
    for (const child of active) child.stop();
    await Promise.allSettled([...active].map((child) => child.done));
    await rm(root, { recursive: true, force: true });
  });

  const home = join(root, 'home');
  const temp = join(root, 'tmp');
  await mkdir(home);
  await mkdir(temp);

  // Never inherit credentials, CI detection, Git overrides, or Node preload flags.
  const env = {
    PATH: [dirname(process.execPath), '/usr/bin', '/bin'].join(delimiter),
    HOME: home,
    XDG_CONFIG_HOME: join(home, '.config'),
    TMPDIR: temp,
    TMP: temp,
    TEMP: temp,
    LANG: 'C',
    LC_ALL: 'C',
    TZ: 'UTC',
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_SYSTEM: '/dev/null',
    GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_CONFIG_COUNT: '0',
    GIT_ALLOW_PROTOCOL: 'file',
    GIT_TERMINAL_PROMPT: '0',
    GIT_ASKPASS: '/usr/bin/false',
    SSH_ASKPASS: '/usr/bin/false',
    GIT_SSH_COMMAND: '/usr/bin/false',
    GIT_PAGER: 'cat',
  };

  async function run(command, args, { cwd = root, extraEnv = {}, timeout = 30_000 } = {}) {
    const child = spawn(command, args, {
      cwd,
      env: { ...env, ...extraEnv },
      detached: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let failure;
    const stop = () => {
      if (!child.pid) return;
      try {
        // Terminate the whole group, including semantic-release's Git children.
        process.kill(-child.pid, 'SIGKILL');
      } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    };
    const timer = setTimeout(() => {
      failure = new Error(`${command} exceeded ${timeout}ms`);
      stop();
    }, timeout);
    const capture = (stream, chunk) => {
      if (stream === 'stdout') stdout += chunk;
      else stderr += chunk;
      if (stdout.length + stderr.length > 1024 * 1024) {
        failure = new Error(`${command} exceeded the fixture output limit`);
        stop();
      }
    };
    child.stdout.setEncoding('utf8').on('data', (chunk) => capture('stdout', chunk));
    child.stderr.setEncoding('utf8').on('data', (chunk) => capture('stderr', chunk));
    const done = new Promise((resolve, reject) => {
      child.on('error', (error) => { failure = error; });
      child.on('close', (status, signal) => {
        clearTimeout(timer);
        if (failure) reject(failure);
        else resolve({ status, signal, stdout, stderr });
      });
    });
    const entry = { stop, done };
    active.add(entry);
    try {
      return await done;
    } finally {
      active.delete(entry);
    }
  }

  async function git(cwd, ...args) {
    const result = await run('git', args, { cwd, timeout: 10_000 });
    assert.equal(result.status, 0, `git ${args.join(' ')}: ${result.stderr}`);
    return result.stdout.trim();
  }

  // macOS's system Bash 3 does not apply errexit to failed [[ ... ]] checks.
  const bash = process.env.RC_TEST_BASH || '/bin/bash';
  return { root, run, git, bash };
}

module.exports = { createFixture };
