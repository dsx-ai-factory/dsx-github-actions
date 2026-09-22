#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Catalog each runtime manifest once, whether or not vulnerability scanning is enabled.
set -euo pipefail
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }
: "${OCI_LAYOUT:?}" "${EXPECTED_DIGEST:?}" "${PLATFORMS:?}" "${REPORTS:?}" "${SYFT_IMAGE:?}"
mkdir -p "$REPORTS"
blob_path() { printf '%s/blobs/sha256/%s\n' "$OCI_LAYOUT" "${1#sha256:}"; }

jq -e --arg digest "$EXPECTED_DIGEST" \
  '.manifests | length > 0 and all(.[]; .digest == $digest)' "$OCI_LAYOUT/index.json" >/dev/null \
  || fail 'OCI export does not match the Buildx output digest'
# Normalize only the layout catalog; never rewrite a content-addressed manifest.
jq '.manifests = [.manifests[0]]' "$OCI_LAYOUT/index.json" > "$OCI_LAYOUT/index.scan.json"
mv "$OCI_LAYOUT/index.scan.json" "$OCI_LAYOUT/index.json"
descriptors="$(jq -c --arg digest "$EXPECTED_DIGEST" \
  '(.manifests // [{digest: $digest}])[] | select(.annotations["vnd.docker.reference.type"] != "attestation-manifest")' \
  "$(blob_path "$EXPECTED_DIGEST")")"
: > "$REPORTS/platforms.tsv"
while IFS= read -r descriptor; do
  digest="$(jq -er '.digest' <<< "$descriptor")"
  config="$(blob_path "$(jq -er '.config.digest' "$(blob_path "$digest")")")"
  platform="$(jq -er '[.os, .architecture, (.variant // "")] | map(select(. != "")) | join("/") | sub("^linux/arm64/v8$"; "linux/arm64")' "$config")"
  printf '%s\t%s\n' "$platform" "$digest" >> "$REPORTS/platforms.tsv"
done <<< "$descriptors"
requested="$(jq -ner --arg platforms "$PLATFORMS" '
  $platforms | gsub(","; "\n") | split("\n") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))
  | map(sub("^linux/arm64/v8$"; "linux/arm64")) | sort
  | if length > 0 and all(.[]; test("^linux/[a-z0-9_]+(/[a-z0-9_.-]+)?$")) and length == (unique | length)
    then .[] else error("Use explicit, distinct Linux platforms") end')"
actual="$(cut -f1 "$REPORTS/platforms.tsv" | LC_ALL=C sort)"
[[ "$requested" == "$actual" ]] || fail 'Requested and exported platforms do not match'

tool_tmp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/docker-sbom.XXXXXX")"
trap 'rm -rf "$tool_tmp"' EXIT
while IFS=$'\t' read -r platform digest; do
  key="${platform//\//-}"
  docker run --rm --user "$(id -u):$(id -g)" \
    --cap-drop=ALL --security-opt=no-new-privileges --network=none --env SYFT_CHECK_FOR_APP_UPDATE=false \
    --volume "$tool_tmp:/tmp" --env XDG_CACHE_HOME=/tmp/.cache \
    --volume "$OCI_LAYOUT:/candidate:ro" --volume "$REPORTS:/reports" \
    "$SYFT_IMAGE" scan oci-dir:/candidate --platform "$platform" \
    -o "syft-json=/reports/$key.syft.json" -o "spdx-json=/reports/$key.spdx.json"
  jq -e --arg digest "$digest" '.source.metadata.manifestDigest == $digest' \
    "$REPORTS/$key.syft.json" >/dev/null || fail "Syft scanned the wrong manifest for $platform"
  jq -e '.spdxVersion and .SPDXID == "SPDXRef-DOCUMENT"' \
    "$REPORTS/$key.spdx.json" >/dev/null || fail "Invalid SPDX report for $platform"
done < "$REPORTS/platforms.tsv"
jq -Rn --arg digest "$EXPECTED_DIGEST" \
  '{digest: $digest, platforms: [inputs | split("\t") | {platform: .[0], digest: .[1]}]}' \
  < "$REPORTS/platforms.tsv" > "$REPORTS/image.json"
