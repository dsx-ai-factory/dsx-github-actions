# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
TAG = ROOT / ".github/actions/git-tag/scripts/tag.sh"
ASKPASS = TAG.with_name("git-askpass.sh")
AUTH = ROOT / ".github/actions/docker-build/scripts/prepare-registry-auth.sh"


class ActionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="action-security-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GITHUB_OUTPUT": str(self.root / "output"),
            "CALLS": str(self.root / "calls"),
        }

    def executable(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + body)
        path.chmod(0o755)
        return path

    def run_script(self, script, *args, env=None):
        return subprocess.run(
            ["bash", str(script), *args], cwd=self.root,
            env={**self.env, **(env or {})},
            text=True, capture_output=True, timeout=15,
        )

    def calls(self):
        path = self.root / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def docker_config(self, value):
        directory = self.root / ".docker"
        directory.mkdir(exist_ok=True)
        (directory / "config.json").write_text(json.dumps(value))

    def test_invalid_credential_helpers_fail_without_execution(self):
        for helper in ("../evil", "../../tmp/evil", "/tmp/evil", "a/b", "a b", "a\nb", "$(id)", 123, {}):
            for field in ("credsStore", "credHelpers"):
                with self.subTest(helper=helper, field=field):
                    self.docker_config({field: {"ghcr.io": helper} if field == "credHelpers" else helper})
                    result = self.run_script(AUTH, "ghcr.io/example/image", str(self.root / "auth.json"))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(json.loads((self.root / "auth.json").read_text()), {"auths": {}})

    def test_valid_helper_and_registry_specific_selection(self):
        self.executable("docker-credential-test-helper", """import json, os, sys
from pathlib import Path
Path(os.environ['CALLS']).write_text(json.dumps([sys.argv[1:], sys.stdin.read()]))
print(json.dumps({'Username': 'fixture', 'Secret': 'not-a-real-secret'}))
""")
        self.docker_config({"credsStore": "../invalid-fallback", "credHelpers": {"ghcr.io": "test-helper"}})
        result = self.run_script(AUTH, "ghcr.io/example/image", str(self.root / "auth.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "calls").read_text()), [["get"], "ghcr.io"])
        self.assertEqual(json.loads((self.root / "auth.json").read_text()), {
            "auths": {"ghcr.io": {"auth": base64.b64encode(b"fixture:not-a-real-secret").decode()}}})
        self.assertNotIn("not-a-real-secret", result.stdout + result.stderr)
        self.assertEqual((self.root / "auth.json").stat().st_mode & 0o777, 0o600)

    def test_no_helper_preserves_only_destination_auth(self):
        self.docker_config({"auths": {"ghcr.io": {"auth": "fixture"}, "other.invalid": {"auth": "other"}}})
        result = self.run_script(AUTH, "ghcr.io/example/image", str(self.root / "auth.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "auth.json").read_text()), {"auths": {"ghcr.io": {"auth": "fixture"}}})

    def test_missing_config_remains_anonymous(self):
        result = self.run_script(AUTH, "ghcr.io/example/image", str(self.root / "auth.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / "auth.json").read_text()), {"auths": {}})

    def mock_git(self):
        self.executable("git", """import json, os, subprocess, sys
with open(os.environ['CALLS'], 'a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[0] == 'config': print('fixture')
if args[0] == 'rev-parse': sys.exit(1)
if 'push' in args and os.environ.get('INPUT_GITHUB_TOKEN'):
    askpass = os.environ['GIT_ASKPASS']
    username = subprocess.check_output([askpass, "Username for 'https://github.com':"], text=True).strip()
    password = subprocess.check_output([askpass, "Password for 'https://x-access-token@github.com':"], text=True).strip()
    assert username == 'x-access-token'
    assert password == os.environ['INPUT_GITHUB_TOKEN']
    assert os.environ['GIT_TERMINAL_PROMPT'] == '0'
if 'push' in args:
    print('push remote: ' + args[args.index('push') + 1])
    sys.exit(int(os.environ.get('PUSH_EXIT', '0')))
""")

    def test_tag_token_is_not_in_argv_or_push_errors(self):
        self.mock_git()
        for code in (0, 7):
            with self.subTest(exit=code):
                result = self.run_script(TAG, env={
                    "INPUT_TAG": "v1.2.3", "INPUT_GITHUB_TOKEN": "fixture-token-only",
                    "GITHUB_REPOSITORY": "example/project", "PUSH_EXIT": str(code),
                })
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertNotIn("fixture-token-only", result.stdout + result.stderr)
                self.assertNotIn("fixture-token-only", (self.root / "calls").read_text())
                push = self.calls()[-1]
                self.assertEqual(push, ["-c", "credential.helper=", "-c", "http.https://github.com/.extraheader=",
                                        "push", "https://github.com/example/project.git", "v1.2.3"])

    def test_tag_without_token_keeps_origin(self):
        self.mock_git()
        result = self.run_script(TAG, env={"INPUT_TAG": "v1.2.3"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], ["push", "origin", "v1.2.3"])

    def test_git_credential_protocol_uses_askpass(self):
        result = subprocess.run(
            [shutil.which("git"), "-c", "credential.helper=", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n", text=True, capture_output=True,
            env={**self.env, "GIT_ASKPASS": str(ASKPASS), "GIT_TERMINAL_PROMPT": "0",
                 "INPUT_GITHUB_TOKEN": "fixture-token-only"}, cwd=self.root, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("username=x-access-token\n", result.stdout)
        self.assertIn("password=fixture-token-only\n", result.stdout)
        self.assertNotIn("fixture-token-only", result.stderr)

    def workflow_steps(self):
        workflow = ROOT / ".github/workflows/promote-image.yml"
        result = subprocess.run(
            ["yq", "-o=json", ".jobs.promote.steps", str(workflow)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        return json.loads(result.stdout)

    def run_step(self, step, env):
        return subprocess.run(
            ["bash", "-c", step["run"]], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True, timeout=15,
        )

    def test_promotion_rejects_newline_output_injection(self):
        prepare = self.workflow_steps()[0]
        defaults = {"INPUT_DIGEST": "", "INPUT_SOURCE": "example/source", "INPUT_SOURCE_TAG": "1",
                    "INPUT_DESTINATION": "example/dest", "INPUT_DESTINATION_TAG": "2"}
        for key in defaults:
            for newline in ("\n", "\r"):
                with self.subTest(key=key, newline=newline):
                    result = self.run_step(prepare, {**defaults, key: f"x{newline}injected=value"})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((self.root / "output").exists())

    def test_promotion_tag_and_digest_refs(self):
        prepare = self.workflow_steps()[0]
        for digest in ("", "sha256:" + "a" * 64):
            with self.subTest(digest=digest):
                (self.root / "output").unlink(missing_ok=True)
                result = self.run_step(prepare, {
                    "INPUT_DIGEST": digest, "INPUT_SOURCE": "example/source", "INPUT_SOURCE_TAG": "1",
                    "INPUT_DESTINATION": "example/dest", "INPUT_DESTINATION_TAG": "2",
                })
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = f"example/source@{digest}" if digest else "example/source:1"
                self.assertEqual((self.root / "output").read_text(),
                                 f"source_ref={expected}\ndestination_ref=example/dest:2\n")

    def test_promotion_treats_shell_syntax_as_data(self):
        self.executable("skopeo", """import json, os, sys
with open(os.environ['CALLS'], 'a') as out: out.write(json.dumps(sys.argv[1:]) + '\\n')
print('{}')
""")
        payload = 'example/image:$(touch injected);`touch injected`"'
        steps = self.workflow_steps()[1:]
        for step in steps:
            with self.subTest(step=step["name"]):
                self.assertNotIn("${{", step["run"])
                self.assertEqual(step["env"]["DESTINATION_REF"], "${{ steps.refs.outputs.destination_ref }}")
                result = self.run_step(step, {
                    "SOURCE_REF": payload, "DESTINATION_REF": payload,
                    "SRC_USER": "fixture", "SRC_PASS": "fixture",
                    "DEST_USER": "fixture", "DEST_PASS": "fixture",
                })
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"docker://{payload}", self.calls()[-1])
                self.assertFalse((self.root / "injected").exists())


if __name__ == "__main__":
    unittest.main()
