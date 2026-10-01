#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/anonymous-build-test.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
original="${DOCKER_CONFIG:-$HOME/.docker}"
mkdir "$test_dir/inherited"
# Deliberately invalid NGC credentials must never reach the public base pull.
if [[ -f "$original/config.json" ]]; then
  jq '{auths: {"nvcr.io": {auth: "aW52YWxpZDppbnZhbGlk"}},
    credsStore: "must-not-be-used", credHelpers: {"nvcr.io": "must-not-be-used"}} +
    (if has("currentContext") then {currentContext} else {} end)' \
    "$original/config.json" > "$test_dir/inherited/config.json"
else
  echo '{"auths":{"nvcr.io":{"auth":"aW52YWxpZDppbnZhbGlk"}},"credsStore":"must-not-be-used","credHelpers":{"nvcr.io":"must-not-be-used"}}' > "$test_dir/inherited/config.json"
fi
jq '.proxies = {default: {
  httpProxy: "http://proxy.example:3128",
  httpsProxy: "http://proxy.example:3129",
  noProxy: "localhost,Exact.example"
}}' "$test_dir/inherited/config.json" > "$test_dir/proxy-config.json"
mv "$test_dir/proxy-config.json" "$test_dir/inherited/config.json"
for directory in contexts buildx cli-plugins; do
  if [[ -d "$original/$directory" ]]; then
    ln -s "$(cd "$original/$directory" && pwd)" "$test_dir/inherited/$directory"
  fi
done
export DOCKER_CONFIG="$test_dir/inherited"
export RUNNER_TEMP="$test_dir"
export GITHUB_OUTPUT="$test_dir/output"
ANONYMOUS_BUILD=true bash "$root/scripts/prepare-build-config.sh"
build_config="$(sed -n 's/^config=//p' "$GITHUB_OUTPUT")"
# Exercise BuildKit against the real public base with inherited credentials present.
DOCKER_CONFIG="$build_config" docker buildx build --pull --no-cache \
  --platform linux/amd64,linux/arm64 \
  --output "type=oci,dest=$test_dir/image,tar=false" \
  --metadata-file "$test_dir/metadata.json" "$root/tests"
digest="$(jq -er '."containerimage.digest"' "$test_dir/metadata.json")"
jq -e '[.manifests[] | select(.platform.os == "linux") | .platform.architecture] |
  sort == ["amd64", "arm64"]' "$test_dir/image/blobs/sha256/${digest#sha256:}"
