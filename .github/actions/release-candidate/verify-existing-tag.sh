#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
: "${RC_TAG:?RC_TAG is required}"
: "${RC_VERSION:?RC_VERSION is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"
node "$(dirname "$0")/validate-version.cjs"
[[ "$RC_TAG" == "v${RC_VERSION}" ]]
[[ "$(git rev-parse "${RC_TAG}^{commit}")" == "$GITHUB_SHA" ]]

# semantic-release 25 uses a separate channel-notes ref for each tag.
note_ref="refs/notes/semantic-release-${RC_TAG}"
if ! git fetch --no-tags origin "+${note_ref}:${note_ref}"; then
  echo "Existing RC has no readable channel metadata. A maintainer must repair the original release. No tag was changed." >&2
  exit 1
fi
git notes --ref="semantic-release-${RC_TAG}" show "$RC_TAG" | node -e '
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => { input += chunk; });
process.stdin.on("end", () => {
  try {
    const note = JSON.parse(input);
    if (!Array.isArray(note.channels) || !note.channels.includes("rc")) {
      throw new Error("missing rc channel");
    }
  } catch {
    console.error("Existing RC channel metadata is invalid. A maintainer must repair it.");
    process.exitCode = 1;
  }
});
'
