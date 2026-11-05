#!/usr/bin/env python3
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sort registered version tags by descending SemVer precedence."""

import sys


def precedence(tag):
    version = tag.removeprefix('v').split('+', 1)[0]
    core, separator, prerelease = version.partition('-')
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in prerelease.split('.')
    ) if separator else ()
    return tuple(map(int, core.split('.'))), not separator, identifiers


if __name__ == '__main__':
    # Stable sorting preserves registry order when only build metadata differs.
    tags = sorted(sys.stdin.read().splitlines(), key=precedence, reverse=True)
    sys.stdout.writelines(f'{tag}\n' for tag in tags)
