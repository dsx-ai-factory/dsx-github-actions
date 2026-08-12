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

log_info() { printf '%s[INFO]%s %s\n' "$GREEN" "$RESET" "$1"; }
log_warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$1"; }
log_error() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$1"; }

if command -v ngc >/dev/null 2>&1; then
  log_info "NGC CLI already available, skipping installation."
  exit 0
fi

NGCCLI_VERSION="${NGCCLI_VERSION:-4.9.17}"

# NGC ships a separate CLI build per architecture. ngccli_linux.zip is x86_64-only —
# running it on an arm64 runner fails with "cannot execute binary file: Exec format error",
# so select the matching archive for the runner's architecture.
case "$(uname -m)" in
  x86_64 | amd64) ngc_cli_file="ngccli_linux.zip" ;;
  aarch64 | arm64) ngc_cli_file="ngccli_arm64.zip" ;;
  *) log_error "Unsupported architecture for NGC CLI: $(uname -m)"; exit 1 ;;
esac

case "${NGCCLI_VERSION}:${ngc_cli_file}" in
  4.9.17:ngccli_linux.zip)
    expected_sha256="b1cc4299151cdcbc2bd18318c0a3b7f321f3e62e20ba234b6a8a0e7024cf3b44"
    ;;
  4.9.17:ngccli_arm64.zip)
    expected_sha256="7945c17290b0bb4003b01065149a4d2f254335e56037b16d9417c1aec566e60c"
    ;;
  *)
    expected_sha256="${NGCCLI_SHA256:-}"
    if [[ -z "$expected_sha256" ]]; then
      log_error "No trusted checksum configured for NGC CLI ${NGCCLI_VERSION} (${ngc_cli_file})"
      exit 1
    fi
    ;;
esac

download_url="https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/${NGCCLI_VERSION}/files/${ngc_cli_file}"
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

log_info "Downloading NGC CLI from $download_url"
curl -sSfL "$download_url" -o "$work_dir/ngccli.zip"
printf '%s  %s\n' "$expected_sha256" "$work_dir/ngccli.zip" | sha256sum -c -
log_info "Verified NGC CLI SHA256 checksum"
unzip -q "$work_dir/ngccli.zip" -d "$work_dir"

extract_dir=$(find "$work_dir" -maxdepth 2 -type d -name 'ngc-cli*' | head -n 1)
if [[ -z "$extract_dir" ]]; then
  log_error "Failed to locate extracted ngc-cli directory"
  exit 1
fi

install_root="$HOME/.local/ngc-cli"
rm -rf "$install_root"
mkdir -p "$install_root"
cp -R "$extract_dir"/. "$install_root/"

bin_path="$install_root/ngc"
if [[ ! -f "$bin_path" ]]; then
  log_error "Unable to find ngc executable after installation"
  exit 1
fi
chmod +x "$bin_path"

if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$install_root" >> "$GITHUB_PATH"
else
  export PATH="$install_root:$PATH"
fi

log_info "NGC CLI installed at $install_root/ngc"

if [[ -d /usr/local/bin && -w /usr/local/bin ]]; then
  ln -sf "$bin_path" /usr/local/bin/ngc
  log_info "Symlinked ngc into /usr/local/bin"
else
  log_warn "Skipping symlink into /usr/local/bin (directory not writable); PATH updated via GITHUB_PATH."
fi

"$bin_path" --version
