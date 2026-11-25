#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Scan the SBOMs produced by sbom-oci.sh; do not recatalog or rebuild the image.
set -euo pipefail

fail() { printf '::error::%s\n' "$*" >&2; exit 1; }

: "${SCAN_DIGEST:?}" "${SCAN_REPORTS:?}" "${GRYPE_IMAGE:?}"
SCAN_CACHE="${SCAN_CACHE:-$HOME/.cache/grype/db}"
SCAN_FAIL_ON_CRITICAL="${SCAN_FAIL_ON_CRITICAL:-true}"
[[ "$SCAN_FAIL_ON_CRITICAL" == true || "$SCAN_FAIL_ON_CRITICAL" == false ]] || fail 'Invalid Critical policy'
mkdir -p "$SCAN_REPORTS" "$SCAN_CACHE"

jq -e --arg digest "$SCAN_DIGEST" \
  '.digest == $digest and (.platforms | length > 0)' "$SCAN_REPORTS/image.json" >/dev/null \
  || fail 'SBOM evidence does not match the built image'
platforms="$(jq -er '.platforms[] | [.platform, .digest] | @tsv' "$SCAN_REPORTS/image.json")"

# Run as the runner user so reports/cache remain writable on self-hosted runners.
# Use disk-backed scratch space: extracting large images into tmpfs can OOM.
tool_tmp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/docker-build-tools.XXXXXX")"
trap 'rm -rf "$tool_tmp"' EXIT
container=(docker run --rm --user "$(id -u):$(id -g)"
  --cap-drop=ALL --security-opt=no-new-privileges
  --volume "$tool_tmp:/tmp" --env XDG_CACHE_HOME=/tmp/.cache)
config_args=()
if [[ -n "${SCAN_CONFIG:-}" ]]; then
  [[ -f "$SCAN_CONFIG" && -r "$SCAN_CONFIG" ]] || fail 'Grype config must be a readable file'
  : "${GITHUB_WORKSPACE:?}"
  # Resolve symlinks and ".." before comparing: the config must come from the caller checkout.
  workspace="$(realpath -- "$GITHUB_WORKSPACE")" || fail 'Cannot resolve the workspace'
  config_source="$(realpath -- "$SCAN_CONFIG")" || fail 'Cannot resolve the Grype config'
  [[ "$config_source" == "$workspace"/* ]] || fail 'Grype config must be inside the workspace'
  # Snapshot caller policy once so every platform uses the same configuration.
  cp -- "$config_source" "$tool_tmp/grype.yaml"
  # Callers may add ignore rules, but settings such as db.* or registry.* must stay with the action.
  # List the parsed key nodes: yq's JSON view hides merge keys ("<<") that Grype still applies.
  yq_version="$(yq --version 2>&1)" || fail 'mikefarah yq is required to validate the Grype config'
  [[ "$yq_version" == *mikefarah* ]] || fail 'mikefarah yq is required to validate the Grype config'
  yq -o=json -I0 '{"kind": kind, "keys": ((select(kind == "map") | keys) // []), "ignore": ((select(kind == "map") | .ignore | tag) // "")}' "$tool_tmp/grype.yaml" \
    | jq -s -e 'length == 1 and .[0].kind == "map" and ((.[0].keys - ["ignore"]) | length == 0) and (.[0].ignore | IN("!!seq", "!!null"))' >/dev/null \
    || fail 'Grype config must be one YAML document that contains only ignore rules'
  container+=(--volume "$tool_tmp/grype.yaml:/grype.yaml:ro")
  config_args=(--config /grype.yaml)
fi
blocked=false
printf '\n### Container scan\n\nCandidate: %s\n\n' "$SCAN_DIGEST" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
while IFS=$'\t' read -r platform digest; do
  key="${platform//\//-}"
  printf 'Scanning %s at %s\n' "$platform" "$digest"
  jq -e --arg digest "$digest" '.source.metadata.manifestDigest == $digest' \
    "$SCAN_REPORTS/$key.syft.json" >/dev/null || fail "Syft scanned the wrong manifest for $platform"
  [[ -s "$SCAN_REPORTS/$key.spdx.json" ]] || fail "Missing SPDX report for $platform"

  # Analyze the platform-specific SBOM; do not catalog a different image.
  rc=0
  "${container[@]}" --volume "$SCAN_REPORTS:/reports" --volume "$SCAN_CACHE:/cache" \
    --env GRYPE_DB_CACHE_DIR=/cache "$GRYPE_IMAGE" "sbom:/reports/$key.syft.json" "${config_args[@]}" --fail-on critical \
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
done <<< "$platforms"
[[ "$blocked" == false ]] || fail 'Critical vulnerability policy failed'
