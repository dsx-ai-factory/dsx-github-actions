#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
: "${SYFT_IMAGE:?required}" "${GRYPE_IMAGE:?required}" "${SKOPEO_IMAGE:?required}"
PLATFORM="${PLATFORM:-linux/amd64}"
CHECK_HOST_SKOPEO="${CHECK_HOST_SKOPEO:-false}"
[[ "$CHECK_HOST_SKOPEO" == true || "$CHECK_HOST_SKOPEO" == false ]] || exit 1
for tool in docker timeout grep; do command -v "$tool" >/dev/null; done
if [[ "$CHECK_HOST_SKOPEO" == true ]]; then command -v skopeo >/dev/null; fi

test_dir="$(mktemp -d "${TMPDIR:-/tmp}/tool-trust.XXXXXX")"
containers=()
total=0 failures=0 sequence=0
cleanup() {
  if (( ${#containers[@]} )); then
    timeout --kill-after=5s 20s docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$test_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$test_dir/docker" "$test_dir/home" "$test_dir/runtime" "$test_dir/db"
# Do not use the runner's registry credentials, including for tool-image pulls.
export DOCKER_CONFIG="$test_dir/docker"

run_container() {
  local seconds="$1" rc=0 name
  shift
  sequence=$((sequence + 1))
  name="tool-trust-${test_dir##*.}-$$-$sequence"
  containers+=("$name")
  timeout --kill-after=5s "${seconds}s" docker run --rm --pull=never \
    --name "$name" --platform "$PLATFORM" --user "$(id -u):$(id -g)" \
    --cap-drop=ALL --security-opt=no-new-privileges \
    -e HOME=/tmp -e XDG_CACHE_HOME=/tmp/.cache "$@" || rc=$?
  # Killing the Docker client does not stop its container. Remove it explicitly.
  if (( rc != 0 )); then
    timeout --kill-after=5s 15s docker rm -f "$name" >/dev/null 2>&1 || true
  fi
  return "$rc"
}

check() {
  local expected="$1" label="$2" host="$3" rc=0 matched=false
  shift 3
  total=$((total + 1))
  "$@" > "$test_dir/result.log" 2>&1 || rc=$?
  if [[ "$expected" == success ]]; then
    (( rc == 0 )) && matched=true
  elif (( rc > 0 && rc < 124 )); then
    # Require both the exact hostname and a verification error on the same line.
    # A timeout, DNS failure, HTTP error or missing manifest is not TLS rejection.
    if grep -Ei 'x509: certificate (signed by unknown authority|has expired|is valid for|is not valid for|is not authorized|relies on)|failed to verify certificate|certificate verif(y|ication) failed' \
      "$test_dir/result.log" | grep -E "(^|[^[:alnum:]_.-])${host//./\\.}([^[:alnum:]_.-]|$)" \
      > "$test_dir/tls.log"; then matched=true; fi
  fi
  if [[ "$matched" == true ]]; then
    printf 'PASS %s\n' "$label"
    if [[ "$expected" == rejection ]]; then sed -n '1,2p' "$test_dir/tls.log"; fi
  else
    failures=$((failures + 1))
    printf 'FAIL %s (exit %s, expected %s)\n' "$label" "$rc" "$expected" >&2
    tail -n 8 "$test_dir/result.log" >&2
  fi
}

for image in "$SYFT_IMAGE" "$GRYPE_IMAGE" "$SKOPEO_IMAGE"; do
  if ! timeout --kill-after=5s 180s docker pull --platform "$PLATFORM" "$image" \
    > "$test_dir/pull.log" 2>&1; then
    printf 'FAIL tool-image pull: %s\n' "$image" >&2
    tail -n 8 "$test_dir/pull.log" >&2
    exit 1
  fi
done

fixture=registry.k8s.io/pause:3.10
syft_env=(-e SYFT_CHECK_FOR_APP_UPDATE=false)
grype_env=(-e GRYPE_CHECK_FOR_APP_UPDATE=false)
# Only a fresh vulnerability DB is shared, never Docker auth or the host socket.
db_cache=(--volume "$test_dir/db:/tmp/db" -e GRYPE_DB_CACHE_DIR=/tmp/db)
check success 'skopeo public registry' '' run_container 90 \
  "$SKOPEO_IMAGE" inspect --no-creds "docker://$fixture"
check success 'syft public registry' '' run_container 90 "${syft_env[@]}" \
  "$SYFT_IMAGE" "registry:$fixture" --platform "$PLATFORM" -o json
check success 'grype public DB update' '' run_container 300 "${grype_env[@]}" \
  "${db_cache[@]}" "$GRYPE_IMAGE" db update
check success 'grype public registry' '' run_container 90 "${grype_env[@]}" \
  "${db_cache[@]}" -e GRYPE_DB_AUTO_UPDATE=false \
  "$GRYPE_IMAGE" "registry:$fixture" --platform "$PLATFORM" -o json

host_skopeo=(env -i "PATH=$PATH" "HOME=$test_dir/home"
  "XDG_CONFIG_HOME=$test_dir/home" "XDG_RUNTIME_DIR=$test_dir/runtime"
  skopeo inspect --no-creds)
if [[ "$CHECK_HOST_SKOPEO" == true ]]; then
  check success 'host skopeo public registry' '' timeout --kill-after=5s 90s \
    "${host_skopeo[@]}" "docker://$fixture"
fi

hosts=(
  dv-g3b-emudhra.we-have-a-problem.com
  dv-g3b-aaa-emudhra.we-have-a-problem.com
  dv-g3b-noct-emudhra.we-have-a-problem.com
  dv-g3b-aaa-noct-emudhra.we-have-a-problem.com
)
for host in "${hosts[@]}"; do
  check rejection "skopeo registry $host" "$host" run_container 60 \
    "$SKOPEO_IMAGE" inspect --no-creds "docker://$host/x/y:z"
  check rejection "syft registry $host" "$host" run_container 60 "${syft_env[@]}" \
    "$SYFT_IMAGE" "registry:$host/x/y:z"
  check rejection "grype registry $host" "$host" run_container 60 "${grype_env[@]}" \
    "${db_cache[@]}" -e GRYPE_DB_AUTO_UPDATE=false "$GRYPE_IMAGE" "registry:$host/x/y:z"
  # Separate, uncached DB requests exercise Grype's HTTPS download client too.
  check rejection "grype DB $host" "$host" run_container 60 "${grype_env[@]}" \
    -e "GRYPE_DB_UPDATE_URL=https://$host/listing.json" -e GRYPE_DB_CACHE_DIR=/tmp/db \
    "$GRYPE_IMAGE" db update
  if [[ "$CHECK_HOST_SKOPEO" == true ]]; then
    check rejection "host skopeo registry $host" "$host" timeout --kill-after=5s 60s \
      "${host_skopeo[@]}" "docker://$host/x/y:z"
  fi
done
printf '\nTool trust: %s/%s passed, %s failed (%s)\n' "$((total - failures))" "$total" "$failures" "$PLATFORM"
(( failures == 0 ))
