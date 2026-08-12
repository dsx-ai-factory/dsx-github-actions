#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

RED=${RED:-$'\033[91m'}
GREEN=${GREEN:-$'\033[92m'}
YELLOW=${YELLOW:-$'\033[93m'}
RESET=${RESET:-$'\033[0m'}

errexit() {
  printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1" >&2
  exit 1
}

info() {
  printf '%s[INFO]%s %s\n' "$GREEN" "$RESET" "$1"
}

require_non_empty() {
  local value=$1
  local field=$2
  if [[ -z "$value" ]]; then
    errexit "$field must be provided."
  fi
}

# GitHub environment files use a delimiter for multiline values. A unique
# delimiter keeps embedded newlines from being interpreted as new variables.
write_env() {
  local name=$1
  local value=$2
  local delimiter="ghadelimiter_${RANDOM}_${RANDOM}_$$"

  while grep -Fqx -- "$delimiter" <<< "$value"; do
    delimiter="ghadelimiter_${RANDOM}_${RANDOM}_$$"
  done

  {
    printf '%s<<%s\n' "$name" "$delimiter"
    printf '%s\n' "$value"
    printf '%s\n' "$delimiter"
  } >> "$GITHUB_ENV"
}

CC_RESOURCE_NAME=${INPUT_NAME:-}
if [[ -n ${RESOURCE_NAME:-} ]]; then
  info "Reading name from RESOURCE_NAME"
  CC_RESOURCE_NAME=$RESOURCE_NAME
fi
require_non_empty "$CC_RESOURCE_NAME" "Resource name"

CC_RESOURCE_DISPLAY_NAME=${INPUT_DISPLAY_NAME:-}
if [[ -z "$CC_RESOURCE_DISPLAY_NAME" ]]; then
  CC_RESOURCE_DISPLAY_NAME=$CC_RESOURCE_NAME
fi

CC_RESOURCE_DESCRIPTION=${INPUT_DESCRIPTION:-}
if [[ -n ${RESOURCE_DESCRIPTION:-} ]]; then
  info "Reading description from RESOURCE_DESCRIPTION"
  CC_RESOURCE_DESCRIPTION=$RESOURCE_DESCRIPTION
fi
if [[ -z "$CC_RESOURCE_DESCRIPTION" ]]; then
  CC_RESOURCE_DESCRIPTION=$CC_RESOURCE_NAME
fi

CC_RESOURCE_PATH=${INPUT_PATH:-}
if [[ -n ${RESOURCE_PATH:-} ]]; then
  info "Reading path from RESOURCE_PATH"
  CC_RESOURCE_PATH=$RESOURCE_PATH
fi
require_non_empty "$CC_RESOURCE_PATH" "Resource path"
if [[ ! -e "$CC_RESOURCE_PATH" ]]; then
  errexit "Resource path '$CC_RESOURCE_PATH' does not exist."
fi
if [[ -d "$CC_RESOURCE_PATH" ]]; then
  info "Resource path resolved to directory $CC_RESOURCE_PATH"
fi

if command -v realpath >/dev/null 2>&1; then
  CC_RESOURCE_PATH=$(realpath "$CC_RESOURCE_PATH")
else
  CC_RESOURCE_PATH=$(python3 - <<'PY'
import os, sys
print(os.path.abspath(sys.argv[1]))
PY
"$CC_RESOURCE_PATH")
fi

CC_RESOURCE_VERSION=${INPUT_VERSION:-}
if [[ -n ${RESOURCE_VERSION:-} ]]; then
  info "Reading version from RESOURCE_VERSION"
  CC_RESOURCE_VERSION=$RESOURCE_VERSION
fi
require_non_empty "$CC_RESOURCE_VERSION" "Resource version"

CC_RESOURCE_FORMAT=${INPUT_FORMAT:-generic}
if [[ -n ${RESOURCE_FORMAT:-} ]]; then
  info "Reading format from RESOURCE_FORMAT"
  CC_RESOURCE_FORMAT=$RESOURCE_FORMAT
fi
require_non_empty "$CC_RESOURCE_FORMAT" "Resource format"

