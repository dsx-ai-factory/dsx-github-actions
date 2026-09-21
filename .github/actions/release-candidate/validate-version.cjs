// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

function releaseTarget(branch) {
  const match = /^release\/((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$/.exec(branch || "");
  if (!match || match[0] !== branch) {
    throw new Error("RC publishing requires an exact release/X.Y.Z branch");
  }
  return match[1];
}

function validateVersion(branch, version) {
  const prefix = `${releaseTarget(branch)}-rc.`;
  if (typeof version !== "string" || !version.startsWith(prefix)) {
    throw new Error("Calculated RC version does not match the release branch target");
  }
  const number = version.slice(prefix.length);
  const match = /^[1-9][0-9]*$/.exec(number);
  if (!match || match[0] !== number) {
    throw new Error("RC version must end with a positive integer without leading zeros");
  }
}

function verifyRelease(_pluginConfig, { branch, nextRelease, env = {} }) {
  validateVersion(branch.name, nextRelease.version);
  if (env.GITHUB_SHA && nextRelease.gitHead !== env.GITHUB_SHA) {
    throw new Error("RC source commit does not match this workflow run");
  }
}

module.exports = { releaseTarget, validateVersion, verifyRelease };

if (require.main === module) {
  try {
    validateVersion(process.env.GITHUB_REF_NAME, process.env.RC_VERSION);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
