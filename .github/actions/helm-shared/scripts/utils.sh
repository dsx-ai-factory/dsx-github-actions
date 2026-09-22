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
#
# shellcheck shell=bash
set -euo pipefail

RED=${RED:-$'\033[91m'}
GREEN=${GREEN:-$'\033[92m'}
YELLOW=${YELLOW:-$'\033[93m'}
RESET=${RESET:-$'\033[0m'}

log_info() { printf '%s[INFO]%s %s\n' "$GREEN" "$RESET" "$1"; }
log_warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$1"; }
log_error() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1"; }

# Helper to expand variables if they contain env var references (basic envsubst)
expand_vars() {
  local val="$1"
  # If envsubst is available, use it. Otherwise, basic echo.
  if command -v envsubst >/dev/null 2>&1; then
    echo "$val" | envsubst
  else
    echo "$val"
  fi
}

helm_init_fetch() {
  # CC_HELM_CHART_NAME, CC_HELM_CHART_VERSION, CC_HELM_CHART_VERSION_SUFFIX from env

  [[ -n "${HELM_CHART_NAME:-}" ]] && CC_HELM_CHART_NAME="$HELM_CHART_NAME"
  [[ -z "${CC_HELM_CHART_NAME:-}" ]] && log_error "Chart name not specified." && exit 1

  [[ -n "${HELM_CHART_VERSION:-}" ]] && CC_HELM_CHART_VERSION="$HELM_CHART_VERSION"
  [[ -z "${CC_HELM_CHART_VERSION:-}" ]] && log_error "Chart version not specified." && exit 1

  [[ -n "${HELM_CHART_VERSION_SUFFIX:-}" ]] && CC_HELM_CHART_VERSION_SUFFIX="$HELM_CHART_VERSION_SUFFIX"
  if [[ -n "${CC_HELM_CHART_VERSION_SUFFIX:-}" ]] && [[ "$CC_HELM_CHART_VERSION" != *"$CC_HELM_CHART_VERSION_SUFFIX" ]]; then
    CC_HELM_CHART_VERSION="${CC_HELM_CHART_VERSION}${CC_HELM_CHART_VERSION_SUFFIX}"
  fi

  log_info "Chart Name: ${CC_HELM_CHART_NAME}"
  log_info "Chart Version: ${CC_HELM_CHART_VERSION}"

  # Export back to env for subsequent steps
  {
    echo "CC_HELM_CHART_NAME=$CC_HELM_CHART_NAME"
    echo "CC_HELM_CHART_VERSION=$CC_HELM_CHART_VERSION"
  } >> "$GITHUB_ENV"
}

helm_init_package() {
  [[ -n "${HELM_CHART_VERSION:-}" ]] && CC_HELM_CHART_VERSION="$HELM_CHART_VERSION"
  if [[ -z "${CC_HELM_CHART_VERSION:-}" ]]; then
    if [[ -f "${CC_HELM_CHART_PATH:-.}/Chart.yaml" ]]; then
      CC_HELM_CHART_VERSION=$(yq -r '.version' "${CC_HELM_CHART_PATH:-.}/Chart.yaml")
    fi
  fi
  [[ -z "${CC_HELM_CHART_VERSION:-}" ]] && log_error "Chart version not specified." && exit 1

  [[ -n "${HELM_CHART_VERSION_SUFFIX:-}" ]] && CC_HELM_CHART_VERSION_SUFFIX="$HELM_CHART_VERSION_SUFFIX"
  if [[ -n "${CC_HELM_CHART_VERSION_SUFFIX:-}" ]] && [[ "$CC_HELM_CHART_VERSION" != *"$CC_HELM_CHART_VERSION_SUFFIX" ]]; then
    CC_HELM_CHART_VERSION="${CC_HELM_CHART_VERSION}${CC_HELM_CHART_VERSION_SUFFIX}"
  fi

  log_info "Chart Version: ${CC_HELM_CHART_VERSION}"
  echo "CC_HELM_CHART_VERSION=$CC_HELM_CHART_VERSION" >> "$GITHUB_ENV"

  [[ -n "${HELM_CHART_APP_VERSION:-}" ]] && CC_HELM_CHART_APP_VERSION="$HELM_CHART_APP_VERSION"
  if [[ -z "${CC_HELM_CHART_APP_VERSION:-}" ]]; then
    if [[ -f "${CC_HELM_CHART_PATH:-.}/Chart.yaml" ]]; then
      CC_HELM_CHART_APP_VERSION=$(yq -r '.appVersion // ""' "${CC_HELM_CHART_PATH:-.}/Chart.yaml")
    fi
  fi

  if [[ -n "${CC_HELM_CHART_APP_VERSION:-}" ]]; then
    log_info "App Version: ${CC_HELM_CHART_APP_VERSION}"
    echo "CC_HELM_CHART_APP_VERSION=$CC_HELM_CHART_APP_VERSION" >> "$GITHUB_ENV"
  fi
}