CC_RESOURCE_APPLICATION=${INPUT_APPLICATION:-OTHER}
if [[ -n ${RESOURCE_APPLICATION:-} ]]; then
  info "Reading application from RESOURCE_APPLICATION"
  CC_RESOURCE_APPLICATION=$RESOURCE_APPLICATION
fi
require_non_empty "$CC_RESOURCE_APPLICATION" "Resource application"

CC_RESOURCE_FRAMEWORK=${INPUT_FRAMEWORK:-Other}
if [[ -n ${RESOURCE_FRAMEWORK:-} ]]; then
  info "Reading framework from RESOURCE_FRAMEWORK"
  CC_RESOURCE_FRAMEWORK=$RESOURCE_FRAMEWORK
fi
require_non_empty "$CC_RESOURCE_FRAMEWORK" "Resource framework"

CC_RESOURCE_PRECISION=${INPUT_PRECISION:-OTHER}
if [[ -n ${RESOURCE_PRECISION:-} ]]; then
  info "Reading precision from RESOURCE_PRECISION"
  CC_RESOURCE_PRECISION=$RESOURCE_PRECISION
fi
require_non_empty "$CC_RESOURCE_PRECISION" "Resource precision"

CC_RESOURCE_NGC_FORCE=${INPUT_NGC_FORCE:-skip}
if [[ -n ${RESOURCE_NGC_FORCE:-} ]]; then
  info "Reading ngc-force from RESOURCE_NGC_FORCE"
  CC_RESOURCE_NGC_FORCE=$RESOURCE_NGC_FORCE
fi
case "$CC_RESOURCE_NGC_FORCE" in
  overwrite|skip|fail) ;;
  *)
    errexit "ngc-force must be one of overwrite|skip|fail"
    ;;
esac

CC_RESOURCE_NGC_PATH=${INPUT_NGC_PATH:-}
require_non_empty "$CC_RESOURCE_NGC_PATH" "ngc-path"
CC_RESOURCE_NGC_KEY=${INPUT_NGC_KEY:-}
require_non_empty "$CC_RESOURCE_NGC_KEY" "ngc-key"

cat <<SUMMARY
Resource Name: $CC_RESOURCE_NAME
Resource Display Name: $CC_RESOURCE_DISPLAY_NAME
Resource Description: $CC_RESOURCE_DESCRIPTION
Resource Path: $CC_RESOURCE_PATH
Resource Version: $CC_RESOURCE_VERSION
Resource Format: $CC_RESOURCE_FORMAT
Resource Application: $CC_RESOURCE_APPLICATION
Resource Framework: $CC_RESOURCE_FRAMEWORK
Resource Precision: $CC_RESOURCE_PRECISION
Resource NGC Force: $CC_RESOURCE_NGC_FORCE
Resource NGC Path: $CC_RESOURCE_NGC_PATH
SUMMARY

write_env CC_RESOURCE_NAME "$CC_RESOURCE_NAME"
write_env CC_RESOURCE_DISPLAY_NAME "$CC_RESOURCE_DISPLAY_NAME"
write_env CC_RESOURCE_DESCRIPTION "$CC_RESOURCE_DESCRIPTION"
write_env CC_RESOURCE_PATH "$CC_RESOURCE_PATH"
write_env CC_RESOURCE_VERSION "$CC_RESOURCE_VERSION"
write_env CC_RESOURCE_FORMAT "$CC_RESOURCE_FORMAT"
write_env CC_RESOURCE_APPLICATION "$CC_RESOURCE_APPLICATION"
write_env CC_RESOURCE_FRAMEWORK "$CC_RESOURCE_FRAMEWORK"
write_env CC_RESOURCE_PRECISION "$CC_RESOURCE_PRECISION"
write_env CC_RESOURCE_NGC_FORCE "$CC_RESOURCE_NGC_FORCE"
write_env CC_RESOURCE_NGC_PATH "$CC_RESOURCE_NGC_PATH"
write_env CC_RESOURCE_NGC_KEY "$CC_RESOURCE_NGC_KEY"
