# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import importlib.util
import os
from pathlib import Path
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

    def test_fern_subdirectory_keeps_markdown_paths_at_repository_root(self):
        (self.root / 'config').mkdir()
        (self.root / 'fern').rename(self.root / 'config/fern')
        self.write('config/fern/docs.yml', 'products:\n  - display-name: Identity\n    path: ../../index.yml\n')
        self.prepare(FERN_WORKING_DIRECTORY='config')
        self.write('cmd/README.md', '# Invalid { MDX\n')
        self.prepare(False, FERN_WORKING_DIRECTORY='config')

    def test_mdx_outside_docs_is_checked(self):
        self.write('cmd/README.md', '# Invalid { MDX\n')
        self.assertIn('bare {', self.prepare(False).stdout)

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

    def test_preview_page_links_escape_markdown_and_urls(self):
        spec = importlib.util.spec_from_file_location('page_links', SCRIPTS / 'page-links.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.render('https://preview.docs.buildwithfern.com/product/',
                               [{'slug': 'guide/start'}, {'slug': 'x](https://example.invalid)'}, {'slug': None}])
        self.assertIn('[guide/start](https://preview.docs.buildwithfern.com/product/guide/start)', result)
        self.assertIn(r'x\]\(https://example.invalid\)', result)
        self.assertIn('x%5D%28https%3A//example.invalid%29', result)


if __name__ == '__main__':
    unittest.main()
