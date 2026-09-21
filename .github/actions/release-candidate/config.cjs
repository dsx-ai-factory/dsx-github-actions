// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const { execFileSync } = require("node:child_process");
const { writeFileSync } = require("node:fs");
const { releaseTarget } = require("./validate-version.cjs");

function createConfig(branch, defaultBranch) {
  releaseTarget(branch);
  if (!defaultBranch || defaultBranch.startsWith("release/")) {
    throw new Error("A separate default branch is required");
  }
  const checked = execFileSync("git", ["check-ref-format", "--branch", defaultBranch], {
    encoding: "utf8", stdio: "pipe",
  });
  if (checked !== `${defaultBranch}\n`) {
    throw new Error("The default branch must be a literal branch name");
  }
  return {
    branches: [defaultBranch, { name: branch, channel: "rc", prerelease: "rc" }],
    tagFormat: "v${version}",
    plugins: [
      ["@semantic-release/commit-analyzer", { preset: "conventionalcommits" }],
      ["@semantic-release/release-notes-generator", { preset: "conventionalcommits" }],
      require.resolve("./validate-version.cjs"),
      ["@semantic-release/github", {
        successComment: false, failComment: false, releasedLabels: false,
      }],
    ],
  };
}

module.exports = { createConfig };

if (require.main === module) {
  const config = createConfig(process.env.GITHUB_REF_NAME, process.env.RC_DEFAULT_BRANCH);
  const repository = process.env.GITHUB_REPOSITORY || "";
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new Error("A GitHub owner/repository is required");
  }
  config.repositoryUrl = `https://github.com/${repository}.git`;
  // Only the disposable sparse checkout receives a configuration file.
  writeFileSync(".releaserc.json", `${JSON.stringify(config, null, 2)}\n`, { flag: "wx" });
}
