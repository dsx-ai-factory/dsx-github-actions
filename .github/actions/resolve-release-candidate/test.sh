#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

action_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
resolver="${action_dir}/resolve.sh"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

assert_output() {
  local output_file="$1"
  local expected="$2"
  grep -Fxq "$expected" "$output_file"
}

run_resolver() {
  local repo="$1"
  local output_file="$2"
  shift 2
  (
    cd "$repo"
    GITHUB_OUTPUT="$output_file" "$@" "$resolver"
  )
}

repo="${test_root}/repo"
mkdir "$repo"
git -C "$repo" init --quiet
git -C "$repo" config user.name test
git -C "$repo" config user.email test@example.com
printf 'fixture\n' > "${repo}/fixture.txt"
git -C "$repo" add fixture.txt
git -C "$repo" commit --quiet -m fixture

output="${test_root}/published-rc.out"
run_resolver "$repo" "$output" env \
  NEW_RELEASE_PUBLISHED=true \
  NEW_RELEASE_VERSION=1.2.3-rc.4 \
  NEW_RELEASE_GIT_TAG=v1.2.3-rc.4
assert_output "$output" 'should-publish=true'
assert_output "$output" 'version=1.2.3-rc.4'
assert_output "$output" 'tag=v1.2.3-rc.4'
assert_output "$output" 'reused-existing-tag=false'

output="${test_root}/published-invalid-leading-zero.out"
run_resolver "$repo" "$output" env \
  NEW_RELEASE_PUBLISHED=true \
  NEW_RELEASE_VERSION=01.2.3-rc.01 \
  NEW_RELEASE_GIT_TAG=v01.2.3-rc.01
assert_output "$output" 'should-publish=false'

output="${test_root}/published-final.out"
run_resolver "$repo" "$output" env \
  NEW_RELEASE_PUBLISHED=true \
  NEW_RELEASE_VERSION=1.2.3 \
  NEW_RELEASE_GIT_TAG=v1.2.3
assert_output "$output" 'should-publish=false'

git -C "$repo" tag v1.2.4-rc.1
output="${test_root}/rerun.out"
run_resolver "$repo" "$output" env NEW_RELEASE_PUBLISHED=false
assert_output "$output" 'should-publish=true'
assert_output "$output" 'version=1.2.4-rc.1'
assert_output "$output" 'reused-existing-tag=true'

git -C "$repo" tag v1.2.4-rc.2
output="${test_root}/ambiguous.out"
if run_resolver "$repo" "$output" env NEW_RELEASE_PUBLISHED=false; then
  printf 'expected multiple RC tags on HEAD to fail\n' >&2
  exit 1
fi

git -C "$repo" tag -d v1.2.4-rc.1 v1.2.4-rc.2 >/dev/null
git -C "$repo" tag v01.2.4-rc.01
output="${test_root}/rerun-invalid-leading-zero.out"
run_resolver "$repo" "$output" env NEW_RELEASE_PUBLISHED=false
assert_output "$output" 'should-publish=false'

git -C "$repo" tag -d v01.2.4-rc.01 >/dev/null
output="${test_root}/no-release.out"
run_resolver "$repo" "$output" env NEW_RELEASE_PUBLISHED=false
assert_output "$output" 'should-publish=false'

printf 'resolve-release-candidate tests passed\n'
