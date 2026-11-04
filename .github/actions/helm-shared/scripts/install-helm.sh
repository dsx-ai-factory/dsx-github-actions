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
# Usage: install-helm.sh [VERSION] [--force]
#   VERSION   Helm version to install (default: v3.13.2)
#   --force   Install even if Helm is already present
#   HELM_SHA256   Optional trusted archive digest; otherwise use Helm's HTTPS checksum
#
# shellcheck shell=bash
set -euo pipefail

RED=${RED:-$'\033[91m'}
GREEN=${GREEN:-$'\033[92m'}
RESET=${RESET:-$'\033[0m'}

# Log info message to stdout
log_info() { printf '%s[INFO]%s %s\n' "$GREEN" "$RESET" "$1"; }
# Log error message to stdout
log_error() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1"; }

# Parse arguments
HELM_VERSION="v3.13.2"
FORCE=false

for arg in "$@"; do
  case $arg in
    --force)
      FORCE=true
      ;;
    v*)
      HELM_VERSION="$arg"
      ;;
    *)
      log_error "Invalid argument"
      exit 1
      ;;
  esac
done

if [[ ! "$HELM_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
  log_error "Invalid Helm version. Expected a version such as v3.13.2."
  exit 1
fi

if command -v helm >/dev/null 2>&1 && [ "$FORCE" = false ]; then
  log_info "Helm already available, skipping installation. Use --force to reinstall."
  helm version --short
  exit 0
fi

log_info "Installing Helm $HELM_VERSION..."

# Determine OS and Architecture
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

if [[ "$ARCH" == "x86_64" ]]; then
  ARCH="amd64"
elif [[ "$ARCH" == "aarch64" ]]; then
  ARCH="arm64"
else
  log_error "Unsupported architecture: $ARCH"
  exit 1
fi

DOWNLOAD_URL="https://get.helm.sh/helm-${HELM_VERSION}-${OS}-${ARCH}.tar.gz"
log_info "Downloading from $DOWNLOAD_URL"

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

curl --proto '=https' --proto-redir '=https' -fsSL "$DOWNLOAD_URL" -o "$WORK_DIR/helm.tar.gz"

if [[ -n "${HELM_SHA256:-}" ]]; then
  HELM_SHA256=$(printf '%s' "$HELM_SHA256" | tr '[:upper:]' '[:lower:]')
else
  curl --proto '=https' --proto-redir '=https' -fsSL "${DOWNLOAD_URL}.sha256" -o "$WORK_DIR/helm.sha256"
  HELM_SHA256=$(tr '[:upper:]' '[:lower:]' < "$WORK_DIR/helm.sha256")
fi
if [[ ! "$HELM_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  log_error "Missing or invalid Helm SHA256 checksum."
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  CHECKSUM_COMMAND=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
  CHECKSUM_COMMAND=(shasum -a 256)
else
  log_error "Helm verification requires sha256sum or shasum."
  exit 1
fi

# Validate the digest before extracting or executing any downloaded content.
if ! (
  cd "$WORK_DIR" || exit 1
  printf '%s  helm.tar.gz\n' "$HELM_SHA256" | "${CHECKSUM_COMMAND[@]}" -c -
); then
  log_error "Helm SHA256 verification failed."
  exit 1
fi

tar -xzf "$WORK_DIR/helm.tar.gz" -C "$WORK_DIR"

INSTALL_DIR="$HOME/.local/bin"
mkdir -p "$INSTALL_DIR"
cp "$WORK_DIR/${OS}-${ARCH}/helm" "$INSTALL_DIR/helm"
chmod +x "$INSTALL_DIR/helm"

# Add to PATH if not already there (for GHA)
echo "$INSTALL_DIR" >>"$GITHUB_PATH"

log_info "Helm installed successfully at $INSTALL_DIR/helm"
"$INSTALL_DIR/helm" version
