#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
node "$(dirname "$0")/validate-version.cjs"
[[ "$RC_TAG" == "v${RC_VERSION}" ]]
[[ "$(git rev-parse "${RC_TAG}^{commit}")" == "$GITHUB_SHA" ]]
info="$(mktemp)"
errors="$(mktemp)"
trap 'rm -f "$info" "$errors"' EXIT
endpoint="repos/${GITHUB_REPOSITORY}/releases/tags/${RC_TAG}"
if ! gh api "$endpoint" > "$info" 2> "$errors"; then
  if ! grep -Fq '(HTTP 404)' "$errors"; then
    cat "$errors" >&2
    exit 1
  fi
  # Retry only a missing release. Never manufacture or move its Git tag.
  gh release create "$RC_TAG" --verify-tag --prerelease --title "$RC_TAG" --generate-notes
  gh api "$endpoint" > "$info"
fi
jq -e --arg tag "$RC_TAG" '.prerelease == true and .draft == false and .tag_name == $tag' "$info"
url="$(jq -er '.html_url' "$info")"
printf 'release-url=%s\n' "$url" >> "$GITHUB_OUTPUT"
{
  printf '## Release candidate\n\nTag: %s\n\n%s\n\n' "$RC_TAG" "$url"
  printf 'Source tag and GitHub prerelease verified. Images and charts require separate publishing jobs.\n'
} >> "$GITHUB_STEP_SUMMARY"
