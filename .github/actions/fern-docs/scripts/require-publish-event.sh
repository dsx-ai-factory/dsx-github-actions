#!/bin/bash
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

if [[ "$GITHUB_EVENT_NAME" == workflow_dispatch && "$GITHUB_REF" == "refs/heads/${DEFAULT_BRANCH}" ]] ||
   [[ "$GITHUB_EVENT_NAME" == release && "$GITHUB_REF" == "refs/tags/${TAG_VERSION}" ]] ||
   [[ "$GITHUB_EVENT_NAME" == push && "$GITHUB_REF" == "refs/tags/${TAG_VERSION}" ]]; then
  source_ref="refs/remotes/origin/${DEFAULT_BRANCH}"
  if [[ "$TAG_VERSION" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)-rc\.[0-9]+$ ]]; then
    [[ "${PUBLISH_RC:-false}" == true && "${REGISTER_VERSIONS:-true}" == false ]] || exit 1
    # RC fixes can exist only on the protected branch for this release version.
    source_ref="refs/remotes/origin/release/${BASH_REMATCH[1]}"
  elif ! [[ "$TAG_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    exit 1
  fi
  if git merge-base --is-ancestor "refs/tags/${TAG_VERSION}" "$source_ref"; then
    exit 0
  fi
  echo "::error::Release tag must be reachable from ${source_ref}"
  exit 1
fi
echo '::error::Publishing requires a matching release tag event or default-branch dispatch'
exit 1
