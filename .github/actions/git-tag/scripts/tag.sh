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

set +x
set -euo pipefail

log_info() { echo -e "\033[32m[INFO]\033[0m $1"; }
log_error() { echo -e "\033[31m[ERROR]\033[0m $1"; }

install_git() {
    if ! command -v git &> /dev/null; then
        log_info "Git not found. Installing..."
        if command -v sudo &> /dev/null; then
            sudo apt-get update && sudo apt-get install -y git
        else
            apt-get update && apt-get install -y git
        fi
    else
        log_info "Git is already installed."
    fi
}

configure_git() {
    if [[ -z "$(git config user.name)" ]]; then
        log_info "Configuring git user.name..."
        git config user.name "github-actions[bot]"
    fi
    if [[ -z "$(git config user.email)" ]]; then
        log_info "Configuring git user.email..."
        git config user.email "github-actions[bot]@users.noreply.github.com"
    fi
}

main() {
    local tag="${INPUT_TAG:-}"

    if [[ -z "$tag" ]]; then
        log_error "Tag name is required."
        exit 1
    fi

    log_info "Tag: $tag"

    install_git
    configure_git

    log_info "Creating tag: $tag"
    if git rev-parse "$tag" >/dev/null 2>&1; then
        log_info "Tag $tag already exists locally."
    else
        git tag "$tag"
    fi

    log_info "Pushing tag: $tag"

    if [[ -n "${INPUT_GITHUB_TOKEN:-}" ]]; then
        # Supply credentials only when Git asks, never in the remote URL or argv.
        GIT_ASKPASS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/git-askpass.sh" \
        GIT_TERMINAL_PROMPT=0 \
            git -c credential.helper= -c http.https://github.com/.extraheader= \
            push "https://github.com/${GITHUB_REPOSITORY}.git" "$tag"
    else
        git push origin "$tag"
    fi
}

main "$@"
