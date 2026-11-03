#!/usr/bin/env python3
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render Fern slug mappings as escaped Markdown links."""

import json
import re
import sys
from urllib.parse import quote


def render(base, mappings):
    lines = []
    for mapping in mappings:
        slug = mapping.get('slug')
        if not isinstance(slug, str) or not slug:
            continue
        label = re.sub(r'([\\`*_{}\[\]()<>!|])', r'\\\1', slug.replace('\r', ' ').replace('\n', ' '))
        lines.append(f'- [{label}]({base.rstrip("/")}/{quote(slug, safe="/")})')
    return '\n'.join(lines)


if __name__ == '__main__':
    print(render(sys.argv[1], json.load(sys.stdin).get('mappings', [])))
