#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

published="${NEW_RELEASE_PUBLISHED:-false}"
version="${NEW_RELEASE_VERSION:-}"
tag="${NEW_RELEASE_GIT_TAG:-}"
identifier="${PRERELEASE_IDENTIFIER:-rc}"
tag_prefix="${TAG_PREFIX:-v}"

if [[ "$published" != "true" && "$published" != "false" ]]; then
  printf 'new-release-published must be true or false, got %q\n' "$published" >&2
  exit 1
fi

if [[ ! "$identifier" =~ ^[0-9A-Za-z-]+$ ]]; then
  printf 'prerelease-identifier must contain only letters, digits, and hyphens\n' >&2
  exit 1
fi

if [[ -z "${GITHUB_OUTPUT:-}" ]]; then
  printf 'GITHUB_OUTPUT must be set\n' >&2
  exit 1
fi

is_release_candidate() {
  local candidate_version="$1"
  local numeric_identifier='(0|[1-9][0-9]*)'
  [[ "$candidate_version" =~ ^${numeric_identifier}\.${numeric_identifier}\.${numeric_identifier}-${identifier}\.${numeric_identifier}$ ]]
}

write_outputs() {
  local should_publish="$1"
  local resolved_version="$2"
  local resolved_tag="$3"
  local reused_existing_tag="$4"

  {
    printf 'should-publish=%s\n' "$should_publish"
    printf 'version=%s\n' "$resolved_version"
    printf 'tag=%s\n' "$resolved_tag"
    printf 'reused-existing-tag=%s\n' "$reused_existing_tag"
  } >> "$GITHUB_OUTPUT"
}

if [[ "$published" == "true" ]]; then
  if [[ -z "$version" || -z "$tag" ]]; then
    printf 'semantic-release reported a release without both version and tag outputs\n' >&2
    exit 1
  fi

  if [[ "$tag" != "${tag_prefix}${version}" ]]; then
    printf 'release tag %q does not match expected tag %q\n' \
      "$tag" "${tag_prefix}${version}" >&2
    exit 1
  fi

  if is_release_candidate "$version"; then
    write_outputs true "$version" "$tag" false
    printf 'Selected newly published release candidate %s\n' "$tag"
  else
    write_outputs false "" "" false
    printf 'Release %s is not an accepted release candidate; artifact publishing is disabled\n' "$tag"
  fi
  exit 0
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'A Git checkout is required to detect an existing RC tag during reruns\n' >&2
  exit 1
fi

matching_tags=()
while IFS= read -r candidate; do
  [[ -z "$candidate" ]] && continue
  [[ "$candidate" == "$tag_prefix"* ]] || continue
  candidate_version="${candidate:${#tag_prefix}}"
  if is_release_candidate "$candidate_version"; then
    matching_tags+=("$candidate")
  fi
done < <(git tag --points-at HEAD)

if (( ${#matching_tags[@]} == 0 )); then
  write_outputs false "" "" false
  printf 'No release candidate was published or found on HEAD\n'
  exit 0
fi

if (( ${#matching_tags[@]} > 1 )); then
  printf 'Multiple release candidate tags point at HEAD: %s\n' "${matching_tags[*]}" >&2
  exit 1
fi

tag="${matching_tags[0]}"
version="${tag:${#tag_prefix}}"
write_outputs true "$version" "$tag" true
printf 'Reusing existing release candidate %s for this workflow rerun\n' "$tag"
