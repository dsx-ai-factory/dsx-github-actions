#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set +x
set -euo pipefail

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

[[ "${CHART_NAME:-}" =~ ^[a-z0-9]+([._-][a-z0-9]+)*$ ]] || fail 'CHART_NAME must be a valid chart name.'
[[ "${RELEASE_VERSION:-}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*$ ]] ||
  fail 'RELEASE_VERSION must be a canonical X.Y.Z-rc.N version.'
[[ "${EXPECTED_REVISION:-}" =~ ^[0-9a-f]{40}$ ]] || fail 'EXPECTED_REVISION must be a full Git commit SHA.'
export CHART_NAME RELEASE_VERSION EXPECTED_REVISION

verify_dir="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/rc-chart.XXXXXX")"
trap 'rm -rf -- "$verify_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
chart_archive="$verify_dir/$CHART_NAME-$RELEASE_VERSION.tgz"
chart_metadata="$verify_dir/Chart.yaml"

# Reuse helm-repo-ngc and its credentials from helm-package-push. Tool errors can
# include authenticated URLs, so report only bounded, credential-free diagnostics.
for attempt in 1 2 3 4 5 6; do
  rm -f -- "$chart_archive"
  if helm repo update helm-repo-ngc --fail-on-repo-update-fail >/dev/null 2>&1 &&
    helm pull "helm-repo-ngc/$CHART_NAME" --version "$RELEASE_VERSION" \
      --destination "$verify_dir" >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 6 ]]; then
    fail 'NGC did not expose the chart after 6 attempts.'
  fi
  printf 'Chart unavailable (attempt %s/6); retrying in 10s.\n' "$attempt" >&2
  sleep 10
done

[[ -f "$chart_archive" && ! -L "$chart_archive" ]] || fail 'Helm did not produce the expected chart archive.'
helm show chart "$chart_archive" > "$chart_metadata" 2>/dev/null || fail 'Unable to read published chart metadata.'
yq eval-all --exit-status '
  [.] | ((length == 1) and
  (.[0].name == strenv(CHART_NAME)) and
  (.[0].version == strenv(RELEASE_VERSION)) and
  (.[0].appVersion == strenv(RELEASE_VERSION)) and
  (.[0].annotations."dsx.nvidia.com/source-revision" == strenv(EXPECTED_REVISION)))
' "$chart_metadata" >/dev/null 2>&1 || fail 'Published chart metadata does not match the expected release and source revision.'
printf 'Verified published chart %s %s.\n' "$CHART_NAME" "$RELEASE_VERSION"
