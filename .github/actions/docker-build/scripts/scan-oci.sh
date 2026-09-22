#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Scan every platform in the Buildx OCI export. Publication is a separate step.
set -euo pipefail

fail() { printf '::error::%s\n' "$*" >&2; exit 1; }

: "${SCAN_LAYOUT:?}" "${SCAN_DIGEST:?}" "${SCAN_PLATFORMS:?}" "${SCAN_REPORTS:?}"
: "${SYFT_IMAGE:?}" "${GRYPE_IMAGE:?}"
SCAN_CACHE="${SCAN_CACHE:-$HOME/.cache/grype/db}"
SCAN_FAIL_ON_CRITICAL="${SCAN_FAIL_ON_CRITICAL:-true}"
[[ "$SCAN_FAIL_ON_CRITICAL" == true || "$SCAN_FAIL_ON_CRITICAL" == false ]] || fail 'Invalid Critical policy'
mkdir -p "$SCAN_REPORTS" "$SCAN_CACHE"

# The layout comes directly from the preceding Buildx step.
blob_path() { printf '%s/blobs/sha256/%s\n' "$SCAN_LAYOUT" "${1#sha256:}"; }

jq -e --arg digest "$SCAN_DIGEST" \
  '.manifests | length > 0 and all(.[]; .digest == $digest)' "$SCAN_LAYOUT/index.json" >/dev/null \
  || fail 'OCI export does not match the Buildx output digest'
# Buildx emits one catalog entry per tag, all pointing to the same root blob.
# Keep one alias so OCI readers see an unambiguous image. This changes only the
# layout catalog, not the content-addressed index/manifest that gets published.
jq '.manifests = [.manifests[0]]' "$SCAN_LAYOUT/index.json" > "$SCAN_LAYOUT/index.scan.json"
mv "$SCAN_LAYOUT/index.scan.json" "$SCAN_LAYOUT/index.json"
root="$(blob_path "$SCAN_DIGEST")"
# Buildx exports either a single image or an index containing runtime images
# and optional attestations. Keep attestations for publication, not scanning.
descriptors="$(jq -c --arg digest "$SCAN_DIGEST" \
  '(.manifests // [{digest: $digest}])[] | select(.annotations["vnd.docker.reference.type"] != "attestation-manifest")' "$root")"
: > "$SCAN_REPORTS/platforms.tsv"
while IFS= read -r descriptor; do
  digest="$(jq -er '.digest' <<< "$descriptor")"
  manifest="$(blob_path "$digest")"
  config="$(blob_path "$(jq -er '.config.digest' "$manifest")")"
  platform="$(jq -er '[.os, .architecture, (.variant // "")] | map(select(. != "")) | join("/") | sub("^linux/arm64/v8$"; "linux/arm64")' "$config")"
  printf '%s\t%s\n' "$platform" "$digest" >> "$SCAN_REPORTS/platforms.tsv"
done <<< "$descriptors"

requested="$(jq -ner --arg platforms "$SCAN_PLATFORMS" '
  $platforms | gsub(","; "\n") | split("\n") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))
  | map(sub("^linux/arm64/v8$"; "linux/arm64")) | sort
  | if length > 0 and all(.[]; test("^linux/[a-z0-9_]+(/[a-z0-9_.-]+)?$")) and length == (unique | length)
    then .[] else error("Use explicit, distinct Linux platforms") end')"
actual="$(cut -f1 "$SCAN_REPORTS/platforms.tsv" | LC_ALL=C sort)"
[[ "$requested" == "$actual" ]] || fail 'Requested and exported platforms do not match'

# Run as the runner user so reports/cache remain writable on self-hosted runners.
# Use disk-backed scratch space: extracting large images into tmpfs can OOM.
tool_tmp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/docker-build-tools.XXXXXX")"
trap 'rm -rf "$tool_tmp"' EXIT
container=(docker run --rm --user "$(id -u):$(id -g)"
  --cap-drop=ALL --security-opt=no-new-privileges
  --volume "$tool_tmp:/tmp" --env XDG_CACHE_HOME=/tmp/.cache)
blocked=false
printf '\n### Container scan\n\nCandidate: %s\n\n' "$SCAN_DIGEST" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
while IFS=$'\t' read -r platform digest; do
  key="${platform//\//-}"
  printf 'Scanning %s at %s\n' "$platform" "$digest"
  "${container[@]}" --network=none --env SYFT_CHECK_FOR_APP_UPDATE=false \
    --volume "$SCAN_LAYOUT:/candidate:ro" --volume "$SCAN_REPORTS:/reports" \
    "$SYFT_IMAGE" scan oci-dir:/candidate --platform "$platform" \
    -o "syft-json=/reports/$key.syft.json" -o "spdx-json=/reports/$key.spdx.json"
  jq -e --arg digest "$digest" '.source.metadata.manifestDigest == $digest' \
    "$SCAN_REPORTS/$key.syft.json" >/dev/null || fail "Syft scanned the wrong manifest for $platform"
  [[ -s "$SCAN_REPORTS/$key.spdx.json" ]] || fail "Missing SPDX report for $platform"

  # Analyze the platform-specific SBOM; do not catalog a different image.
  rc=0
  "${container[@]}" --volume "$SCAN_REPORTS:/reports" --volume "$SCAN_CACHE:/cache" \
    --env GRYPE_DB_CACHE_DIR=/cache "$GRYPE_IMAGE" "sbom:/reports/$key.syft.json" --fail-on critical \
    -o "json=/reports/$key.grype.json" -o "sarif=/reports/$key.sarif" -o "table=/reports/$key.txt" || rc=$?
  # Grype uses exit 2 for policy findings; other failures always block push.
  case "$rc" in
    0) ;;
    2) if [[ "$SCAN_FAIL_ON_CRITICAL" == true ]]; then blocked=true; fi ;;
    *) fail "Grype failed for $platform (exit $rc)" ;;
  esac
  jq -e --arg digest "$digest" \
    '.source.target.manifestDigest == $digest and (.matches | type == "array")' \
    "$SCAN_REPORTS/$key.grype.json" >/dev/null || fail "Invalid Grype report for $platform"
  criticals="$(jq '[.matches[] | select(.vulnerability.severity == "Critical")] | length' "$SCAN_REPORTS/$key.grype.json")"
  [[ -s "$SCAN_REPORTS/$key.sarif" && -s "$SCAN_REPORTS/$key.txt" ]] || fail "Missing Grype reports for $platform"
  printf -- '- %s: %s Critical (%s)\n' "$platform" "$criticals" "$digest" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
done < "$SCAN_REPORTS/platforms.tsv"
[[ "$blocked" == false ]] || fail 'Critical vulnerability policy failed'
