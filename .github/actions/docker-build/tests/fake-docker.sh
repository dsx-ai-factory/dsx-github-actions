#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Fake only external tool execution; the test uses real OCI metadata and hashes.
set -euo pipefail
while [[ "$1" != syft && "$1" != grype && "$1" != skopeo ]]; do shift; done
tool="$1"
shift
printf '%s\n' "$tool" >> "$SCAN_REPORTS/calls"
case "$tool" in
  syft)
    [[ "$FAKE_MODE" != syft-error ]] || exit 1
    [[ "$1" == scan && "$2" == oci-dir:/candidate && "$3" == --platform ]]
    platform="$4"
    key="${platform//\//-}"
    digest="$(awk -v platform="$platform" '$1 == platform {print $2}' "$SCAN_REPORTS/platforms.tsv")"
    if [[ "$FAKE_MODE" == syft-wrong-manifest ]]; then digest=wrong; fi
    jq -n --arg digest "$digest" '{source:{metadata:{manifestDigest:$digest}}}' > "$SCAN_REPORTS/$key.syft.json"
    printf '{}\n' > "$SCAN_REPORTS/$key.spdx.json"
    ;;
  grype)
    [[ "$FAKE_MODE" != grype-error ]] || exit 1
    key="${1#sbom:/reports/}"
    key="${key%.syft.json}"
    [[ "$2" == --fail-on && "$3" == critical ]]
    digest="$(jq -r '.source.metadata.manifestDigest' "$SCAN_REPORTS/$key.syft.json")"
    if [[ "$FAKE_MODE" == grype-wrong-manifest ]]; then digest=wrong; fi
    matches='[]'
    rc=0
    if [[ "$FAKE_MODE" == arm-critical && "$key" == linux-arm64 ]]; then
      matches='[{"vulnerability":{"severity":"Critical"}}]'
      rc=2
    fi
    jq -n --arg digest "$digest" --argjson matches "$matches" \
      '{source:{target:{manifestDigest:$digest}},matches:$matches}' > "$SCAN_REPORTS/$key.grype.json"
    if [[ "$FAKE_MODE" != missing-report ]]; then printf '{}\n' > "$SCAN_REPORTS/$key.sarif"; fi
    printf 'Grype report\n' > "$SCAN_REPORTS/$key.txt"
    exit "$rc"
    ;;
  skopeo)
    [[ "$1" == copy && "$2" == --all && "$3" == --preserve-digests ]]
    [[ "$FAKE_MODE" != publish-error ]] || exit 1
    # Every platform must have been scanned before the first publication.
    [[ "$(awk '$1 == "grype" {n++} END {print n+0}' "$SCAN_REPORTS/calls")" == "$(awk 'END {print NR}' "$SCAN_REPORTS/platforms.tsv")" ]]
    digest="$SCAN_DIGEST"
    if [[ "$FAKE_MODE" == publish-wrong-digest ]]; then digest=wrong; fi
    printf '%s\n' "$digest" > "$SCAN_REPORTS/published.digest"
    ;;
esac
