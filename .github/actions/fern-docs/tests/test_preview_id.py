# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/preview-id.py'
SPEC = importlib.util.spec_from_file_location('preview_id', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PreviewIdTest(unittest.TestCase):
    def test_preview_identity_is_stable_and_isolated(self):
        cases = [
            ('shared-fern', '123456789', '57'),
            ('shared-fern', '123456789', '58'),
            ('shared-fern', '123456789', '1234'),
            ('shared-fern', '987654321', '57'),
            ('experiment', '123456789', '57'),
        ]
        ids = [MODULE.preview_id('nvidia', *case) for case in cases]
        self.assertEqual(ids[0], 'shared-fern-r123456789-pr-57')
        self.assertEqual(len(set(ids)), len(cases))
        for case, result in zip(cases, ids):
            with self.subTest(case=case):
                self.assertEqual(MODULE.preview_id('nvidia', *case), result)
                self.assertEqual(MODULE.preview_id('NVIDIA', *case), result)
                self.assertTrue(result.endswith(f'-pr-{case[2]}'))

    def test_fern_hostname_boundary_depends_on_organization(self):
        for organization, namespace_length in [('nvidia', 30), ('longer-org', 26)]:
            with self.subTest(organization=organization):
                result = MODULE.preview_id(organization, 'a' * namespace_length, '123456789', '57')
                self.assertEqual(len(f'{organization}-preview-{result}'), 62)
                with self.assertRaisesRegex(ValueError, '62-character limit'):
                    MODULE.preview_id(organization, 'a' * (namespace_length + 1), '123456789', '57')

    def test_invalid_inputs_are_rejected_instead_of_sanitized(self):
        defaults = ['nvidia', 'shared-fern', '123456789', '57']
        cases = [
            (0, ['', None, 'bad.org', '-org', 'org-', 'nvidi\u212a', 'org\n']),
            (1, ['', 'Shared', 'a--b', '-a', 'a-', 'a_b', 'a.b', 'a\n']),
            (2, ['', '0', '0123', '-1', '123\n', '\uff11']),
            (3, ['', '0', '057', '-1', '57\nother=value', '\uff11']),
        ]
        for position, values in cases:
            for value in values:
                with self.subTest(position=position, value=value):
                    arguments = defaults.copy()
                    arguments[position] = value
                    with self.assertRaises(ValueError):
                        MODULE.preview_id(*arguments)

    def test_cli_reads_trusted_config_and_emits_only_valid_id(self):
        env = {key: value for key, value in os.environ.items() if key in ('PATH', 'TMPDIR', 'LANG')}
        env.update(PREVIEW_NAMESPACE='shared-fern', GITHUB_REPOSITORY_ID='123456789', PR_NUMBER='57')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'fern.config.json'
            config.write_text(json.dumps({'organization': 'NVIDIA', 'version': '5.186.0'}))
            command = [sys.executable, str(SCRIPT), str(config)]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'shared-fern-r123456789-pr-57\n')
            self.assertEqual(result.stderr, '')
            for content in ['{}', 'null', '{"organization": null}', '{invalid-json']:
                with self.subTest(content=content):
                    config.write_text(content)
                    result = subprocess.run(command, env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Preview ID validation failed:', result.stderr)
            config.write_text('{"organization": "nvidia"}')
            env.pop('GITHUB_REPOSITORY_ID')
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
