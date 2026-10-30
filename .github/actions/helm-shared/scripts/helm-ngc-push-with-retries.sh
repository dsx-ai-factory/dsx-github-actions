#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

RED=${RED:-$'\033[91m'}
GREEN=${GREEN:-$'\033[92m'}
RESET=${RESET:-$'\033[0m'}

log_info() { printf '%s[INFO]%s %s\n' "$GREEN" "$RESET" "$1"; }
log_error() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1"; }

retries=${CC_HELM_NGC_RETRIES-0}
if [[ ! "$retries" =~ ^[0-9]+$ ]]; then
  log_error "retries must be a nonnegative integer." >&2
  exit 1
fi
[[ "${1:-}" != --validate-only ]] || exit 0

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
push_status=failed
attempt_env=""
# shellcheck disable=SC2317
finish() {
  local exit_status=$?
  echo "CC_HELM_NGC_PUSH_STATUS=$push_status" >> "$GITHUB_ENV" || exit_status=1
  [[ -z "$attempt_env" ]] || rm -f -- "$attempt_env"
  exit "$exit_status"
}
trap 'finish' EXIT
attempt_env=$(mktemp)
retry=0
retry_delay=5
log_info "Initial NGC publish."
# GITHUB_ENV is not reloaded within a step. Isolate each attempt's writes,
# discard intermediate failures, and report only the final outcome.
while true; do
  echo -n "" > "$attempt_env"
  # A fresh Bash process preserves errexit inside the publisher even though
  # this command's exit status is tested by an if statement.
  if CC_HELM_NGC_PUSH_STATUS="" GITHUB_ENV="$attempt_env" \
      "$script_dir/utils.sh" helm_ngc_push; then
    while IFS= read -r line; do
      case "$line" in
        CC_HELM_NGC_PUSH_STATUS=success) push_status=success ;;
        CC_HELM_NGC_PUSH_STATUS=skipped) push_status=skipped ;;
      esac
    done < "$attempt_env"
    [[ "$push_status" != failed ]]
    exit
  fi
  if [[ "$retries" =~ ^0*${retry}$ ]]; then
    log_error "NGC publish failed: reached the configured retry limit ($retries retries)." >&2
    exit 1
  fi
  retry=$((retry + 1))
  log_info "Retrying NGC publish in ${retry_delay}s (retry $retry of $retries)."
  sleep "$retry_delay"
  retry_delay=$((retry_delay + 5))
done
