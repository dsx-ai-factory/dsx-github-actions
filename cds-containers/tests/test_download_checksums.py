# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline checks for pinned downloads and fail-closed installation guards."""

import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


DOCKERFILE = Path(__file__).resolve().parents[1] / "tools/Dockerfile"
DOWNLOADS = {
    "KUBECTL": "kubectl",
    "KUBELOGIN": "kubelogin.zip",
    "YQ": "yq",
    "NGCCLI": "ngccli_linux.zip",
    "REGCTL": "regctl",
    "HELM": "helm.tar.gz",
    "TERRAFORM": "terraform.zip",
    "TERRAGRUNT": "terragrunt",
    "BAZEL6": "bazel6",
    "BAZEL8": "bazel8",
}


class DownloadChecksumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dockerfile = DOCKERFILE.read_text()
        # Join Dockerfile continuations so shell commands can be tokenized.
        logical_lines = re.sub(r"\\\n\s*", " ", cls.dockerfile)
        cls.runs = re.findall(r"^RUN (.+)$", logical_lines, re.MULTILINE)

    def download_run(self, name):
        matching = [run for run in self.runs if "${" + name + "_VERSION}" in run]
        self.assertEqual(len(matching), 1, f"Expected one download RUN for {name}")
        return matching[0]

    def checksum_command(self, name):
        commands = self.download_run(name).split("&&")
        self.assertGreaterEqual(len(commands), 3)
        return commands[1].strip()

    def test_all_binary_downloads_are_covered(self):
        outputs = set()
        for run in self.runs:
            command = shlex.split(run.split("&&")[0])
            if command[0] != "curl":
                continue
            self.assertIn("-o", command, "Downloads must use an explicit output file")
            output = command[command.index("-o") + 1]
            # APT repository keys are authenticated separately, not executed.
            if output.endswith((".key", ".asc")):
                continue
            outputs.add(output)
        self.assertEqual(outputs, set(DOWNLOADS.values()))

    def test_each_download_has_a_pinned_sha256(self):
        for name in DOWNLOADS:
            with self.subTest(tool=name):
                self.assertRegex(self.dockerfile,
                                 rf'(?m)^ARG {name}_SHA256="[a-f0-9]{{64}}"$')

    def test_verification_precedes_extract_install_and_execution(self):
        for name, filename in DOWNLOADS.items():
            with self.subTest(tool=name):
                run = self.download_run(name)
                download = shlex.split(run.split("&&")[0])
                self.assertEqual(download[:4], ["curl", "-fsSL", "-o", filename])
                self.assertEqual(len(download), 5)
                self.assertTrue(download[4].startswith("https://"))
                # The second command must verify the exact downloaded file.
                # Every later operation remains gated on its successful exit.
                self.assertEqual(shlex.split(self.checksum_command(name)), [
                    "printf", "%s  %s\\n", f"${name}_SHA256", filename,
                    "|", "sha256sum", "-c", "-",
                ])
                self.assertNotIn(";", run)
                self.assertNotIn("||", run)
                self.assertNotIn("&", run.replace("&&", ""))

    def test_checksum_guard_rejects_bad_downloads_before_installation(self):
        checksum = shutil.which("gsha256sum") or shutil.which("sha256sum")
        self.assertIsNotNone(checksum, "GNU sha256sum is required")
        version = subprocess.run([checksum, "--version"], capture_output=True, text=True)
        if "GNU coreutils" not in version.stdout:
            self.skipTest("GNU coreutils is required to match the Ubuntu build environment")
        payload = b"offline download fixture\n"
        digest = hashlib.sha256(payload).hexdigest()
        for name, filename in DOWNLOADS.items():
            for case in ("valid", "corrupt", "wrong_checksum", "empty_checksum", "missing"):
                with self.subTest(tool=name, case=case), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    commands = directory / "bin"
                    commands.mkdir()
                    (commands / "sha256sum").symlink_to(checksum)
                    if case != "missing":
                        contents = payload + b"tampered" if case == "corrupt" else payload
                        (directory / filename).write_bytes(contents)
                    environment = os.environ.copy()
                    environment["PATH"] = str(commands) + os.pathsep + os.environ["PATH"]
                    expected = {"wrong_checksum": "0" * 64, "empty_checksum": ""}.get(
                        case, digest)
                    environment[f"{name}_SHA256"] = expected
                    # Execute the Dockerfile's exact verifier without fetching
                    # or running a real tool. The marker models installation.
                    result = subprocess.run(
                        ["sh", "-c", self.checksum_command(name)
                         + " && printf installed > installation-marker"],
                        cwd=directory, env=environment, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode == 0, case == "valid", result.stderr)
                    self.assertEqual((directory / "installation-marker").exists(),
                                     case == "valid")


if __name__ == "__main__":
    unittest.main()
