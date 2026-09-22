#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Bind downloaded build evidence to the exact registry image before any signing.
set -euo pipefail
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }
: "${IMAGE:?}" "${EXPECTED_DIGEST:?}" "${PLATFORMS:?}" "${REPORTS:?}" "${GITHUB_OUTPUT:?}"
[[ "$IMAGE" =~ ^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+$ ]] || fail 'Use a fully qualified, tag-free image repository'
[[ "$EXPECTED_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'Invalid image digest'

requested="$(jq -nc --arg platforms "$PLATFORMS" '
  $platforms | gsub(","; "\n") | split("\n") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))
  | map(sub("^linux/arm64/v8$"; "linux/arm64")) | sort
  | if length > 0 and all(.[]; test("^linux/[a-z0-9_]+(/[a-z0-9_.-]+)?$")) and length == (unique | length)
    then . else error("Use explicit, distinct Linux platforms") end')"
jq -e --arg digest "$EXPECTED_DIGEST" --argjson requested "$requested" '
  .digest == $digest and
  (.platforms | type == "array" and length > 0) and
  ([.platforms[].platform] | sort) == $requested and
  all(.platforms[]; (.digest | test("^sha256:[a-f0-9]{64}$")))
' "$REPORTS/image.json" >/dev/null || fail 'Build evidence has the wrong digest or platform set'

# The registry tree, not the mutable candidate tag, is authoritative.
manifest="$(docker buildx imagetools inspect "$IMAGE@$EXPECTED_DIGEST" --raw)"
recorded="$(jq -cS '.platforms | sort_by(.platform)' "$REPORTS/image.json")"
if jq -e 'has("manifests")' <<< "$manifest" >/dev/null; then
  actual="$(jq -cS '[.manifests[] |
    select(.annotations["vnd.docker.reference.type"] != "attestation-manifest") |
    {platform: ([.platform.os, .platform.architecture, (.platform.variant // "")] |
      map(select(. != "")) | join("/") | sub("^linux/arm64/v8$"; "linux/arm64")), digest}]
    | sort_by(.platform)' <<< "$manifest")"
  [[ "$recorded" == "$actual" ]] || fail 'Registry platform digests differ from the build evidence'
else
  jq -e 'has("config") and has("layers")' <<< "$manifest" >/dev/null || fail 'Not an image manifest'
  jq -e --arg digest "$EXPECTED_DIGEST" \
    '.platforms | length == 1 and .[0].digest == $digest' "$REPORTS/image.json" >/dev/null \
    || fail 'A single manifest must have exactly one matching platform'
fi

while IFS=$'\t' read -r platform digest; do
  key="${platform//\//-}"
  jq -e --arg digest "$digest" '.source.metadata.manifestDigest == $digest' \
    "$REPORTS/$key.syft.json" >/dev/null || fail "SBOM subject mismatch for $platform"
  jq -e '.spdxVersion and .SPDXID == "SPDXRef-DOCUMENT"' \
    "$REPORTS/$key.spdx.json" >/dev/null || fail "Invalid SBOM for $platform"
done < <(jq -r '.platforms[] | [.platform, .digest] | @tsv' "$REPORTS/image.json")

# A plain single-platform manifest is already the root: attest it only once.
# A single-platform BuildKit index still gets separate root provenance.
matrix="$(jq -c '
  . as $image | {include: (
    [.platforms[] | {platform, digest, sbom: ((.platform | gsub("/"; "-")) + ".spdx.json")}] +
    (if any(.platforms[]; .digest == $image.digest) then []
     else [{platform: "index", digest: .digest, sbom: ""}] end)
  )} | if (.include | length) <= 256 then . else error("Too many attestation subjects") end
' "$REPORTS/image.json")"
printf 'matrix=%s\n' "$matrix" >> "$GITHUB_OUTPUT"
