#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

# Resolve only this destination's Docker credentials on the host. Never print
# helper output or pass credentials in argv; the caller removes the authfile.
set +x
set -euo pipefail
umask 077
ref="${1:?Image reference required}"
authfile="${2:?Private authfile path required}"
config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
fail() { echo '::error::Unable to resolve Docker credentials for publication' >&2; exit 1; }

server="${ref%%/*}"
if [[ "$ref" != */* ]] || [[ "$server" != *[.:]* && "$server" != localhost && "$server" != *[A-Z]* ]]; then
  server=docker.io
fi
if [[ "$server" == docker.io || "$server" == index.docker.io ]]; then
  server=https://index.docker.io/v1/
fi

printf '{"auths":{}}\n' > "$authfile"
chmod 600 "$authfile"
[[ -f "$config" ]] || exit 0
helper="$(jq -r --arg server "$server" '.credHelpers[$server] // .credsStore // ""' "$config" 2>/dev/null)" || fail
if [[ -n "$helper" ]]; then
  if ! credentials="$(printf '%s' "$server" | "docker-credential-$helper" get 2>/dev/null)"; then
    # Docker treats the standard helper "not found" response as anonymous.
    [[ "$credentials" == 'credentials not found in native keychain' ]] && exit 0
    fail
  fi
  printf '%s' "$credentials" | jq -e --arg server "$server" '
    if (.Username | type) != "string" or (.Secret | type) != "string" then
      error("Invalid helper response")
    else
      {auths: {($server): (if .Username == "<token>" then
        {identitytoken: .Secret}
      else
        {auth: ((.Username + ":" + .Secret) | @base64)}
      end)}}
    end' > "$authfile" 2>/dev/null || fail
  unset credentials
else
  # Preserve Docker's legacy URL-form auth keys, but do not export unrelated
  # registries or helper settings into the container.
  jq --arg server "$server" '
    (.auths // {}) as $auths |
    ($auths[$server] // ([$auths | to_entries[] |
      select((.key | sub("^https?://"; "") | split("/")[0]) == $server) |
      .value][0]) // {}) as $auth |
    {auths: {($server): $auth}}' "$config" > "$authfile" 2>/dev/null || fail
fi
