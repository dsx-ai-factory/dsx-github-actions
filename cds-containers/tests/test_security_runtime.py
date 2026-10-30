# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline policy and runtime-guard regression tests (no Docker required)."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest


CONTAINERS = Path(__file__).resolve().parents[1]
SMOKE = CONTAINERS / "tests/security-runtime.sh"


class ContainerSecurityPolicyTests(unittest.TestCase):
    def test_go_bases_are_patched_and_digest_pinned(self):
        for directory, tag, digest in (
            ("go-dev-1.24-alpine", "1.26.10-alpine",
             "27f2d3b83cfc5803c8078f8e730af0f7c431bf067afaec8f60a36fb763187cba"),
            ("go-dev-1.24-debian", "1.26.10",
             "672c944cfd07599b5f1bb179869270cdc94c6254e41db4d431259693cb286b82"),
        ):
            with self.subTest(image=directory):
                dockerfile = (CONTAINERS / directory / "Dockerfile").read_text()
                self.assertIn(f"FROM golang:{tag}@sha256:{digest}\n", dockerfile)
                self.assertIn('GOTOOLCHAIN=local go env GOVERSION', dockerfile)
                for tool in ("air-verse/air", "go-delve/delve", "golangci-lint",
                             "x/tools/cmd/goimports", "swaggo/swag"):
                    self.assertIn(tool, dockerfile)
                self.assertIn("ENV CGO_ENABLED=1", dockerfile)
                self.assertIn("delve/cmd/dlv@v1.27.0", dockerfile)
                self.assertIn("go install -mod=readonly", dockerfile)
                self.assertIn("COPY go-tools/golangci-lint/", dockerfile)

    def test_linter_keeps_v1_with_go_export_data_compatibility(self):
        module = (CONTAINERS / "go-tools/golangci-lint/go.mod").read_text()
        self.assertIn("github.com/golangci/golangci-lint v1.64.8", module)
        self.assertIn("golang.org/x/tools v0.45.0", module)
        self.assertIn("honnef.co/go/tools v0.6.1", module)
        self.assertNotIn("golangci-lint/v2", module)
        smoke = SMOKE.read_text()
        self.assertIn("--no-config --disable-all --enable=govet", smoke)
        self.assertIn("--config .golangci.yml", smoke)

    def test_distro_trust_store_floors(self):
        for directory, minimum in (
            ("go-dev-1.24-alpine", "20261020-r0"),
            ("go-dev-1.24-debian", "20250419+deb13u1"),
            ("tools", "20260816ubuntu0.22.04.1"),
        ):
            with self.subTest(image=directory):
                dockerfile = (CONTAINERS / directory / "Dockerfile").read_text()
                self.assertIn(minimum, dockerfile)
                self.assertIn("update-ca-certificates", dockerfile)
                self.assertIn(minimum, SMOKE.read_text())

    def test_tools_pins_patched_node_without_replacing_other_tools(self):
        dockerfile = (CONTAINERS / "tools/Dockerfile").read_text()
        self.assertIn('ARG NODEJS_VERSION="24.22.1-1nodesource1"', dockerfile)
        self.assertIn('"nodejs=${NODEJS_VERSION}"', dockerfile)
        self.assertIn('ge "24.22.1"', dockerfile)
        self.assertIn("signed-by=/usr/share/keyrings/nodesource.gpg", dockerfile)
        for tool in ("BAZEL6_VERSION", "BAZEL8_VERSION", "KUBECTL_VERSION",
                     "KUBELOGIN_VERSION", "NGCCLI_VERSION", "HELM_VERSION",
                     "TERRAFORM_VERSION", "TERRAGRUNT_VERSION", "REGCTL_VERSION"):
            self.assertIn(f"ARG {tool}=", dockerfile)

    def test_alpine_uses_the_installed_package_version_constraint(self):
        script = SMOKE.read_text()
        self.assertIn("apk info --installed --verbose 'ca-certificates>=20261020-r0'", script)
        self.assertNotIn("apk version -t", script)

    def test_immutable_release_version(self):
        self.assertEqual((CONTAINERS / "VERSION").read_text(), "0.2.1\n")

    def test_smoke_script_syntax_and_usage(self):
        subprocess.run(["sh", "-n", str(SMOKE)], check=True)
        for args in ([], ["unknown"]):
            result = subprocess.run(["sh", str(SMOKE), *args], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"Usage:", result.stderr)

    def test_go_guard_rejects_unpatched_or_unexpected_toolchains(self):
        match = re.search(
            r"export GOTOOLCHAIN=local\n(.*?)\n        check_ca_certificates go",
            SMOKE.read_text(), re.S)
        self.assertIsNotNone(match)
        for version in ("go1.24.3", "go1.26.9", "go1.27.3", "go1.26.10"):
            with self.subTest(version=version):
                script = (
                    'set -eu\ngo() { test "$GOTOOLCHAIN" = local; '
                    f'printf "%s\\n" "{version}"; }}\n'
                    'export GOTOOLCHAIN=local\n' + match.group(1)
                )
                result = subprocess.run(["sh", "-c", script], capture_output=True)
                self.assertEqual(result.returncode == 0, version == "go1.26.10")

    @unittest.skipUnless(shutil.which("node"), "Node is required for runtime-guard tests")
    def test_node_guard_rejects_unpatched_or_wrong_major_versions(self):
        # Exercise the exact embedded guard, including its real-runtime entry point.
        match = re.search(r"node <<'NODE'\n(.*?)\nNODE", SMOKE.read_text(), re.S)
        self.assertIsNotNone(match)
        guard = match.group(1)
        environment = os.environ.copy()
        environment.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
        environment.pop("NODE_OPTIONS", None)
        actual = subprocess.run(["node", "-p", "process.versions.node"],
                                env=environment, capture_output=True, check=True,
                                text=True).stdout.strip()
        major, minor, patch = map(int, actual.split("."))
        result = subprocess.run(["node", "-e", guard], env=environment,
                                capture_output=True)
        self.assertEqual(result.returncode == 0,
                         major == 24 and (minor, patch) >= (22, 1), result.stderr)
        for version, expected in (
            ("24.22.0", False), ("24.3.0", False), ("24.21.9", False),
            ("20.20.1", False), ("22.23.4", False), ("25.0.0", False),
            ("24.22.1", True), ("24.22.2", True), ("24.23.0", True),
        ):
            with self.subTest(version=version):
                script = (
                    'Object.defineProperty(process.versions, "node", '
                    f'{{value: "{version}"}});\n{guard}'
                )
                result = subprocess.run(["node", "-e", script], env=environment,
                                        capture_output=True)
                self.assertEqual(result.returncode == 0, expected, result.stderr)
        environment["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
        result = subprocess.run(
            ["node", "-e", 'Object.defineProperty(process.versions, "node", '
             '{value: "24.22.1"});\n' + guard], env=environment, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
