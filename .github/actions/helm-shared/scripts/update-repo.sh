#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set +x
set -euo pipefail

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

[[ "$#" == 1 && "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] ||
  fail 'Expected one configured Helm repository name.'
version=$(helm version --template '{{.Version}}' 2>/dev/null) ||
  fail 'Unable to determine the Helm version.'
[[ "$version" =~ ^v(3|4)\.[0-9]+\.[0-9]+([+-][a-zA-Z0-9.+-]+)?$ ]] ||
  fail 'Chart index refresh requires Helm 3 or 4.'
update=(helm repo update "$1")
# Helm 4 always fails on refresh errors and removed the Helm 3 opt-in flag.
if [[ "$version" == v3.* ]]; then
  update+=(--fail-on-repo-update-fail)
fi
printf 'Refreshing chart index with Helm %s.\n' "$version"
errors=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/helm-update.XXXXXX")
trap 'rm -f -- "$errors"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Diagnostics may contain authenticated URLs. Never print raw tool output.
"${update[@]}" >/dev/null 2> "$errors" ||
  fail "Helm $version could not refresh the chart index; existence is unknown."
[[ ! -s "$errors" ]] || fail 'Chart index refresh reported errors; existence is unknown.'
