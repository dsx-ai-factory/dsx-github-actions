#!/bin/bash
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

[[ "${TAG_VERSION:-}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]] || exit 1
[[ "${MAX_VERSIONS:-}" =~ ^[1-9][0-9]*$ ]] || exit 1

FERN_DOCS_PATH=${FERN_DOCS_PATH:-fern/docs.yml}
FERN_VERSIONS_DIRECTORY=${FERN_VERSIONS_DIRECTORY:-fern/versions}

if [ -f "${FERN_VERSIONS_DIRECTORY}/${TAG_VERSION}.yml" ]; then
  echo "Version ${TAG_VERSION} already registered, skipping"
else
  mkdir -p "$FERN_VERSIONS_DIRECTORY"
# Page paths in the tag's index.yml are repository paths. The frozen
# content step below keeps them under <tag>-content/. yq rewrites only
# the path values, so quoted values and other text stay intact.
  export TAG_VERSION
  git show "refs/tags/${TAG_VERSION}:index.yml" | \
    yq '(.. | select(tag == "!!map" and has("path")) | .path) |= env(TAG_VERSION) + "-content/" + .' \
    > "${FERN_VERSIONS_DIRECTORY}/${TAG_VERSION}.yml"

  EXPR='.products[0].versions += [{"display-name": env(TAG_VERSION),'
  EXPR+=' "path": "versions/" + env(TAG_VERSION) + ".yml",'
  EXPR+=' "slug": env(TAG_VERSION), "availability": "stable"}]'
  yq -i "$EXPR" "$FERN_DOCS_PATH"
fi

# Stable releases sort above prereleases with the same core version.
SORTED_SLUGS=$(yq '.products[0].versions[].slug' "$FERN_DOCS_PATH" | \
  python3 "$(dirname "${BASH_SOURCE[0]}")/sort-versions.py")
yq -i '.products[0].versions = []' "$FERN_DOCS_PATH"
for slug in $SORTED_SLUGS; do
  export slug
  yq -i '.products[0].versions += [{"display-name": env(slug), "path": "versions/" + env(slug) + ".yml", "slug": env(slug), "availability": "stable"}]' "$FERN_DOCS_PATH"
done

echo "--- docs.yml versions after insert and sort ---"
yq '.products[0].versions[].display-name' "$FERN_DOCS_PATH"


TOTAL=$(yq '.products[0].versions | length' "$FERN_DOCS_PATH")

if [ "$TOTAL" -le "$MAX_VERSIONS" ]; then
  echo "Only $TOTAL versions, nothing to prune (keeping $MAX_VERSIONS)"
  exit 0
fi

# Keep the selected tag even when a manual run selects an older version.
PRUNED=$(yq ".products[0].versions | map(select(.slug != env(TAG_VERSION))) | .[$((MAX_VERSIONS - 1)):][].slug" "$FERN_DOCS_PATH")

for slug in $PRUNED; do
  [[ "$slug" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]] || exit 1
  export slug
  yq -i '.products[0].versions |= map(select(.slug != env(slug)))' "$FERN_DOCS_PATH"
  rm -f "${FERN_VERSIONS_DIRECTORY}/${slug}.yml"
  echo "Pruned $slug"
done

echo "--- docs.yml versions after prune ---"
yq '.products[0].versions[].display-name' "$FERN_DOCS_PATH"
