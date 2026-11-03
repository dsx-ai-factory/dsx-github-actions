#!/usr/bin/env python3
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a preview ID without Fern truncating its repository or PR number."""

import argparse
import json
import os
from pathlib import Path
import re
import sys


def preview_id(organization, namespace, repository_id, pr_number):
    if not isinstance(organization, str) or not re.fullmatch(
            r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?', organization):
        raise ValueError('Fern organization must be a valid hostname label')
    # Accept only names unchanged by Fern's lowercase/hyphen sanitizer.
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', namespace):
        raise ValueError('preview_namespace must use lowercase letters, digits, and single internal hyphens')
    for name, value in [('GITHUB_REPOSITORY_ID', repository_id), ('PR_NUMBER', pr_number)]:
        if not re.fullmatch(r'[1-9][0-9]*', value):
            raise ValueError(f'{name} must be a positive integer without leading zeros')
    result = f'{namespace}-r{repository_id}-pr-{pr_number}'
    # Fern CLI 5.186.0 truncates this label to 62 characters, below DNS's 63.
    if len(f'{organization.lower()}-preview-{result}') > 62:
        raise ValueError('Preview hostname exceeds Fern\'s 62-character limit; shorten preview_namespace')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    print(preview_id(config['organization'], os.environ['PREVIEW_NAMESPACE'],
                     os.environ['GITHUB_REPOSITORY_ID'], os.environ['PR_NUMBER']))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'Preview ID validation failed: {error}', file=sys.stderr)
        sys.exit(1)
