#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
action_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
export SCAN_LAYOUT="$test_root/candidate" SCAN_CACHE="$test_root/cache"
export SYFT_IMAGE=syft GRYPE_IMAGE=grype SKOPEO_IMAGE=skopeo
export DOCKER_CONFIG="$test_root/auth" GITHUB_STEP_SUMMARY="$test_root/summary"
export SCAN_TAGS=$'example.test/image:first\nexample.test/image:second'
mkdir -p "$SCAN_LAYOUT/blobs/sha256" "$test_root/bin" "$DOCKER_CONFIG" "$test_root/caller"
ln -s "$action_dir/tests/fake-docker.sh" "$test_root/bin/docker"
export PATH="$test_root/bin:$PATH"

write_blob() {
  local data="$1" digest
  digest="$(printf '%s' "$data" | sha256sum)"
  digest="${digest%% *}"
  printf '%s' "$data" > "$SCAN_LAYOUT/blobs/sha256/$digest"
  printf 'sha256:%s\n' "$digest"
}
make_image() {
  local arch="$1" config
  config="$(write_blob "$(jq -nc --arg arch "$arch" '{os:"linux",architecture:$arch}')")"
  write_blob "$(jq -nc --arg config "$config" '{schemaVersion:2,config:{digest:$config},layers:[]}')"
}
amd64="$(make_image amd64)"
arm64="$(make_image arm64)"
export SCAN_DIGEST
SCAN_DIGEST="$(write_blob "$(jq -nc --arg amd64 "$amd64" --arg arm64 "$arm64" '
  {schemaVersion:2, manifests:[
    {digest:$amd64,platform:{os:"linux",architecture:"amd64"}},
    {digest:$arm64,platform:{os:"linux",architecture:"arm64"}},
    {digest:"attestation-not-a-runtime-image",annotations:{"vnd.docker.reference.type":"attestation-manifest"}}
  ]}')")"
jq -n --arg digest "$SCAN_DIGEST" '{manifests:[{digest:$digest}]}' > "$SCAN_LAYOUT/index.json"
saved_digest="$SCAN_DIGEST"

# Use a different working directory, as when another repository calls this action.
cd "$test_root/caller"
while read -r mode push fail_critical expected scans publications; do
  export FAKE_MODE="$mode" SCAN_PUSH="$push" SCAN_FAIL_ON_CRITICAL="$fail_critical"
  export SCAN_REPORTS="$test_root/$mode-$push-$fail_critical"
  export SCAN_PLATFORMS='linux/amd64,linux/arm64/v8'
  SCAN_DIGEST="$saved_digest"
  jq -n --arg digest "$saved_digest" '{manifests:[{digest:$digest}]}' > "$SCAN_LAYOUT/index.json"
  if [[ "$mode" == duplicate-tags ]]; then
    jq -n --arg digest "$saved_digest" '{manifests:[{digest:$digest},{digest:$digest}]}' > "$SCAN_LAYOUT/index.json"
  fi
  if [[ "$mode" == single-arch ]]; then
    SCAN_DIGEST="$amd64"
    SCAN_PLATFORMS=linux/amd64
    jq -n --arg digest "$amd64" '{manifests:[{digest:$digest}]}' > "$SCAN_LAYOUT/index.json"
  fi
  if [[ "$mode" == missing-platform ]]; then SCAN_PLATFORMS='linux/amd64,linux/arm/v7'; fi
  if [[ "$mode" == wrong-build-digest ]]; then SCAN_DIGEST="${amd64}"; fi
  rc=0
  bash "$action_dir/scripts/scan-and-publish.sh" > "$test_root/log" 2>&1 || rc=$?
  if [[ "$expected" == pass && "$rc" != 0 || "$expected" == fail && "$rc" == 0 ]]; then
    cat "$test_root/log"
    printf 'Unexpected result: %s (exit %s)\n' "$mode" "$rc" >&2
    exit 1
  fi
  actual_scans=0
  actual_publications=0
  if [[ -f "$SCAN_REPORTS/calls" ]]; then
    actual_scans="$(awk '$1 == "grype" {n++} END {print n+0}' "$SCAN_REPORTS/calls")"
    actual_publications="$(awk '$1 == "skopeo" {n++} END {print n+0}' "$SCAN_REPORTS/calls")"
  fi
  [[ "$actual_scans" == "$scans" && "$actual_publications" == "$publications" ]] || {
    printf 'Wrong scan/publish counts for %s: %s/%s\n' "$mode" "$actual_scans" "$actual_publications" >&2
    exit 1
  }
  if [[ "$expected" == pass ]]; then
    while IFS=$'\t' read -r platform digest; do
      jq -e --arg digest "$digest" '.source.target.manifestDigest == $digest' \
        "$SCAN_REPORTS/${platform//\//-}.grype.json" >/dev/null
    done < "$SCAN_REPORTS/platforms.tsv"
  fi
  printf 'PASS %s (push=%s, fail-on-critical=%s)\n' "$mode" "$push" "$fail_critical"
done <<'CASES'
clean true true pass 2 2
clean false true pass 2 0
duplicate-tags true true pass 2 2
single-arch true true pass 1 2
arm-critical true true fail 2 0
arm-critical true false pass 2 2
syft-error true false fail 0 0
grype-error true false fail 1 0
syft-wrong-manifest true false fail 0 0
grype-wrong-manifest true false fail 1 0
missing-report true false fail 1 0
missing-platform true true fail 0 0
wrong-build-digest true true fail 0 0
publish-error true true fail 2 1
publish-wrong-digest true true fail 2 1
CASES

printf 'docker-build scan/publish tests passed\n'
