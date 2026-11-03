#!/usr/bin/env python3
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare and check Fern inputs without a publication token."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parent


def run(script, *args):
    subprocess.run([sys.executable, str(script), *map(str, args)], check=True)


def relative_path(root, value):
    path = Path(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value:
        raise ValueError('Expected a repository-relative path')
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Path leaves the repository')
    return root / path


def stage(source, destination, navigation, docs_directory, with_fern=False, fern_working_directory='.'):
    source = source.absolute()
    destination = destination.absolute()
    if navigation:
        # Identity currently publishes from this root navigation file.
        if navigation != 'index.yml':
            raise ValueError('Navigation layout currently requires root index.yml')
        run(SCRIPTS / 'stage-fern-site', source, destination)
    else:
        docs = relative_path(source, docs_directory)
        if not docs.is_dir() or docs.is_symlink():
            raise ValueError('Documentation directory is missing or a symbolic link')
        if Path(docs_directory).parts[0:1] in [('fern',), ('tools',), ('.github',), ('.git',)] or docs.resolve() == source.resolve():
            raise ValueError('Documentation directory overlaps trusted files')
        if any(path.is_symlink() for path in docs.rglob('*')):
            raise ValueError('Documentation contains a symbolic link')
        target = relative_path(destination, docs_directory)
        # Check every path component before deleting, including dangling links.
        # A destination such as content -> tools must not bypass the root guard.
        for root in (source, destination):
            path = root
            for part in ('.', *Path(docs_directory).parts):
                path = path / part
                if path.is_symlink():
                    raise ValueError('Documentation path contains a symbolic link')
        if docs.resolve().is_relative_to(target.resolve()) or target.resolve().is_relative_to(docs.resolve()):
            raise ValueError('Source and destination documentation directories overlap')
        if target.exists():
            if not target.is_dir():
                raise ValueError('Documentation destination is not a directory')
            shutil.rmtree(target)
        shutil.copytree(docs, target, symlinks=True)
    if with_fern:
        config = relative_path(source, fern_working_directory) / 'fern'
        target = relative_path(destination, fern_working_directory) / 'fern'
        shutil.copytree(config, target, symlinks=True, dirs_exist_ok=True)


def prepare(root, version, navigation, docs_directory, docs_paths, frozen, mdx, fern_working_directory='.'):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('fern_cli_version must be an exact version')
    fern_directory = relative_path(root, fern_working_directory) / 'fern'
    config = json.loads((fern_directory / 'fern.config.json').read_text())
    if config.get('version') != version:
        raise ValueError('Fern CLI version differs from fern/fern.config.json')
    if frozen:
        if navigation != 'index.yml':
            raise ValueError('Frozen versions require root index.yml')
        for registry in sorted((fern_directory / 'versions').glob('v*.yml')):
            if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', registry.stem):
                raise ValueError('Invalid registered version')
            subprocess.run([
                sys.executable, str(SCRIPTS / 'fern-frozen-content'), registry.stem,
                str(registry.with_name(registry.stem + '-content').resolve()),
                os.environ['GITHUB_SERVER_URL'] + '/' + os.environ['GITHUB_REPOSITORY'],
            ], cwd=root, check=True)
    with tempfile.TemporaryDirectory() as directory:
        staged = Path(directory)
        stage(root, staged, navigation, docs_directory, with_fern=True, fern_working_directory=fern_working_directory)
        staged_fern = relative_path(staged, fern_working_directory) / 'fern'
        allowed = [os.path.relpath(staged / 'index.yml', staged_fern)] if navigation else [
            os.path.relpath(path, staged_fern)
            for path in relative_path(staged, docs_directory).rglob('*')
            if not path.is_symlink()
        ]
        arguments = [value for path in allowed for value in ('--allow', path)]
        run(SCRIPTS / 'check-docs-paths', *arguments, staged)
    if mdx:
        paths = [relative_path(root, value) for value in docs_paths.splitlines() if value]
        if not paths:
            raise ValueError('docs_paths must include at least one path')
        run(SCRIPTS.parents[2] / 'scripts/check_docs_mdx.py', *paths)


def main():
    if sys.version_info < (3, 12):
        raise ValueError('Python 3.12 or newer is required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['check', 'stage'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    nav = os.environ.get('NAVIGATION_FILE', '')
    docs = os.environ.get('DOCS_DIRECTORY', 'docs')
    if args.operation == 'stage':
        if args.destination is None:
            raise ValueError('Missing staging destination')
        stage(source, args.destination.absolute(), nav, docs)
    else:
        prepare(source, os.environ['FERN_CLI_VERSION'], nav, docs,
                os.environ.get('DOCS_PATHS') or docs,
                os.environ.get('FROZEN_VERSIONS') == 'true',
                os.environ.get('CHECK_MDX', 'true') == 'true',
                os.environ.get('FERN_WORKING_DIRECTORY', '.'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Fern preparation failed: {error}', file=sys.stderr)
        sys.exit(1)
