#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
: "${GITHUB_REF:?GITHUB_REF is required}"
repository_url="$(node -e 'process.stdout.write(require(process.argv[1]).repositoryUrl(process.env.GITHUB_REPOSITORY, true))' "$(dirname "$0")/config.cjs")"
push_url="$(git config --get remote.origin.pushurl || git config --get remote.origin.url)"
if [[ "$push_url" != "$repository_url" ]]; then
  echo "::error::Deploy Key authentication requires the canonical SSH repository URL."
  exit 1
fi

# Check receive-pack authorization without changing any remote branch or tag.
if ! git push --dry-run --no-verify origin "HEAD:$GITHUB_REF"; then
  echo "::error::Deploy Key cannot push to this repository. Refusing HTTPS token fallback."
  exit 1
fi
