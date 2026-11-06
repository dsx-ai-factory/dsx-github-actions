# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class FernTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if key in ('PATH', 'TMPDIR', 'LANG')}
        self.env.update({
            'HOME': self.tmp.name,
            'FERN_CLI_VERSION': '5.186.0',
            'NAVIGATION_FILE': 'index.yml',
            'DOCS_PATHS': 'docs\ncmd',
            'GITHUB_SERVER_URL': 'https://github.com',
            'GITHUB_REPOSITORY': 'example/identity',
            'GIT_CONFIG_NOSYSTEM': '1',
        })
        self.write('index.yml', 'navigation:\n  - page: Overview\n    path: docs/overview.md\n  - page: Controller\n    path: cmd/README.md\n')
        self.write('docs/overview.md', '# Original overview\n')
        self.write('cmd/README.md', '# Controller\n')
        self.write('fern/docs.yml', 'products:\n  - display-name: Identity\n    path: ../index.yml\n')
        self.write('fern/fern.config.json', '{"organization":"nvidia","version":"5.186.0"}\n')
        self.git('init', '--initial-branch=master')
        self.git('config', 'user.name', 'Fern test')
        self.git('config', 'user.email', 'fern@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.commit()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def execute(self, command, ok=True, **env):
        result = subprocess.run(command, cwd=self.root, env=self.env | env,
                                capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *args):
        return self.execute(['git', *args])

    def commit(self):
        self.git('add', '.')
        self.git('commit', '--allow-empty', '-m', 'fixture')

    def prepare(self, ok=True, **env):
        return self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'check', '.'], ok, **env)

    def test_identity_paths_pass(self):
        self.prepare()

    def move_config(self):
        self.write('.github/fern-docs.yml', (self.root / 'fern/docs.yml').read_text())
        shutil.rmtree(self.root / 'fern')
        self.env.update(FERN_DOCS_PATH='.github/fern-docs.yml', FERN_CLI_VERSION='5.65.2')

    def test_custom_configuration_generates_fern_files(self):
        self.move_config()
        for organization in ['nvidia', 'another-org']:
            with self.subTest(organization=organization):
                self.prepare(FERN_ORGANIZATION=organization)
                self.assertEqual(json.loads((self.root / 'fern/fern.config.json').read_text()),
                                 {'organization': organization, 'version': '5.65.2'})
                self.assertEqual((self.root / 'fern/docs.yml').read_bytes(),
                                 (self.root / '.github/fern-docs.yml').read_bytes())
        self.write('.github/fern-docs.yml', 'navigation:\n  - page: Overview\n    path: ../docs/overview.md\n')
        self.prepare(NAVIGATION_FILE='', DOCS_PATHS='docs')
        self.assertEqual((self.root / 'fern/docs.yml').read_bytes(),
                         (self.root / '.github/fern-docs.yml').read_bytes())

    def test_only_release_configuration_accepts_the_legacy_path(self):
        command = [sys.executable, str(SCRIPTS / 'prepare.py')]
        env = {'FERN_DOCS_PATH': '.github/fern-docs.yml', 'FERN_CLI_VERSION': '5.65.2'}
        output = self.root / 'github-output'
        original = (self.root / 'fern/docs.yml').read_bytes()
        for operation in ('check', 'configure'):
            self.execute([*command, operation, '.'], False, **env)
        self.execute([*command, 'configure-release', '.'], GITHUB_OUTPUT=str(output), **env)
        self.assertEqual(output.read_text(), 'docs_path=fern/docs.yml\nversions_path=fern/versions\n')
        self.assertEqual((self.root / 'fern/docs.yml').read_bytes(), original)
        self.prepare(FERN_DOCS_PATH='fern/docs.yml', FERN_CLI_VERSION='5.65.2')
        self.assertEqual(json.loads((self.root / 'fern/fern.config.json').read_text())['version'], '5.65.2')
        self.write('.github/fern-docs.yml', 'navigation: []\n')
        self.execute([*command, 'configure-release', '.'], **env)
        self.assertEqual((self.root / 'fern/docs.yml').read_text(), 'navigation: []\n')

    def test_legacy_release_fallback_rejects_invalid_paths(self):
        command = [sys.executable, str(SCRIPTS / 'prepare.py'), 'configure-release', '.']
        for path in ('../missing.yml', '.github/missing.txt', '.git/missing.yml'):
            with self.subTest(path=path):
                self.execute(command, False, FERN_DOCS_PATH=path)
        (self.root / '.github').mkdir()
        (self.root / '.github/fern-docs.yml').symlink_to('missing.yml')
        self.execute(command, False, FERN_DOCS_PATH='.github/fern-docs.yml')
        (self.root / '.github/fern-docs.yml').unlink()
        (self.root / 'fern/docs.yml').unlink()
        self.execute(command, False, FERN_DOCS_PATH='.github/fern-docs.yml')
        (self.root / 'fern/docs.yml').symlink_to('../index.yml')
        self.execute(command, False, FERN_DOCS_PATH='.github/fern-docs.yml')

    def test_release_checkout_requires_the_configured_default_branch_path(self):
        workflow = SCRIPTS.parents[2] / 'workflows/publish-fern-docs.yml'
        step = self.execute([
            'yq', '-r', '.jobs.publish.steps[] | select(.name == "Use the release tree for an unversioned site") | .run',
            str(workflow),
        ]).stdout
        self.git('tag', 'v1.0.0')
        self.move_config()
        self.commit()
        self.execute(['bash', '-euo', 'pipefail', '-c', step], False,
                     TAG_VERSION='v1.0.0', FERN_DOCS_PATH='.github/misspelled.yml')
        self.assertTrue((self.root / '.github/fern-docs.yml').exists())
        self.execute(['bash', '-euo', 'pipefail', '-c', step],
                     TAG_VERSION='v1.0.0', FERN_DOCS_PATH='.github/fern-docs.yml')
        self.assertFalse((self.root / '.github/fern-docs.yml').exists())
        self.assertTrue((self.root / 'fern/docs.yml').is_file())
        config_step = self.execute([
            'yq', '-r', '.jobs.publish.steps[] | select(.id == "config") | .run', str(workflow),
        ]).stdout
        (self.root.parent / '_fern-shared').symlink_to(SCRIPTS.parents[3], target_is_directory=True)
        output = self.root / 'github-output'
        self.execute(['bash', '-euo', 'pipefail', '-c', config_step],
                     REGISTER_VERSIONS='false', GITHUB_OUTPUT=str(output))
        paths = dict(line.split('=', 1) for line in output.read_text().splitlines())
        self.prepare(FERN_DOCS_PATH=paths['docs_path'])

    def test_custom_configuration_requires_safe_paths_before_writing(self):
        self.move_config()
        for path in ['../outside.yml', '/tmp/outside.yml', '.git/config', '.github/missing.yml', 'docs/overview.md', '.github/fern-docs.yml\nextra=value']:
            with self.subTest(path=path):
                self.prepare(False, FERN_DOCS_PATH=path)
                self.assertFalse((self.root / 'fern').exists())
        for link in ['.github/alias.yml', 'alias']:
            target = 'fern-docs.yml' if link.endswith('.yml') else '.github'
            (self.root / link).symlink_to(target)
            path = link if link.endswith('.yml') else link + '/fern-docs.yml'
            self.prepare(False, FERN_DOCS_PATH=path)
            self.assertFalse((self.root / 'fern').exists())

    def test_custom_configuration_rejects_a_symlink_destination(self):
        self.move_config()
        (self.root / 'fern').symlink_to('docs', target_is_directory=True)
        self.prepare(False)
        self.assertFalse((self.root / 'docs/fern.config.json').exists())
        self.assertEqual((self.root / 'docs/overview.md').read_text(), '# Original overview\n')

    def test_custom_configuration_does_not_copy_other_files_from_github(self):
        self.move_config()
        self.write('.github/workflows/unrelated.yml', 'unrelated workflow\n')
        self.write('.github/versions/other.txt', 'unrelated file\n')
        self.prepare()
        self.assertEqual({path.name for path in (self.root / 'fern').iterdir()},
                         {'docs.yml', 'fern.config.json'})

    def test_check_rejects_configuration_overlapping_preview_inputs(self):
        self.move_config()
        self.write('docs/site.yml', 'navigation:\n  - page: Overview\n    path: ../docs/overview.md\n')
        self.assertIn('outside the overlaid documentation directory',
                      self.prepare(False, NAVIGATION_FILE='', DOCS_PATHS='docs',
                                   FERN_DOCS_PATH='docs/site.yml').stderr)
        self.assertIn('separate from the overlaid navigation',
                      self.prepare(False, FERN_DOCS_PATH='index.yml').stderr)

    def test_overlay_keeps_custom_trusted_configuration(self):
        destination = Path(self.tmp.name) / 'trusted'
        (destination / '.github').mkdir(parents=True)
        protected = destination / '.github/fern-docs.yml'
        protected.write_text('trusted configuration')
        self.write('.github/fern-docs.yml', 'untrusted configuration')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(destination)],
                     NAVIGATION_FILE='', FERN_DOCS_PATH='.github/fern-docs.yml')
        self.assertEqual(protected.read_text(), 'trusted configuration')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(destination)], False,
                     NAVIGATION_FILE='', FERN_DOCS_PATH='docs/site.yml')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(destination)], False,
                     FERN_DOCS_PATH='index.yml')

    def test_custom_registry_survives_fresh_workspaces_and_prunes_versions(self):
        self.move_config()
        output = self.root / 'github-output'
        for tag in ['v1.9.0', 'v1.10.0', 'v2.0.0', 'v2.1.0']:
            self.git('tag', tag)
            output.write_text('')
            self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'configure', '.'],
                         GITHUB_OUTPUT=str(output))
            self.assertEqual(output.read_text(), 'docs_path=.github/fern-docs.yml\nversions_path=.github/versions\n')
            self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION=tag,
                         MAX_VERSIONS='3', FERN_VERSIONS_DIRECTORY='.github/versions')
            self.prepare(FROZEN_VERSIONS='true')
            self.assertEqual((self.root / f'fern/versions/{tag}-content/docs/overview.md').read_text(),
                             '# Original overview\n')
            shutil.rmtree(self.root / 'fern')
        before = (self.root / '.github/fern-docs.yml').read_bytes()
        self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION='v2.1.0',
                     MAX_VERSIONS='3', FERN_VERSIONS_DIRECTORY='.github/versions')
        self.assertEqual(before, (self.root / '.github/fern-docs.yml').read_bytes())
        self.prepare(FROZEN_VERSIONS='true')
        self.assertEqual({p.name for p in (self.root / 'fern/versions').glob('v*.yml')},
                         {'v1.10.0.yml', 'v2.0.0.yml', 'v2.1.0.yml'})
        self.assertFalse((self.root / '.github/versions/v1.9.0.yml').exists())
        # A repeated preparation must remove a runtime entry pruned from the source.
        (self.root / '.github/versions/v1.10.0.yml').unlink()
        self.execute(['yq', '-i', '.products[0].versions |= map(select(.slug != "v1.10.0"))', '.github/fern-docs.yml'])
        self.prepare(FROZEN_VERSIONS='true')
        self.assertFalse((self.root / 'fern/versions/v1.10.0.yml').exists())

    def test_fern_subdirectory_keeps_markdown_paths_at_repository_root(self):
        (self.root / 'config with spaces').mkdir()
        (self.root / 'fern').rename(self.root / 'config with spaces/fern')
        self.write('config with spaces/fern/docs.yml', 'products:\n  - display-name: Identity\n    path: ../../index.yml\n')
        self.prepare(FERN_WORKING_DIRECTORY='config with spaces')
        self.write('cmd/README.md', '# Invalid { MDX\n')
        self.prepare(False, FERN_WORKING_DIRECTORY='config with spaces')

    def test_mdx_outside_docs_is_checked(self):
        self.write('cmd/README.md', '# Invalid { MDX\n')
        self.assertIn('bare {', self.prepare(False).stdout)

    def test_custom_docs_directory_is_default_mdx_path(self):
        (self.root / 'docs').rename(self.root / 'content')
        self.write('fern/docs.yml', 'navigation:\n  - page: Overview\n    path: ../content/overview.md\n')
        default_paths = self.execute([
            'yq', '-r', '.inputs.docs-paths.default', str(SCRIPTS.parent / 'action.yml'),
        ]).stdout.strip()
        settings = {'NAVIGATION_FILE': '', 'DOCS_DIRECTORY': 'content', 'DOCS_PATHS': default_paths}
        self.prepare(**settings)

        self.write('docs/unrelated.md', '# Unrelated valid page\n')
        self.write('content/overview.md', '# Invalid { MDX\n')
        self.assertIn('bare {', self.prepare(False, **settings).stdout)
        self.prepare(**(settings | {'DOCS_PATHS': 'docs'}))

    def test_wrong_cli_version_is_rejected(self):
        for version in ['latest', '5.187.0']:
            with self.subTest(version=version):
                self.prepare(False, FERN_CLI_VERSION=version)

    def test_navigation_paths_are_rejected(self):
        for page in ['../outside.md', '/tmp/outside.md', 'docs/missing.md', './fern/hidden.md']:
            with self.subTest(page=page):
                self.write('fern/hidden.md', '# Not a published page\n')
                self.write('index.yml', f'navigation:\n  - page: Invalid\n    path: {page}\n')
                self.prepare(False)

    def test_symlink_page_is_rejected(self):
        (self.root / 'docs/overview.md').unlink()
        (self.root / 'docs/overview.md').symlink_to('../cmd/README.md')
        self.prepare(False)

    def test_unsafe_page_read_is_rejected(self):
        for content in ['<Code src="/proc/self/environ" />', '![image](%252e%252e/private.png)', '<Code src={filename} />']:
            with self.subTest(content=content):
                self.write('cmd/README.md', content)
                self.prepare(False)

    def test_directory_layout_preserves_relative_navigation(self):
        self.write('fern/docs.yml', 'navigation:\n  - page: Overview\n    path: ../docs/overview.md\n  - folder: ../docs/guides\n')
        self.write('docs/guides/start.md', '# Start\n')
        self.prepare(NAVIGATION_FILE='', DOCS_PATHS='docs')

    def test_directory_layout_rejects_paths_outside_docs(self):
        self.write('fern/docs.yml', 'navigation:\n  - page: Private\n    path: ../cmd/README.md\n')
        self.prepare(False, NAVIGATION_FILE='', DOCS_PATHS='docs')

    def test_collector_rejects_symlinks_before_artifact_upload(self):
        (self.root / 'docs/leak.md').symlink_to('/proc/self/environ')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(Path(self.tmp.name) / 'artifact')], False,
                     NAVIGATION_FILE='')

    def test_collector_rejects_trusted_directories(self):
        for directory in ['.', 'fern', '.git']:
            with self.subTest(directory=directory):
                self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                              '--destination', str(Path(self.tmp.name) / 'artifact')], False,
                             NAVIGATION_FILE='', DOCS_DIRECTORY=directory)

    def test_overlay_keeps_trusted_configuration_and_tools(self):
        destination = Path(self.tmp.name) / 'trusted'
        (destination / 'fern').mkdir(parents=True)
        (destination / 'fern/docs.yml').write_text('trusted configuration')
        (destination / 'tools').mkdir()
        (destination / 'tools/script').write_text('trusted tool')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.', '--destination', str(destination)])
        self.assertEqual((destination / 'fern/docs.yml').read_text(), 'trusted configuration')
        self.assertEqual((destination / 'tools/script').read_text(), 'trusted tool')
        self.assertEqual((destination / 'cmd/README.md').read_text(), '# Controller\n')

    def test_directory_overlay_replaces_deleted_and_renamed_pages(self):
        destination = Path(self.tmp.name) / 'trusted'
        for name, content in {
            'docs/deleted.md': '# Deleted\n',
            'docs/old-name.md': '# Renamed\n',
            'docs/overview.md': '# Old overview\n',
            'fern/docs.yml': 'trusted configuration',
            'tools/script': 'trusted tool',
        }.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.write('docs/new-name.md', '# Renamed\n')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(destination)], NAVIGATION_FILE='')
        self.assertEqual({path.name for path in (destination / 'docs').iterdir()},
                         {'overview.md', 'new-name.md'})
        self.assertEqual((destination / 'docs/overview.md').read_text(), '# Original overview\n')
        self.assertEqual((destination / 'docs/new-name.md').read_text(), '# Renamed\n')
        self.assertEqual((destination / 'fern/docs.yml').read_text(), 'trusted configuration')
        self.assertEqual((destination / 'tools/script').read_text(), 'trusted tool')

    def test_directory_overlay_rejects_destination_symlinks_before_deletion(self):
        self.write('content/pages/start.md', '# Start\n')
        cases = [
            ('docs', 'docs', 'tools'),
            ('content/pages', 'content', 'tools'),
            ('docs', 'docs', 'missing'),
            ('content/pages', 'content', 'missing'),
        ]
        for index, (directory, link, link_target) in enumerate(cases):
            with self.subTest(directory=directory, target=link_target):
                destination = Path(self.tmp.name) / f'trusted-{index}'
                (destination / 'tools/pages').mkdir(parents=True)
                protected = destination / 'tools/pages/keep.md'
                protected.write_text('trusted tool')
                (destination / link).symlink_to(link_target, target_is_directory=True)
                self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                              '--destination', str(destination)], False,
                             NAVIGATION_FILE='', DOCS_DIRECTORY=directory)
                self.assertEqual(protected.read_text(), 'trusted tool')
                self.assertTrue((destination / link).is_symlink())
                self.assertFalse((destination / 'missing').exists())

    def test_directory_overlay_rejects_a_symlink_checkout(self):
        destination = Path(self.tmp.name) / 'trusted'
        (destination / 'docs').mkdir(parents=True)
        protected = destination / 'docs/keep.md'
        protected.write_text('keep')
        alias = Path(self.tmp.name) / 'alias'
        alias.symlink_to(destination, target_is_directory=True)
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(alias)], False, NAVIGATION_FILE='')
        self.assertEqual(protected.read_text(), 'keep')

    def test_directory_overlay_rejects_overlap_without_removing_source(self):
        for destination in [self.root, self.root / 'docs/nested']:
            with self.subTest(destination=destination):
                self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                              '--destination', str(destination)], False, NAVIGATION_FILE='')
                self.assertEqual((self.root / 'docs/overview.md').read_text(), '# Original overview\n')
        self.assertFalse((self.root / 'docs/nested').exists())
        # The destination docs directory must not contain the source checkout.
        destination = Path(self.tmp.name) / 'ancestor'
        source = destination / 'docs/repo'
        (source / 'docs').mkdir(parents=True)
        protected = source / 'docs/keep.md'
        protected.write_text('keep')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', str(source),
                      '--destination', str(destination)], False, NAVIGATION_FILE='')
        self.assertEqual(protected.read_text(), 'keep')

    def test_directory_overlay_checks_source_before_removing_destination(self):
        destination = Path(self.tmp.name) / 'trusted'
        (destination / 'docs').mkdir(parents=True)
        protected = destination / 'docs/keep.md'
        protected.write_text('keep')
        (self.root / 'docs/link.md').symlink_to('../cmd/README.md')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(destination)], False, NAVIGATION_FILE='')
        self.assertEqual(protected.read_text(), 'keep')

    def test_directory_overlay_does_not_hide_a_deleted_navigation_page(self):
        destination = Path(self.tmp.name) / 'trusted'
        (destination / 'fern').mkdir(parents=True)
        (destination / 'docs').mkdir()
        (destination / 'docs/deleted.md').write_text('# Deleted\n')
        (destination / 'fern/docs.yml').write_text('navigation:\n  - page: Deleted\n    path: ../docs/deleted.md\n')
        (destination / 'fern/fern.config.json').write_text('{"organization":"nvidia","version":"5.186.0"}\n')
        self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'stage', '.',
                      '--destination', str(destination)], NAVIGATION_FILE='')
        result = self.execute([sys.executable, str(SCRIPTS / 'prepare.py'), 'check', str(destination)], False,
                              NAVIGATION_FILE='', DOCS_PATHS='docs')
        self.assertIn("path '../docs/deleted.md' leaves the docs directory", result.stdout)
        self.assertFalse((destination / 'docs/deleted.md').exists())

    def test_frozen_content_uses_tag_and_pins_non_page_links(self):
        self.write('sample.yaml', 'kind: Example\n')
        self.write('docs/overview.md', '# Release {example}\n[Sample](../sample.yaml)\n[Controller](../cmd/README.md)\n```\n{code}\n```\n')
        self.commit()
        self.git('tag', 'v1.2.0')
        self.write('docs/overview.md', '# Changed after release\n')
        self.execute([sys.executable, str(SCRIPTS / 'fern-frozen-content'), 'v1.2.0', 'fern/versions/v1.2.0-content', 'https://github.com/example/identity'])
        frozen = (self.root / 'fern/versions/v1.2.0-content/docs/overview.md').read_text()
        self.assertIn('Release \\{example\\}', frozen)
        self.assertNotIn('Changed after release', frozen)
        self.assertIn('https://github.com/example/identity/blob/v1.2.0/sample.yaml', frozen)
        self.assertIn('[Controller](../cmd/README.md)', frozen)
        self.assertIn('```\n{code}\n```', frozen)

    def test_frozen_content_rejects_missing_tag_and_navigation(self):
        command = [sys.executable, str(SCRIPTS / 'fern-frozen-content'), 'v1.0.0', 'frozen', 'https://github.com/example/identity']
        self.execute(command, False)
        (self.root / 'index.yml').unlink()
        self.commit()
        self.git('tag', 'v1.0.0')
        self.execute(command, False)

    def test_registration_orders_prunes_and_is_idempotent(self):
        for tag in ['v1.9.0', 'v1.10.0', 'v2.0.0', 'v2.1.0']:
            self.git('tag', tag)
            self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION=tag, MAX_VERSIONS='3')
        versions = self.execute(['yq', '-o=json', '.products[0].versions', 'fern/docs.yml']).stdout
        self.assertEqual([item['slug'] for item in json.loads(versions)], ['v2.1.0', 'v2.0.0', 'v1.10.0'])
        self.assertFalse((self.root / 'fern/versions/v1.9.0.yml').exists())
        self.assertIn('v2.1.0-content/cmd/README.md', (self.root / 'fern/versions/v2.1.0.yml').read_text())
        before = (self.root / 'fern/docs.yml').read_bytes()
        self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION='v2.1.0', MAX_VERSIONS='3')
        self.assertEqual(before, (self.root / 'fern/docs.yml').read_bytes())
        self.prepare(FROZEN_VERSIONS='true')
        self.assertEqual((self.root / 'fern/versions/v2.1.0-content/docs/overview.md').read_text(), '# Original overview\n')

    def test_version_order_uses_semver_precedence_and_ignores_build_metadata(self):
        tags = ['v1.2.3-rc.1', 'v1.2.3', 'v1.2.3+build.2', 'v1.2.3+build.10',
                'v1.2.3-alpha.beta', 'v1.2.3-beta.11', 'v1.2.3-alpha.1',
                'v1.2.3-beta', 'v1.2.3-alpha', 'v1.2.3-beta.2',
                'v2.0.0', 'v1.10.0', 'v1.3.0-rc.1']
        result = subprocess.run([sys.executable, str(SCRIPTS / 'sort-versions.py')],
                                input='\n'.join(tags) + '\n', capture_output=True, text=True,
                                cwd=self.root, env=self.env, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            'v2.0.0', 'v1.10.0', 'v1.3.0-rc.1', 'v1.2.3',
            'v1.2.3+build.2', 'v1.2.3+build.10', 'v1.2.3-rc.1',
            'v1.2.3-beta.11', 'v1.2.3-beta.2', 'v1.2.3-beta',
            'v1.2.3-alpha.beta', 'v1.2.3-alpha.1', 'v1.2.3-alpha',
        ])

    def test_registration_orders_stable_before_same_core_prereleases(self):
        for tag in ['v1.2.4-rc.1', 'v1.2.4']:
            self.git('tag', tag)
            self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION=tag, MAX_VERSIONS='3')
        versions = json.loads(self.execute(['yq', '-o=json', '.products[0].versions', 'fern/docs.yml']).stdout)
        self.assertEqual([version['slug'] for version in versions], ['v1.2.4', 'v1.2.4-rc.1'])

    def test_registration_retains_stable_at_pruning_boundary(self):
        for tag in ['v1.2.4-rc.1', 'v1.2.4', 'v1.2.3']:
            self.git('tag', tag)
            self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION=tag, MAX_VERSIONS='2')
        versions = json.loads(self.execute(['yq', '-o=json', '.products[0].versions', 'fern/docs.yml']).stdout)
        self.assertEqual([version['slug'] for version in versions], ['v1.2.4', 'v1.2.3'])
        self.assertTrue((self.root / 'fern/versions/v1.2.4.yml').is_file())
        self.assertFalse((self.root / 'fern/versions/v1.2.4-rc.1.yml').exists())

    def test_publish_event_guard(self):
        self.git('tag', 'v1.2.3')
        self.git('update-ref', 'refs/remotes/origin/master', 'HEAD')
        for event, ref, allowed in [
            ('push', 'refs/heads/test/fern', False),
            ('push', 'refs/heads/pull-request/51', False),
            ('workflow_dispatch', 'refs/heads/test/fern', False),
            ('workflow_dispatch', 'refs/heads/master', True),
            ('release', 'refs/tags/v1.2.3', True),
            ('push', 'refs/tags/v1.2.3', True),
            ('workflow_run', 'refs/heads/master', False),
        ]:
            with self.subTest(event=event, ref=ref):
                self.execute(['bash', str(SCRIPTS / 'require-publish-event.sh')], allowed,
                             GITHUB_EVENT_NAME=event, GITHUB_REF=ref, DEFAULT_BRANCH='master', TAG_VERSION='v1.2.3')

    def test_publish_rejects_a_tag_from_an_unmerged_branch(self):
        self.git('update-ref', 'refs/remotes/origin/master', 'HEAD')
        self.git('checkout', '-b', 'test/fern')
        self.write('docs/overview.md', '# Unmerged change\n')
        self.commit()
        self.git('tag', 'v1.2.4')
        self.execute(['bash', str(SCRIPTS / 'require-publish-event.sh')], False,
                     GITHUB_EVENT_NAME='release', GITHUB_REF='refs/tags/v1.2.4',
                     DEFAULT_BRANCH='master', TAG_VERSION='v1.2.4')

    def test_release_resolution_requires_explicit_rc_opt_in(self):
        workflow = SCRIPTS.parents[2] / 'workflows/publish-fern-docs.yml'
        step = self.execute([
            'yq', '-r', '.jobs.publish.steps[] | select(.id == "resolve") | .run', str(workflow),
        ]).stdout
        output = self.root / 'github-output'
        for tag in ['v1.2.3', 'v1.2.4-rc.1', 'v1.2.4-beta.1']:
            self.git('tag', tag)
        for tag, prerelease, rc, register, ok, publish in [
            ('v1.2.3', 'false', 'false', 'true', True, True),
            ('v1.2.3', 'false', 'true', 'false', True, True),
            ('v1.2.4-rc.1', 'true', 'false', 'false', True, False),
            ('v1.2.4-rc.1', 'true', 'true', 'false', True, True),
            ('v1.2.4-rc.1', 'false', 'true', 'false', True, True),
            ('v1.2.4-rc.1', 'true', 'true', 'true', False, False),
            ('v1.2.4-beta.1', 'true', 'true', 'false', True, False),
            ('v1.2.3', 'true', 'true', 'false', True, False),
            ('v9.9.9-rc.1', 'true', 'true', 'false', False, False),
            ('invalid', 'false', 'true', 'false', False, False),
        ]:
            with self.subTest(tag=tag, prerelease=prerelease, rc=rc, register=register):
                output.write_text('')
                self.execute(['bash', '-euo', 'pipefail', '-c', step], ok,
                             GITHUB_EVENT_NAME='release', RELEASE_TAG=tag, INPUT_TAG='',
                             RELEASE_PRERELEASE=prerelease, PUBLISH_RC=rc,
                             REGISTER_VERSIONS=register, MAX_VERSIONS='3', GITHUB_OUTPUT=str(output))
                if ok:
                    self.assertIn('publish=' + str(publish).lower(), output.read_text())
                    if publish:
                        self.assertIn('tag=' + tag, output.read_text())

    def test_manual_resolution_accepts_version_tags_with_and_without_registration(self):
        workflow = SCRIPTS.parents[2] / 'workflows/publish-fern-docs.yml'
        step = self.execute([
            'yq', '-r', '.jobs.publish.steps[] | select(.id == "resolve") | .run', str(workflow),
        ]).stdout
        tags = ['v1.2.3', 'v1.2.4-rc.1', 'v1.2.4-beta.2', 'v1.2.4+build.1',
                'v1.2.4-beta.2+build.1', 'preview', 'docs/preview', 'v1.2.4/rc.1']
        for tag in tags:
            self.git('tag', tag)
        output = self.root / 'github-output'
        for register in ['true', 'false']:
            for tag in [*tags, 'v9.9.9-rc.1', ' v1.2.3']:
                allowed = tag in tags[:5]
                with self.subTest(register=register, tag=tag):
                    output.write_text('')
                    self.execute(['bash', '-euo', 'pipefail', '-c', step], allowed,
                                 GITHUB_EVENT_NAME='workflow_dispatch', INPUT_TAG=tag,
                                 RELEASE_TAG='', RELEASE_PRERELEASE='', PUBLISH_RC='false',
                                 REGISTER_VERSIONS=register, MAX_VERSIONS='3',
                                 GITHUB_OUTPUT=str(output))
                    self.assertEqual(output.read_text(),
                                     f'tag={tag}\npublish=true\n' if allowed else '')

    def test_manual_registered_version_requires_navigation(self):
        (self.root / 'index.yml').unlink()
        self.commit()
        self.git('tag', 'v1.2.4-rc.1')
        workflow = SCRIPTS.parents[2] / 'workflows/publish-fern-docs.yml'
        step = self.execute([
            'yq', '-r', '.jobs.publish.steps[] | select(.id == "resolve") | .run', str(workflow),
        ]).stdout
        output = self.root / 'github-output'
        self.execute(['bash', '-euo', 'pipefail', '-c', step], False,
                     GITHUB_EVENT_NAME='workflow_dispatch', INPUT_TAG='v1.2.4-rc.1',
                     RELEASE_TAG='', RELEASE_PRERELEASE='', PUBLISH_RC='false',
                     REGISTER_VERSIONS='true', MAX_VERSIONS='3', GITHUB_OUTPUT=str(output))
        self.assertFalse(output.exists())

    def test_manual_publication_requires_trusted_version_ancestry(self):
        self.git('tag', 'v1.2.3-rc.1')
        self.git('update-ref', 'refs/remotes/origin/master', 'HEAD')
        self.git('checkout', '-b', 'release/1.2.4')
        self.write('docs/overview.md', '# Release branch fixes\n')
        self.commit()
        self.git('update-ref', 'refs/remotes/origin/release/1.2.4', 'HEAD')
        for tag in ['v1.2.4-rc.1', 'v1.2.4-beta.2+build.1']:
            self.git('tag', tag)
        self.git('checkout', '-b', 'test/unmerged')
        self.write('docs/overview.md', '# Unmerged fixes\n')
        self.commit()
        self.git('tag', 'v1.2.4-rc.2')
        for tag, allowed in [('v1.2.3-rc.1', True), ('v1.2.4-rc.1', True),
                             ('v1.2.4-beta.2+build.1', True), ('v1.2.4-rc.2', False),
                             ('preview', False), ('docs/preview', False)]:
            for ref in ['refs/heads/master', 'refs/heads/test/unmerged']:
                for register in ['true', 'false']:
                    with self.subTest(tag=tag, ref=ref, register=register):
                        self.execute(['bash', str(SCRIPTS / 'require-publish-event.sh')],
                                     allowed and ref == 'refs/heads/master',
                                     GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF=ref,
                                     DEFAULT_BRANCH='master', INPUT_TAG=tag, TAG_VERSION=tag,
                                     PUBLISH_RC='false', REGISTER_VERSIONS=register)

    def test_prerelease_registry_survives_preparation_and_retains_selected_version(self):
        self.move_config()
        for tag in ['v2.0.0', 'v3.0.0', 'v4.0.0', 'v1.2.4-rc.1', 'v1.2.4-beta.2+build.1']:
            self.git('tag', tag)
            self.prepare()
            self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION=tag,
                         MAX_VERSIONS='3', FERN_VERSIONS_DIRECTORY='.github/versions')
            self.prepare(FROZEN_VERSIONS='true')
            self.assertEqual((self.root / f'fern/versions/{tag}-content/docs/overview.md').read_text(),
                             '# Original overview\n')
        versions = json.loads(self.execute(['yq', '-o=json', '.products[0].versions', '.github/fern-docs.yml']).stdout)
        self.assertEqual([v['slug'] for v in versions], ['v4.0.0', 'v3.0.0', 'v1.2.4-beta.2+build.1'])
        self.assertFalse((self.root / '.github/versions/v1.2.4-rc.1.yml').exists())
        self.execute(['bash', str(SCRIPTS / 'register-version.sh')], TAG_VERSION='docs/preview',
                     MAX_VERSIONS='3', FERN_VERSIONS_DIRECTORY='.github/versions', ok=False)

    def test_release_resolution_rejects_conflicting_input_tag(self):
        workflow = SCRIPTS.parents[2] / 'workflows/publish-fern-docs.yml'
        step = self.execute([
            'yq', '-r', '.jobs.publish.steps[] | select(.id == "resolve") | .run', str(workflow),
        ]).stdout
        output = self.root / 'github-output'
        self.git('tag', 'v1.2.4-rc.1')
        self.git('tag', 'v1.2.4-rc.2')
        for event in ['release', 'push']:
            for requested, ok in [('', True), ('v1.2.4-rc.1', True), ('v1.2.4-rc.2', False)]:
                with self.subTest(event=event, requested=requested):
                    output.write_text('')
                    self.execute(['bash', '-euo', 'pipefail', '-c', step], ok,
                                 GITHUB_EVENT_NAME=event, GITHUB_REF='refs/tags/v1.2.4-rc.1',
                                 RELEASE_TAG='v1.2.4-rc.1', INPUT_TAG=requested,
                                 RELEASE_PRERELEASE='true', PUBLISH_RC='true',
                                 REGISTER_VERSIONS='false', MAX_VERSIONS='3', GITHUB_OUTPUT=str(output))
                    if ok:
                        self.assertIn('tag=v1.2.4-rc.1', output.read_text())
                    else:
                        self.assertEqual(output.read_text(), '')

    def test_rc_publish_requires_matching_release_branch(self):
        self.git('update-ref', 'refs/remotes/origin/master', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/release/9.9.9', 'HEAD')
        self.git('checkout', '-b', 'release/1.2.4')
        self.write('docs/overview.md', '# RC fix outside master\n')
        self.commit()
        self.git('tag', 'v1.2.4-rc.1')
        self.git('tag', 'v9.9.9-rc.1')
        self.git('tag', 'v8.8.8-rc.1')
        self.git('update-ref', 'refs/remotes/origin/release/1.2.4', 'HEAD')
        for event, ref, tag, rc, register, allowed in [
            ('push', 'refs/tags/v1.2.4-rc.1', 'v1.2.4-rc.1', 'true', 'false', True),
            ('release', 'refs/tags/v1.2.4-rc.1', 'v1.2.4-rc.1', 'true', 'false', True),
            ('workflow_dispatch', 'refs/heads/master', 'v1.2.4-rc.1', 'true', 'false', True),
            ('workflow_dispatch', 'refs/heads/release/1.2.4', 'v1.2.4-rc.1', 'true', 'false', False),
            ('push', 'refs/tags/v1.2.4-rc.1', 'v1.2.4-rc.1', 'false', 'false', False),
            ('push', 'refs/tags/v1.2.4-rc.1', 'v1.2.4-rc.1', 'true', 'true', False),
            ('push', 'refs/tags/v9.9.9-rc.1', 'v9.9.9-rc.1', 'true', 'false', False),
            ('push', 'refs/tags/v8.8.8-rc.1', 'v8.8.8-rc.1', 'true', 'false', False),
            ('push', 'refs/tags/v1.2.4-rc.1', 'v9.9.9-rc.1', 'true', 'false', False),
            ('push', 'refs/heads/pull-request/51', 'v1.2.4-rc.1', 'true', 'false', False),
        ]:
            with self.subTest(event=event, ref=ref, tag=tag, rc=rc, register=register):
                self.execute(['bash', str(SCRIPTS / 'require-publish-event.sh')], allowed,
                             GITHUB_EVENT_NAME=event, GITHUB_REF=ref, DEFAULT_BRANCH='master',
                             TAG_VERSION=tag, PUBLISH_RC=rc, REGISTER_VERSIONS=register)

    def test_promoted_stable_requires_matching_rc_commit_and_release_branch(self):
        self.git('update-ref', 'refs/remotes/origin/master', 'HEAD')
        self.git('checkout', '-b', 'release/1.2.4')
        self.write('docs/overview.md', '# Release-only fix\n')
        self.commit()
        self.git('update-ref', 'refs/remotes/origin/release/1.2.4', 'HEAD')
        self.git('tag', '-a', 'v1.2.4-rc.1', '-m', 'RC')
        self.git('tag', '-a', 'v1.2.4', '-m', 'Stable')
        self.git('tag', 'v9.9.9-rc.1')
        self.git('tag', 'v9.9.9')
        env = {'DEFAULT_BRANCH': 'master', 'PUBLISH_RC': 'true', 'REGISTER_VERSIONS': 'false'}
        command = ['bash', str(SCRIPTS / 'require-publish-event.sh')]
        for event, ref, tag, rc, register, allowed in [
            ('push', 'refs/tags/v1.2.4', 'v1.2.4', 'true', 'false', True),
            ('release', 'refs/tags/v1.2.4', 'v1.2.4', 'true', 'false', True),
            ('workflow_dispatch', 'refs/heads/master', 'v1.2.4', 'true', 'false', True),
            ('push', 'refs/tags/v1.2.4', 'v1.2.4', 'false', 'false', False),
            ('push', 'refs/tags/v1.2.4', 'v1.2.4', 'true', 'true', False),
            ('push', 'refs/tags/v9.9.9', 'v9.9.9', 'true', 'false', False),
            ('workflow_dispatch', 'refs/heads/release/1.2.4', 'v1.2.4', 'true', 'false', False),
        ]:
            with self.subTest(event=event, ref=ref, tag=tag, rc=rc, register=register):
                self.execute(command, allowed, **(env | {
                    'GITHUB_EVENT_NAME': event, 'GITHUB_REF': ref, 'TAG_VERSION': tag,
                    'PUBLISH_RC': rc, 'REGISTER_VERSIONS': register}))
        env.update(GITHUB_EVENT_NAME='push', GITHUB_REF='refs/tags/v1.2.4', TAG_VERSION='v1.2.4')
        self.git('tag', '-d', 'v1.2.4-rc.1')
        self.git('tag', 'v1.2.4-rc.not-a-number')
        self.execute(command, False, **env)
        self.git('tag', 'v1.2.4-rc.2')
        self.execute(command, **env)
        self.write('docs/overview.md', '# Not the RC commit\n')
        self.commit()
        self.git('update-ref', 'refs/remotes/origin/release/1.2.4', 'HEAD')
        self.git('tag', '-f', 'v1.2.4')
        self.execute(command, False, **env)
        self.git('tag', '-f', 'v1.2.4-rc.2')
        self.git('update-ref', 'refs/remotes/origin/release/1.2.4', 'HEAD~1')
        self.execute(command, False, **env)

    def test_preview_comment_rechecks_head_and_adopts_only_bot_comments(self):
        workflow = SCRIPTS.parents[2] / 'workflows/fern-docs-preview-comment.yml'
        step = self.execute([
            'yq', '-r', '.jobs.preview.steps[] | select(.name == "Post or update PR comment") | .run',
            str(workflow),
        ]).stdout
        self.write('bin/gh', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
route = next(arg for arg in args if arg.startswith('repos/'))
if '--paginate' in args:
    if '--slurp' in args and '--jq' in args:
        sys.exit('gh does not support --slurp with --jq')
    print(Path('comments.json').read_text())
elif '/pulls/' in route:
    if os.environ['CURRENT_HEAD'] == 'api-error':
        sys.exit(1)
    print(os.environ['CURRENT_HEAD'])
else:
    Path('write.json').write_text(json.dumps(args))
''')
        (self.root / 'bin/gh').chmod(0o755)
        shared = {'id': 21, 'user': {'login': 'github-actions[bot]'}, 'body': '<!-- shared-fern-preview -->'}
        legacy = {'id': 20, 'user': {'login': 'github-actions[bot]'}, 'body': '<!-- preview-docs -->'}
        user = legacy | {'id': 19, 'user': {'login': 'contributor'}}
        expected_head = 'a' * 40
        for name, pages, head, target, ok in [
            ('new', [[]], expected_head, 'issues/51/comments', True),
            ('legacy', [[user], [legacy]], expected_head, 'issues/comments/20', True),
            ('shared first', [[legacy], [shared]], expected_head, 'issues/comments/21', True),
            ('other author', [[user]], expected_head, 'issues/51/comments', True),
            ('advanced', [[shared]], 'b' * 40, None, True),
            ('missing head', [[shared]], '', None, True),
            ('API failure', [[shared]], 'api-error', None, False),
        ]:
            with self.subTest(name=name):
                self.write('comments.json', json.dumps(pages))
                written = self.root / 'write.json'
                written.unlink(missing_ok=True)
                self.execute(['bash', '-euo', 'pipefail', '-c', step], ok,
                             PATH=str(self.root / 'bin') + os.pathsep + self.env['PATH'],
                             REPO='example/identity', PR_NUMBER='51', RUN_HEAD_SHA=expected_head,
                             CURRENT_HEAD=head, PREVIEW_URL='https://example.buildwithfern.com', PAGE_LINKS='')
                if target is None:
                    self.assertFalse(written.exists())
                else:
                    args = json.loads(written.read_text())
                    self.assertIn('repos/example/identity/' + target, args)
                    body = next(arg for arg in args if arg.startswith('body='))
                    self.assertIn('<!-- shared-fern-preview -->', body)
                    self.assertNotIn('<!-- preview-docs -->', body)

    def test_preview_page_links_escape_markdown_and_urls(self):
        spec = importlib.util.spec_from_file_location('page_links', SCRIPTS / 'page-links.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.render('https://preview.docs.buildwithfern.com/product/',
                               [{'slug': 'guide/start'}, {'slug': 'x](https://example.invalid)'}, {'slug': None}])
        self.assertIn('[guide/start](https://preview.docs.buildwithfern.com/product/guide/start)', result)
        self.assertIn(r'x\]\(https://example.invalid\)', result)
        self.assertIn('x%5D%28https%3A//example.invalid%29', result)

    def test_preview_page_links_are_optional(self):
        workflow = SCRIPTS.parents[2] / 'workflows/fern-docs-preview-comment.yml'
        step = self.execute([
            'yq', '-r', '.jobs.preview.steps[] | select(.id == "page-links") | .run', str(workflow),
        ]).stdout
        (self.root / '_fern-shared').symlink_to(SCRIPTS.parents[3], target_is_directory=True)
        self.write('preview/preview-metadata/changed_md_files', 'docs/overview.md\n')
        output = self.root / 'github-output'
        response = self.root / 'response.json'
        curl_args = self.root / 'curl-args'
        mock_curl = 'curl() { printf "%s\\n" "$@" > "$CURL_ARGS"; cat "$CURL_RESPONSE"; return "$CURL_EXIT"; }\n'
        for name, body, status, has_links in [
            ('valid', '{"mappings":[{"slug":"guide/start"}]}', 0, True),
            ('no mappings', '{}', 0, False),
            ('invalid JSON', '<html>Proxy response</html>', 0, False),
            ('array', '[]', 0, False),
            ('null mappings', '{"mappings":null}', 0, False),
            ('transport failure', '', 7, False),
            ('timeout', '', 28, False),
        ]:
            with self.subTest(response=name):
                response.write_text(body)
                output.write_text('')
                self.execute(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', mock_curl + step],
                             PREVIEW_URL='https://example.buildwithfern.com', GITHUB_OUTPUT=str(output),
                             CURL_RESPONSE=str(response), CURL_EXIT=str(status), CURL_ARGS=str(curl_args))
                if has_links:
                    self.assertIn('[guide/start](https://example.buildwithfern.com/guide/start)', output.read_text())
                else:
                    self.assertEqual(output.read_text(), 'page_links=\n')
                arguments = curl_args.read_text().splitlines()
                self.assertIn('--max-time', arguments)
                timeout = float(arguments[arguments.index('--max-time') + 1])
                self.assertGreater(timeout, 0)
                self.assertLess(timeout, 15 * 60)


if __name__ == '__main__':
    unittest.main()
