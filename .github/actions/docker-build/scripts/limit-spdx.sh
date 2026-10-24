#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
fail() { printf '::error::%s\n' "$*" >&2; exit 1; }
report="${1:?Expected an SPDX report path}"
[[ "$report" == *.spdx.json && -s "$report" ]] || fail 'Expected a nonempty .spdx.json report'
# actions/attest limits the complete predicate, including when registry-only.
maximum=16777216
bytes="$(wc -c < "$report")"
(( bytes > maximum )) || exit 0

original="${report%.spdx.json}.spdx.original.json"
mapping="${report%.spdx.json}.spdx-id-map.json"
[[ ! -e "$original" && ! -e "$mapping" ]] || fail 'Refusing to replace existing SPDX source evidence'
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
temporary="$(mktemp -d "$(dirname -- "$report")/.spdx-compact.XXXXXX")"
trap 'rm -rf "$temporary"' EXIT

jq -ce -f "$script_dir/compact-spdx.jq" "$report" > "$temporary/result.json"
jq -c '.document' "$temporary/result.json" > "$temporary/report.json"
compact_bytes="$(wc -c < "$temporary/report.json")"
(( compact_bytes <= maximum )) || fail 'Complete SPDX report still exceeds the 16 MiB signing limit; no evidence was removed'
jq -c '.idMap' "$temporary/result.json" > "$temporary/id-map.json"

# Keep the exact Syft document and inverse map in the same evidence artifact.
cp -- "$report" "$original"
mv -- "$temporary/id-map.json" "$mapping"
mv -- "$temporary/report.json" "$report"
printf 'SPDX file identifiers compacted: %s -> %s bytes; original evidence retained\n' "$bytes" "$compact_bytes"
