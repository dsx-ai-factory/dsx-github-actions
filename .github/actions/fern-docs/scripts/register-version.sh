#!/bin/bash
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

[[ "${TAG_VERSION:-}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
[[ "${MAX_VERSIONS:-}" =~ ^[1-9][0-9]*$ ]] || exit 1

if [ -f "fern/versions/${TAG_VERSION}.yml" ]; then
  echo "Version ${TAG_VERSION} already registered, skipping"
else
  mkdir -p fern/versions
# Page paths in the tag's index.yml are repository paths. The frozen
# content step below keeps them under <tag>-content/. yq rewrites only
# the path values, so quoted values and other text stay intact.
  export TAG_VERSION
  git show "refs/tags/${TAG_VERSION}:index.yml" | \
    yq '(.. | select(tag == "!!map" and has("path")) | .path) |= env(TAG_VERSION) + "-content/" + .' \
    > "fern/versions/${TAG_VERSION}.yml"

  EXPR='.products[0].versions += [{"display-name": env(TAG_VERSION),'
  EXPR+=' "path": "versions/" + env(TAG_VERSION) + ".yml",'
  EXPR+=' "slug": env(TAG_VERSION), "availability": "stable"}]'
  yq -i "$EXPR" fern/docs.yml
fi

# Sort all version entries by semver descending
SORTED_SLUGS=$(yq '.products[0].versions[].slug' fern/docs.yml | sort -rV)
yq -i '.products[0].versions = []' fern/docs.yml
for slug in $SORTED_SLUGS; do
  export slug
  yq -i '.products[0].versions += [{"display-name": env(slug), "path": "versions/" + env(slug) + ".yml", "slug": env(slug), "availability": "stable"}]' fern/docs.yml
done

echo "--- docs.yml versions after insert and sort ---"
yq '.products[0].versions[].display-name' fern/docs.yml


TOTAL=$(yq '.products[0].versions | length' fern/docs.yml)

if [ "$TOTAL" -le "$MAX_VERSIONS" ]; then
  echo "Only $TOTAL versions, nothing to prune (keeping $MAX_VERSIONS)"
  exit 0
fi

PRUNED=$(yq ".products[0].versions | .[$MAX_VERSIONS:] | .[].slug" fern/docs.yml)
yq -i ".products[0].versions |= .[:$MAX_VERSIONS]" fern/docs.yml

for slug in $PRUNED; do
  [[ "$slug" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
  rm -f "fern/versions/${slug}.yml"
  echo "Pruned $slug"
done

echo "--- docs.yml versions after prune ---"
yq '.products[0].versions[].display-name' fern/docs.yml
