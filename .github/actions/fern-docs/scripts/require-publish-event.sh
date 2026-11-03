#!/bin/bash
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

if [[ "$GITHUB_EVENT_NAME" == workflow_dispatch && "$GITHUB_REF" == "refs/heads/${DEFAULT_BRANCH}" ]] ||
   [[ "$GITHUB_EVENT_NAME" == release && "$GITHUB_REF" == refs/tags/* ]] ||
   [[ "$GITHUB_EVENT_NAME" == push && "$GITHUB_REF" == refs/tags/* ]]; then
  [[ "$TAG_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
  if git merge-base --is-ancestor "refs/tags/${TAG_VERSION}" "refs/remotes/origin/${DEFAULT_BRANCH}"; then
    exit 0
  fi
  echo '::error::Release tag must be reachable from the default branch'
  exit 1
fi
echo '::error::Publishing requires a release tag event or default-branch dispatch'
exit 1
