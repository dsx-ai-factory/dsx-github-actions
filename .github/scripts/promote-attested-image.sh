#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Run only after every subject's signed evidence has been verified.
set -euo pipefail
: "${IMAGE:?}" "${EXPECTED_DIGEST:?}" "${IMAGE_TAGS:?}"
[[ "$IMAGE" =~ ^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+$ ]]
[[ "$EXPECTED_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]]
tags=()
while IFS= read -r tag; do
  [[ -n "$tag" ]] || continue
  suffix="${tag#"$IMAGE:"}"
  [[ "$tag" == "$IMAGE:"* && "$suffix" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]] || {
    echo '::error::Release tags must belong to the attested image repository'
    exit 1
  }
  tags+=(--tag "$tag")
done <<< "$IMAGE_TAGS"
(( ${#tags[@]} > 0 ))
# Do not wrap a plain single-platform manifest in a new index.
docker buildx imagetools create --prefer-index=false "${tags[@]}" "$IMAGE@$EXPECTED_DIGEST"
while IFS= read -r tag; do
  [[ -n "$tag" ]] || continue
  digest="$(docker buildx imagetools inspect "$tag" --format '{{json .Manifest}}' | jq -er '.digest')"
  [[ "$digest" == "$EXPECTED_DIGEST" ]] || {
    echo '::error::Release tag digest differs from the attested digest'
    exit 1
  }
done <<< "$IMAGE_TAGS"
