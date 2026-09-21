#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 IMAGE_REF EXPECTED_REVISION [EXPECTED_PLATFORMS]" >&2
  exit 64
fi

image_ref="$1"
expected_revision="$2"
expected_platforms="${3-linux/amd64,linux/arm64}"
platform_pattern='[a-z0-9]+/[a-z0-9_]+(/[a-z0-9._-]+)?'
if [[ -z "$image_ref" || "$image_ref" == -* || -z "$expected_revision" ||
      ! "$expected_platforms" =~ ^${platform_pattern}(,${platform_pattern})*$ ]]; then
  echo "Image, revision, and comma-separated os/architecture[/variant] platforms are required." >&2
  exit 64
fi

temp_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
temp_dir="$(mktemp -d "$temp_root/dsx-rc-image.XXXXXX")" || exit 1
trap 'rm -rf "$temp_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
manifest_file="$temp_dir/manifest.json"
image_file="$temp_dir/image.json"
error_file="$temp_dir/error"
targets_file="$temp_dir/targets"

# --raw resolves only the root manifest. Formatted inspection also loads child
# manifests/configs, whose absence must not authorize publishing over this tag.
if ! docker buildx imagetools inspect --raw "$image_ref" >"$manifest_file" 2>"$error_file"; then
  message="$(<"$error_file")"
  message="${message#ERROR: }"
  cat "$error_file" >&2
  # Deliberately narrow: generic HTTP 404s and "not found" can be auth/proxy errors.
  case "$message" in
    "$image_ref: not found" | "$image_ref: manifest unknown" | \
    "$image_ref: manifest unknown: manifest unknown" | \
    'manifest unknown' | 'manifest unknown: manifest unknown' | \
    'MANIFEST_UNKNOWN: manifest unknown')
      exit 3
      ;;
  esac
  exit 1
fi

# Slurp and require one document, so empty output or trailing JSON cannot pass.
if ! jq -se '
  def descriptor:
    type == "object"
    and (.mediaType | type == "string")
    and (.digest | type == "string" and test("^[a-z0-9]+:[a-f0-9]{32,}$"))
    and (.size | type == "number" and . >= 0 and floor == .);
  length == 1 and (.[0] |
    type == "object" and .schemaVersion == 2
    and if .mediaType == "application/vnd.oci.image.index.v1+json"
        or .mediaType == "application/vnd.docker.distribution.manifest.list.v2+json" then
      (.manifests | type == "array" and length > 0 and all(.[]; descriptor))
    elif .mediaType == "application/vnd.oci.image.manifest.v1+json"
        or .mediaType == "application/vnd.docker.distribution.manifest.v2+json" then
      (.config | descriptor) and (.layers | type == "array" and all(.[]; descriptor))
    else false end)
' "$manifest_file" >/dev/null; then
  echo "$image_ref returned an invalid or unsupported image manifest." >&2
  exit 1
fi

if ! jq -r --arg platforms "$expected_platforms" '
  . as $manifest | $platforms | split(",") | unique[] as $requested
  | ($requested | split("/")) as $parts
  | if $manifest.manifests then
      [$manifest.manifests[]
        | select(.mediaType == "application/vnd.oci.image.manifest.v1+json"
            or .mediaType == "application/vnd.docker.distribution.manifest.v2+json")
        | select(.annotations["vnd.docker.reference.type"] != "attestation-manifest")
        | select(.platform.os == $parts[0] and .platform.architecture == $parts[1])
        | select(($parts | length) == 2 or
            (.platform.variant // (if $parts[1] == "arm64" then "v8" else "" end)) == $parts[2])]
      | if length == 0 then error("missing requested platform " + $requested)
        else .[] | [$requested, .digest] | @tsv end
    else
      [$requested, ""] | @tsv
    end
' "$manifest_file" >"$targets_file"; then
  echo "$image_ref does not contain every requested platform: $expected_platforms." >&2
  exit 1
fi

# Inspect every selected child by digest, not the platform-keyed .Image map of
# the index: duplicate platform descriptors can otherwise hide a bad revision.
# For a single manifest, Buildx also returns one OCI image config, not a map.
inspected_single=false
while IFS=$'\t' read -r platform digest; do
  target_ref="$image_ref"
  if [[ -n "$digest" ]]; then
    target_ref="${image_ref%%@*}@$digest"
  fi
  if [[ -n "$digest" || "$inspected_single" == false ]]; then
    if ! docker buildx imagetools inspect --format '{{json .Image}}' \
      "$target_ref" >"$image_file" 2>"$error_file"; then
      cat "$error_file" >&2
      echo "Unable to inspect $image_ref platform $platform." >&2
      exit 1
    fi
    # Reuse a single-manifest snapshot even when several platforms are requested.
    inspected_single=true
  fi

  if ! jq -se --arg expected "$expected_revision" --arg platform "$platform" '
    ($platform | split("/")) as $parts
    | length == 1 and (.[0] |
        type == "object"
        and .os == $parts[0] and .architecture == $parts[1]
        and (($parts | length) == 2 or
          (.variant // (if .architecture == "arm64" then "v8" else "" end)) == $parts[2])
        and (.config | type == "object")
        and (.config.Labels | type == "object")
        and .config.Labels["org.opencontainers.image.revision"] == $expected)
  ' "$image_file" >/dev/null; then
    echo "$image_ref does not match platform $platform and source revision $expected_revision." >&2
    exit 1
  fi
done <"$targets_file"

echo "Verified $image_ref at source revision $expected_revision for $expected_platforms."
