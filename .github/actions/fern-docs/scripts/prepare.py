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


def config_path(root, value):
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', value) or '.git' in Path(value).parts:
        raise ValueError('Expected a repository-relative configuration path')
    path = relative_path(root, value)
    current = root
    for part in Path(value).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Configuration path contains a symbolic link')
    return path


def configure(root, version, fern_working_directory='.', docs_path='', organization='nvidia', *, legacy_release=False):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('fern_cli_version must be an exact version')
    fern = relative_path(root, fern_working_directory) / 'fern'
    if not docs_path:
        if json.loads((fern / 'fern.config.json').read_text()).get('version') != version:
            raise ValueError('Fern CLI version differs from fern/fern.config.json')
        return fern / 'docs.yml', fern / 'versions'
    target = config_path(root, (fern / 'docs.yml').relative_to(root).as_posix())
    config = config_path(root, (fern / 'fern.config.json').relative_to(root).as_posix())
    if not re.fullmatch(r'[A-Za-z0-9_-]+', organization):
        raise ValueError('fern_organization must be a Fern organization name')
    source = config_path(root, docs_path)
    if source.suffix.lower() not in ('.yml', '.yaml'):
        raise ValueError('fern_docs_path must name an existing YAML file')
    # The publisher checks the configured path on the default branch first.
    # Older release trees can still keep their site YAML at the original path.
    if legacy_release and not source.exists():
        source = target
    if not source.is_file():
        raise ValueError('fern_docs_path must name an existing YAML file')
    registry = config_path(root, (source.parent / 'versions').relative_to(root).as_posix())
    destination = config_path(root, (fern / 'versions').relative_to(root).as_posix())
    if any(path.exists() and not path.is_dir() for path in (registry, destination)):
        raise ValueError('Version registry must be a directory')
    entries = list(registry.glob('v*.yml'))
    previous = list(destination.glob('v*.yml'))
    # Validate both sides before replacing generated files or pruning old entries.
    for path in [*entries, *previous]:
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\.yml', path.name):
            raise ValueError('Invalid registered version')
    fern.mkdir(parents=True, exist_ok=True)
    if source != target:
        shutil.copyfile(source, target)
    config.write_text(json.dumps({'organization': organization, 'version': version}, indent=2) + '\n')
    if registry != destination:
        for path in previous:
            path.unlink()
        if entries:
            destination.mkdir(parents=True, exist_ok=True)
            for path in entries:
                shutil.copyfile(path, destination / path.name)
    return source, registry


def stage(source, destination, navigation, docs_directory, with_fern=False, fern_working_directory='.', fern_docs_path=''):
    source = source.absolute()
    destination = destination.absolute()
    if navigation:
        # Identity currently publishes from this root navigation file.
        if navigation != 'index.yml':
            raise ValueError('Navigation layout currently requires root index.yml')
        if fern_docs_path and relative_path(source, fern_docs_path) == source / navigation:
            raise ValueError('Fern configuration must be separate from the overlaid navigation')
        run(SCRIPTS / 'stage-fern-site', source, destination)
    else:
        docs = relative_path(source, docs_directory)
        if fern_docs_path and relative_path(source, fern_docs_path).is_relative_to(docs):
            raise ValueError('Fern configuration must be outside the overlaid documentation directory')
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


def prepare(root, version, navigation, docs_directory, docs_paths, frozen, mdx, fern_working_directory='.', fern_docs_path='', organization='nvidia'):
    configure(root, version, fern_working_directory, fern_docs_path, organization)
    fern_directory = relative_path(root, fern_working_directory) / 'fern'
    if frozen:
        if navigation != 'index.yml':
            raise ValueError('Frozen versions require root index.yml')
        for registry in sorted((fern_directory / 'versions').glob('v*.yml')):
            if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', registry.stem):
                raise ValueError('Invalid registered version')
            subprocess.run([
                sys.executable, str(SCRIPTS / 'fern-frozen-content'), registry.stem,
                str(registry.with_name(registry.stem + '-content').resolve()),
                os.environ['GITHUB_SERVER_URL'] + '/' + os.environ['GITHUB_REPOSITORY'],
            ], cwd=root, check=True)
    with tempfile.TemporaryDirectory() as directory:
        staged = Path(directory)
        stage(root, staged, navigation, docs_directory, with_fern=True,
              fern_working_directory=fern_working_directory, fern_docs_path=fern_docs_path)
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
    parser.add_argument('operation', choices=['check', 'stage', 'configure', 'configure-release'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    nav = os.environ.get('NAVIGATION_FILE', '')
    docs = os.environ.get('DOCS_DIRECTORY', 'docs')
    fern_working_directory = os.environ.get('FERN_WORKING_DIRECTORY', '.')
    fern_docs_path = os.environ.get('FERN_DOCS_PATH', '')
    organization = os.environ.get('FERN_ORGANIZATION', 'nvidia')
    if args.operation == 'stage':
        if args.destination is None:
            raise ValueError('Missing staging destination')
        stage(source, args.destination.absolute(), nav, docs, fern_docs_path=fern_docs_path)
    elif args.operation in ('configure', 'configure-release'):
        config, registry = configure(source, os.environ['FERN_CLI_VERSION'], fern_working_directory,
                                     fern_docs_path, organization, legacy_release=args.operation == 'configure-release')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write(f'docs_path={config.relative_to(source).as_posix()}\n')
                output.write(f'versions_path={registry.relative_to(source).as_posix()}\n')
    else:
        prepare(source, os.environ['FERN_CLI_VERSION'], nav, docs,
                os.environ.get('DOCS_PATHS') or docs,
                os.environ.get('FROZEN_VERSIONS') == 'true',
                os.environ.get('CHECK_MDX', 'true') == 'true',
                fern_working_directory, fern_docs_path, organization)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'Fern preparation failed: {error}', file=sys.stderr)
        sys.exit(1)
