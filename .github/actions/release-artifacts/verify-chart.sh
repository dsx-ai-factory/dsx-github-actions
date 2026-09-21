#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set +x
set -euo pipefail

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

check_existing=false
if [[ "$#" == 1 && "$1" == --check-existing ]]; then
  check_existing=true
elif [[ "$#" != 0 ]]; then
  fail 'Usage: bash verify-chart.sh [--check-existing]'
fi

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
if [[ "$check_existing" == true ]]; then
  helm repo update helm-repo-ngc --fail-on-repo-update-fail >/dev/null 2> "$verify_dir/update.errors" ||
    fail 'Unable to refresh the NGC chart index; existence is unknown.'
  [[ ! -s "$verify_dir/update.errors" ]] || fail 'Chart index refresh reported errors; existence is unknown.'
  # --devel is essential: RC versions are excluded by the default search range.
  # Search is a substring query; the JSON parser below enforces exact identity.
  helm search repo "helm-repo-ngc/$CHART_NAME" --versions --devel --output json \
    > "$verify_dir/search.json" 2> "$verify_dir/search.errors" ||
    fail 'Unable to search the fresh NGC chart index; existence is unknown.'
  # Helm can warn about an unreadable cache and still return success with [].
  [[ ! -s "$verify_dir/search.errors" ]] || fail 'Chart index search reported errors; existence is unknown.'
  state="$(node - "$verify_dir/search.json" <<'NODE'
const fs = require('node:fs');
try {
  const rows = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  if (!Array.isArray(rows) || rows.some(row => !row || typeof row !== 'object' ||
      Array.isArray(row) || typeof row.name !== 'string' || !row.name ||
      typeof row.version !== 'string' || !row.version)) throw new Error();
  const matches = rows.filter(row => row.name === `helm-repo-ngc/${process.env.CHART_NAME}` &&
    row.version === process.env.RELEASE_VERSION);
  if (matches.length > 1) throw new Error();
  process.stdout.write(matches.length === 1 ? 'present' : 'absent');
} catch {
  process.exitCode = 1;
}
NODE
  )" || fail 'Invalid chart index search results; existence is unknown.'
  if [[ "$state" == absent ]]; then
    printf 'Chart version is absent from the fresh NGC index.\n'
    exit 3
  fi
  [[ "$state" == present ]] || fail 'Unable to determine chart existence.'
  helm pull "helm-repo-ngc/$CHART_NAME" --version "$RELEASE_VERSION" \
    --destination "$verify_dir" >/dev/null 2>&1 ||
    fail 'Indexed chart could not be pulled; refusing to classify it as absent.'
else
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
fi

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
