#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
node -e 'require(process.argv[1]).releaseTarget(process.env.GITHUB_REF_NAME)' "$(dirname "$0")/validate-version.cjs"
[[ "${GITHUB_EVENT_NAME:-}" == "push" ]]
[[ "${GITHUB_REF:-}" == "refs/heads/${GITHUB_REF_NAME}" ]]
if [[ "${RC_REF_PROTECTED:-}" != "true" ]]; then
  echo "Protect the release branch before enabling RC publishing." >&2
  exit 1
fi
[[ "$(git rev-parse --is-shallow-repository)" == "false" ]]
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]]
remote_head="$(git ls-remote --exit-code origin "$GITHUB_REF" | cut -f1)"
if [[ "$remote_head" != "$GITHUB_SHA" ]]; then
  echo "This run is not the current release-branch head. Run the latest commit instead." >&2
  exit 1
fi
