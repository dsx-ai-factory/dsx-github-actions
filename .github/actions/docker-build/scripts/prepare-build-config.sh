#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
original="${DOCKER_CONFIG:-$HOME/.docker}"
case "${ANONYMOUS_BUILD:?}" in
  false) echo "config=$original" >> "$GITHUB_OUTPUT"; exit 0 ;;
  true) ;;
  *) echo '::error::anonymous-build must be true or false' >&2; exit 64 ;;
esac

config="$(mktemp -d "${RUNNER_TEMP}/docker-anonymous.XXXXXX")"
# Record cleanup ownership before any preparation that can fail.
echo "config=$config" >> "$GITHUB_OUTPUT"
if [[ -f "$original/config.json" ]]; then
  jq '{auths: {}} +
    (if has("currentContext") then {currentContext} else {} end) +
    (if has("proxies") then {proxies} else {} end)' \
    "$original/config.json" > "$config/config.json"
else
  echo '{"auths":{}}' > "$config/config.json"
fi
# Retain daemon access, builder selection, and CLI plugins, without registry auth.
if [[ -d "$original" ]]; then
  original="$(cd "$original" && pwd)"
  for directory in contexts buildx cli-plugins; do
    if [[ -d "$original/$directory" ]]; then
      ln -s "$original/$directory" "$config/$directory"
    fi
  done
fi
