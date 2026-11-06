#!/bin/bash
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

if [[ "$GITHUB_EVENT_NAME" == workflow_dispatch && "$GITHUB_REF" == "refs/heads/${DEFAULT_BRANCH}" ]] ||
   [[ "$GITHUB_EVENT_NAME" == release && "$GITHUB_REF" == "refs/tags/${TAG_VERSION}" ]] ||
   [[ "$GITHUB_EVENT_NAME" == push && "$GITHUB_REF" == "refs/tags/${TAG_VERSION}" ]]; then
  source_ref="refs/remotes/origin/${DEFAULT_BRANCH}"
  if [[ "$GITHUB_EVENT_NAME" == workflow_dispatch && -n "${INPUT_TAG:-}" && "$INPUT_TAG" == "$TAG_VERSION" ]]; then
    [[ "$TAG_VERSION" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]] || exit 1
    release_version="${BASH_REMATCH[1]}"
    if git merge-base --is-ancestor "refs/tags/${TAG_VERSION}" "$source_ref"; then
      exit 0
    fi
    # A manually selected version can include fixes from its release branch.
    source_ref="refs/remotes/origin/release/${release_version}"
  elif [[ "$TAG_VERSION" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)-rc\.[0-9]+$ ]]; then
    [[ "${PUBLISH_RC:-false}" == true && "${REGISTER_VERSIONS:-true}" == false ]] || exit 1
    # RC fixes can exist only on the protected branch for this release version.
    source_ref="refs/remotes/origin/release/${BASH_REMATCH[1]}"
  elif ! [[ "$TAG_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    exit 1
  fi
  if git merge-base --is-ancestor "refs/tags/${TAG_VERSION}" "$source_ref"; then
    exit 0
  fi
  if [[ "$TAG_VERSION" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ &&
        "${PUBLISH_RC:-false}" == true && "${REGISTER_VERSIONS:-true}" == false ]]; then
    version="${BASH_REMATCH[1]}"
    source_ref="refs/remotes/origin/release/${version}"
    # Promotion retains the RC commit. Require both the matching release branch
    # and an RC tag for this version, including annotated tags.
    if git merge-base --is-ancestor "refs/tags/${TAG_VERSION}" "$source_ref"; then
      stable_commit=$(git rev-parse --verify "refs/tags/${TAG_VERSION}^{commit}")
      while IFS= read -r rc_tag; do
        if [[ "$rc_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$ ]] &&
           [[ "$(git rev-parse --verify "refs/tags/${rc_tag}^{commit}")" == "$stable_commit" ]]; then
          exit 0
        fi
      done < <(git tag --list "v${version}-rc.*")
    fi
  fi
  echo "::error::Release tag must be reachable from ${source_ref}"
  exit 1
fi
echo '::error::Publishing requires a matching release tag event or default-branch dispatch'
exit 1
