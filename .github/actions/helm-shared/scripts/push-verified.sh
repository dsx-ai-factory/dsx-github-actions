#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
chart_ref="helm-repo-ngc/${CC_HELM_CHART_NAME}"
ngc_chart="${CC_HELM_NGC_PATH}/${CC_HELM_CHART_NAME}"
ngc_version="${ngc_chart}:${CC_HELM_CHART_VERSION}"
push_status=failed
verify_dir=$(mktemp -d)
# Invoked by the EXIT trap, including after failed commands.
# shellcheck disable=SC2317
finish() {
  local exit_status=$?
  echo "CC_HELM_NGC_PUSH_STATUS=$push_status" >> "$GITHUB_ENV" || exit_status=1
  rm -rf -- "$verify_dir"
  exit "$exit_status"
}
trap 'finish' EXIT

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

command -v python3 >/dev/null || fail 'ngc-duplicate=verify requires python3.'
command -v yq >/dev/null || fail 'ngc-duplicate=verify requires yq.'
command -v jq >/dev/null || fail 'ngc-duplicate=verify requires jq.'

# These built-in Helm commands need only the configured NGC repository. Keep
# unrelated caches, plugins, and Kubernetes configuration out of the lookup.
export KUBECONFIG="$verify_dir/kubeconfig"
export HELM_PLUGINS="$verify_dir/plugins"
repository_config=$(helm env HELM_REPOSITORY_CONFIG 2>/dev/null) ||
  fail 'Unable to locate the Helm repository configuration.'
yq eval -e '.repositories |= map(select(.name == "helm-repo-ngc")) |
  select(.repositories | length == 1)' "$repository_config" \
  > "$verify_dir/repositories.yaml" 2>/dev/null ||
  fail 'Unable to isolate the configured NGC repository.'
export HELM_REPOSITORY_CONFIG="$verify_dir/repositories.yaml"
export HELM_REPOSITORY_CACHE="$verify_dir/repository"

# Helm can return success with an empty result after a cache error. Neither a
# failed refresh nor a warning from search establishes that a version is absent.
# Raw diagnostics can contain credentialed URLs; report the failed operation.
lookup_version() {
  if ! helm repo update helm-repo-ngc --fail-on-repo-update-fail > /dev/null 2> "$verify_dir/update.errors"; then
    printf 'Unable to refresh the NGC chart index; existence is unknown.\n' >&2
    return 1
  fi
  if [[ -s "$verify_dir/update.errors" ]]; then
    printf 'NGC chart index refresh reported errors; existence is unknown.\n' >&2
    return 1
  fi
  if ! helm search repo "$chart_ref" --versions --version '>=0.0.0-0' --output json \
      > "$verify_dir/search.json" 2> "$verify_dir/search.errors"; then
    printf 'Unable to search the NGC chart index; existence is unknown.\n' >&2
    return 1
  fi
  if [[ -s "$verify_dir/search.errors" ]]; then
    printf 'NGC chart index search reported errors; existence is unknown.\n' >&2
    return 1
  fi
  # Search is a substring query. The inclusive constraint also covers the
  # minimum prerelease, 0.0.0-0, which Helm's --devel constraint excludes.
  jq -ers --arg name "$chart_ref" --arg version "$CC_HELM_CHART_VERSION" '
    (if length == 1 then .[0] else error("Expected one chart index result") end) |
    if type != "array" or any(.[];
      type != "object" or (.name | type) != "string" or .name == "" or
      (.version | type) != "string" or .version == "") then
      error("Invalid NGC chart index search results")
    else
      [.[] | select(.name == $name and .version == $version)] | length |
      if . == 0 then "absent" elif . == 1 then "present"
      else error("Ambiguous NGC chart index search results") end
    end
  ' "$verify_dir/search.json" 2>/dev/null || {
    printf 'Invalid NGC chart index search results; existence is unknown.\n' >&2
    return 1
  }
}

# Failed Helm downloads can retry after an upload failure. Successfully
# downloaded contents that differ or cannot be verified are terminal (status 2).
compare_published_chart() {
  rm -f -- "$verify_dir/${CC_HELM_CHART_NAME}-${CC_HELM_CHART_VERSION}.tgz"
  if ! helm pull "$chart_ref" --version "$CC_HELM_CHART_VERSION" \
      --destination "$verify_dir" > /dev/null 2>&1; then
    printf 'Unable to download the indexed NGC chart version.\n' >&2
    return 1
  fi
  python3 "$script_dir/compare-charts.py" "$CC_HELM_CHART_TGZ" \
    "$verify_dir/${CC_HELM_CHART_NAME}-${CC_HELM_CHART_VERSION}.tgz" || return 2
}

reuse_published_chart() {
  push_status=skipped
  printf 'Verified existing NGC chart %s; reusing its contents.\n' "$ngc_version"
}

state=$(lookup_version) || fail 'Refusing to publish without a fresh NGC version lookup.'
if [[ "$state" == present ]]; then
  compare_published_chart || fail 'Existing NGC chart could not be verified; refusing to reuse or overwrite it.'
  reuse_published_chart
  exit 0
fi

# NGC info uses the same failure status for absence and other errors. Ensure the
# record exists through a successful read or create instead of inferring absence.
if ! ngc registry chart info "$ngc_chart" > /dev/null 2>&1; then
  printf 'NGC chart metadata lookup failed; attempting to create the chart record.\n' >&2
  if ! ngc registry chart create "$ngc_chart" --short-desc "${CC_HELM_CHART_DESCRIPTION:-}" > /dev/null 2>&1; then
    printf 'NGC chart creation failed; checking for concurrent creation.\n' >&2
    ngc registry chart info "$ngc_chart" > /dev/null 2>&1 ||
      fail 'Unable to confirm the NGC chart record after lookup and creation failures.'
  fi
fi

if ngc registry chart push --source "$(dirname -- "$CC_HELM_CHART_TGZ")" "$ngc_version"; then
  push_status=success
  exit 0
fi

# The CLI describes every HTTP 409 as "already exists". Only a successful
# content comparison can establish that a failed upload is safe to accept.
printf 'NGC upload failed; checking for an identical published chart.\n' >&2
for attempt in 1 2 3 4 5 6; do
  if state=$(lookup_version) && [[ "$state" == present ]]; then
    comparison_status=0
    compare_published_chart || comparison_status=$?
    if [[ "$comparison_status" == 0 ]]; then
      reuse_published_chart
      exit 0
    fi
    [[ "$comparison_status" != 2 ]] || fail 'Published NGC chart differs or is invalid; refusing to reuse it.'
  fi
  if [[ "$attempt" != 6 ]]; then
    printf 'NGC chart not yet verified (attempt %s/6); retrying in 10s.\n' "$attempt" >&2
    sleep 10
  fi
done
fail 'NGC upload failed and no identical chart could be verified after 6 attempts.'