helm_init_push() {
  if [[ "${CC_HELM_NGC_PUSH:-false}" != "false" ]]; then
    if [[ -z "${CC_HELM_NGC_PATH:-}" || -z "${CC_HELM_NGC_KEY:-}" ]]; then
      log_error "ngc-path and ngc-key are required."
      exit 1
    fi
  fi

  [[ -n "${HELM_NGC_DUPLICATE:-}" ]] && CC_HELM_NGC_DUPLICATE="$HELM_NGC_DUPLICATE"

  # Check package dir
  local pkg_dir="${CC_HELM_PACKAGE_DIR:-package}"
  local files=("$pkg_dir"/*.tgz)

  if [[ ! -e "${files[0]}" ]]; then
    log_error "No .tgz file found on: $pkg_dir"
    exit 1
  fi
  if [[ "${#files[@]}" -gt 1 ]]; then
    log_error "Only one .tgz file is supported, found ${#files[@]}."
    exit 1
  fi

  CC_HELM_CHART_TGZ="${files[0]}"
  log_info "Chart package: $CC_HELM_CHART_TGZ"

  CC_HELM_CHART_NAME=$(helm show chart "$CC_HELM_CHART_TGZ" | yq -r '.name')
  CC_HELM_CHART_DESCRIPTION=$(helm show chart "$CC_HELM_CHART_TGZ" | yq -r '.description')
  CC_HELM_CHART_VERSION=$(helm show chart "$CC_HELM_CHART_TGZ" | yq -r '.version')

  log_info "Chart Name: $CC_HELM_CHART_NAME"
  log_info "Chart Description: $CC_HELM_CHART_DESCRIPTION"
  log_info "Chart Version: $CC_HELM_CHART_VERSION"

  {
    echo "CC_HELM_CHART_TGZ=$CC_HELM_CHART_TGZ"
    echo "CC_HELM_CHART_NAME=$CC_HELM_CHART_NAME"
    echo "CC_HELM_CHART_DESCRIPTION=$CC_HELM_CHART_DESCRIPTION"
    echo "CC_HELM_CHART_VERSION=$CC_HELM_CHART_VERSION"
    echo "CC_HELM_NGC_DUPLICATE=$CC_HELM_NGC_DUPLICATE"
  } >> "$GITHUB_ENV"
}

helm_extra_repos_var() {
  # Convert Ruby hash style to JSON if needed
  local extra_repos="${CC_HELM_EXTRA_REPOS:-}"
  log_info "Processing extra repos..."

  # Simple heuristic: if it contains "=>", try sed conversion
  if [[ "$extra_repos" == *"=>"* ]]; then
    log_info "Converting extra repos array from Ruby to Json..."
    extra_repos=$(echo "$extra_repos" | sed -r -e 's/\s*:\"?([^" =]*)\"?\s*=>\s*\"?([^" ,]*)\"?\s*/"\1":"\2"/g')
  fi

  # Validate JSON
  if ! echo "$extra_repos" | jq -ecr '[ .[] | (type == "object" and length == 1) or (keys - ["name", "url", "username", "password"] == []) ] | all' >/dev/null 2>&1; then
    log_error "Invalid extra repositories format."
    exit 1
  fi

  CC_HELM_EXTRA_REPOS="$extra_repos"
  echo "CC_HELM_EXTRA_REPOS=$CC_HELM_EXTRA_REPOS" >> "$GITHUB_ENV"
}

helm_extra_repos_add() {
  if echo "${CC_HELM_EXTRA_REPOS:-}" | jq -e 'length > 0' >/dev/null 2>&1; then
    log_info "Adding extra repositories..."
    echo "$CC_HELM_EXTRA_REPOS" \
    | jq -cr '.[] | if (type=="object" and length==1) then (to_entries as [{key: $name, value: $url}] | {$name, $url} ) else . end | "\(.name) \(.url) \(.username) \(.password)"' \
    | while read -r name url username password; do
      log_info "Attempting to add: ${name} ${url}"
      if [[ "$username" == "null" && "$password" == "null" ]]; then
         helm repo add "$name" "$url"
      else
         echo "$password" | helm repo add "$name" "$url" --username="$username" --password-stdin
      fi
    done
    helm repo list
    helm repo update
  fi
}

helm_value_overrides_var() {
  local overrides="${CC_HELM_VALUE_OVERRIDES:-}"
  log_info "Processing value overrides..."

  if [[ "$overrides" == *"=>"* ]]; then
     log_info "Converting value overrides array from Ruby to Json..."
     overrides=$(echo "$overrides" | sed -r -e 's/\s*:\"?([^" =]*)\"?\s*=>\s*\"?([^" ,]*)\"?\s*/"\1":"\2"/g')
  fi

  # Validate JSON
  if ! echo "$overrides" | jq -ecr '[ .[] | type == "string" ] | all' >/dev/null 2>&1; then
    log_error "Invalid value overrides format. Valid format is list of 'key=value'."
    exit 1
  fi

  CC_HELM_VALUE_OVERRIDES="$overrides"
  echo "CC_HELM_VALUE_OVERRIDES=$CC_HELM_VALUE_OVERRIDES" >> "$GITHUB_ENV"
}

helm_lint() {
  if [[ "${CC_HELM_LINT:-}" == "true" ]]; then
    log_info "Linting ${CC_HELM_CHART_PATH:-.}..."

    local values_file="" status=0
    local values_args=()
    if [[ -n "${CC_HELM_LINT_VALUES:-}" ]]; then
      if ! printf '%s' "$CC_HELM_LINT_VALUES" | jq -e 'type == "object"' >/dev/null 2>&1; then
        log_error "lint-values must be a JSON mapping."
        return 1
      fi
      values_file=$(mktemp)
      printf '%s' "$CC_HELM_LINT_VALUES" > "$values_file"
      values_args=(--values "$values_file")
    fi

    local set_args=""
    if echo "${CC_HELM_VALUE_OVERRIDES:-}" | jq -e 'length > 0' >/dev/null 2>&1; then
      log_info "Building value overrides..."
      set_args=$(echo "$CC_HELM_VALUE_OVERRIDES" | jq -cr '.[] | "--set " + .' | tr '\n' ' ')
      log_info "Value overrides: $set_args"
    fi

    # disable SC2086 because we want word splitting for set_args
    # shellcheck disable=SC2086
    helm lint "${CC_HELM_CHART_PATH:-.}" "${values_args[@]}" $set_args || status=$?
    if [[ -n "$values_file" ]]; then
      rm -f "$values_file"
    fi
    return "$status"
  fi
}

helm_template() {
  if [[ "${CC_HELM_TEMPLATE:-}" == "true" ]]; then
    log_info "Templating ${CC_HELM_CHART_PATH:-.}..."

    local set_args=""
    if echo "${CC_HELM_VALUE_OVERRIDES:-}" | jq -e 'length > 0' >/dev/null 2>&1; then
      set_args=$(echo "$CC_HELM_VALUE_OVERRIDES" | jq -cr '.[] | "--set " + .' | tr '\n' ' ')
    fi

    helm dependency build "${CC_HELM_CHART_PATH:-.}"
    # shellcheck disable=SC2086
    helm template "${CC_HELM_CHART_PATH:-.}" $set_args
  fi
}

helm_package() {
  log_info "Packaging ${CC_HELM_CHART_PATH:-.}..."
  mkdir -p "${CC_HELM_PACKAGE_DIR:-package}"

  local app_version_args=()
  if [[ -n "${CC_HELM_CHART_APP_VERSION:-}" ]]; then
    app_version_args=(--app-version "${CC_HELM_CHART_APP_VERSION}")
  fi

  helm package \
    --destination "${CC_HELM_PACKAGE_DIR:-package}" \
    --dependency-update \
    --version "${CC_HELM_CHART_VERSION}" \
    "${app_version_args[@]}" \
    "${CC_HELM_CHART_PATH:-.}"
}

helm_ngc_repo_add() {
  if [[ "${CC_HELM_NGC_PUSH:-}" != "false" ]]; then
    log_info "Adding NGC push repository..."
    echo "${CC_HELM_NGC_KEY}" \
    | helm repo add \
        --username=\$oauthtoken \
        --password-stdin \
        helm-repo-ngc \
        "${CC_HELM_NGC_REGISTRY}/${CC_HELM_NGC_PATH}"
    helm repo update helm-repo-ngc
  fi
}

helm_ngc_fetch() {
  log_info "Fetching from NGC..."
  mkdir -p "${CC_HELM_PACKAGE_DIR:-package}"
  helm fetch helm-repo-ngc/"${CC_HELM_CHART_NAME}" \
    --version "${CC_HELM_CHART_VERSION}" \
    --destination "${CC_HELM_PACKAGE_DIR:-package}"
}

helm_ngc_push() {
  if [[ "${CC_HELM_NGC_PUSH:-}" != "false" ]]; then
    log_info "Pushing to NGC..."

    export NGC_CLI_API_KEY="${CC_HELM_NGC_KEY}"
    unset NGC_API_KEY

    # Split Org/Team
    local ngc_org
    local ngc_team
    IFS="/" read -r ngc_org ngc_team <<< "${CC_HELM_NGC_PATH}"
    export NGC_CLI_ORG="${ngc_org:-no-org}"
    export NGC_CLI_TEAM="${ngc_team:-no-team}"

    ngc config current

    if ngc registry chart info "${CC_HELM_NGC_PATH}/${CC_HELM_CHART_NAME}" >/dev/null 2>&1; then
      log_info "${CC_HELM_CHART_NAME} already exists in ${CC_HELM_NGC_PATH}. No need to create."
    else
      log_warn "${CC_HELM_CHART_NAME} not found in ${CC_HELM_NGC_PATH}. Creating..."
      local desc="${CC_HELM_CHART_DESCRIPTION:-}"
      if [[ -z "$desc" && -n "${CC_HELM_CHART_TGZ:-}" && -f "${CC_HELM_CHART_TGZ}" ]]; then
        desc=$(helm show chart "${CC_HELM_CHART_TGZ}" | yq -r '.description // ""')
      elif [[ -n "${CC_HELM_CHART_PATH:-}" && -f "${CC_HELM_CHART_PATH}/Chart.yaml" ]]; then
        desc=$(yq -r '.description // ""' "${CC_HELM_CHART_PATH}/Chart.yaml")
      fi
      ngc registry chart create "${CC_HELM_NGC_PATH}/${CC_HELM_CHART_NAME}" --short-desc "$desc"
    fi

    local version_exists=false
    if ngc registry chart info "${CC_HELM_NGC_PATH}/${CC_HELM_CHART_NAME}:${CC_HELM_CHART_VERSION}" >/dev/null 2>&1; then
      log_warn "'${CC_HELM_CHART_NAME}' version ${CC_HELM_CHART_VERSION} already exists in NGC"
      version_exists=true
    fi

    local push_status="success"

    if [[ "$version_exists" == "true" && "${CC_HELM_NGC_DUPLICATE}" == "skip" ]]; then
      log_warn "Version already exists in NGC, skipping."
      push_status="skipped"
    elif [[ "$version_exists" == "true" && "${CC_HELM_NGC_DUPLICATE}" == "fail" ]]; then
      log_error "Version already exists in NGC."
      push_status="failed"
    else
      if [[ "$version_exists" == "true" && "${CC_HELM_NGC_DUPLICATE}" == "overwrite" ]]; then
        log_warn "Overwriting version in NGC."
        ngc registry chart remove -y "${CC_HELM_NGC_PATH}/${CC_HELM_CHART_NAME}:${CC_HELM_CHART_VERSION}"
      fi

      log_info "Pushing new version..."
      local chart_dirname
      chart_dirname=$(dirname "${CC_HELM_CHART_TGZ}")
      ngc registry chart push --source "$chart_dirname" "${CC_HELM_NGC_PATH}/${CC_HELM_CHART_NAME}:${CC_HELM_CHART_VERSION}"
    fi

    echo "CC_HELM_NGC_PUSH_STATUS=$push_status" >> "$GITHUB_ENV"
  fi
}

helm_check_push_errors() {
  local status="${CC_HELM_NGC_PUSH_STATUS:-}"
  if [[ "$status" == "failed" ]]; then
    log_error "Some pushes failed, check the logs. (exit code 1)"
    exit 1
  fi
  if [[ "$status" == "skipped" ]]; then
    log_warn "Some pushes were skipped, check the logs. (exit code 0 for GHA, used to be 66)"
    # In GHA, we usually don't want to fail the build for skip, so we exit 0 but maybe set an output
  fi
}

# Main dispatcher
cmd="$1"
shift
if [[ -n "$cmd" ]] && declare -f "$cmd" >/dev/null; then
  "$cmd" "$@"
else
  log_error "Function '$cmd' not found in utils.sh"
  exit 1
fi
