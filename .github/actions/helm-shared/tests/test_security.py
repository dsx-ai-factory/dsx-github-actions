# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavioral tests for Helm download integrity and environment-file boundaries."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
UTILS = SCRIPTS / "utils.sh"
INSTALLER = SCRIPTS / "install-helm.sh"


def read_environment(path):
    # Model the Linux runner's environment-file parser, not shell evaluation.
    lines = iter(path.read_bytes().decode().split("\n"))
    result = {}
    for line in lines:
        if not line:
            continue
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delimiter = line.split("<<", 1)
            value = []
            for line in lines:
                if line == delimiter:
                    break
                value.append(line)
            else:
                raise AssertionError("Unterminated environment block")
            value = "\n".join(value)
        else:
            name, value = line.split("=", 1)
        if name in result:
            raise AssertionError(f"Duplicate environment variable: {name}")
        result[name] = value
    return result


class EnvironmentSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(shutil.which(tool) for tool in ("jq", "yq")):
            raise unittest.SkipTest("Helm metadata tests require jq and yq")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="helm-env-security-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        for tool in ("jq", "yq"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.metadata = self.directory / "metadata.json"
        self.chart = {"name": "test-chart", "description": "A chart", "version": "1.2.3"}
        self.package = self.directory / "package"
        self.package.mkdir()
        (self.package / "test-chart.tgz").touch()
        helm = self.bin / "helm"
        helm.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            "assert sys.argv[1:3] == ['show', 'chart']\n"
            "sys.stdout.write(pathlib.Path(os.environ['TEST_METADATA']).read_text())\n"
        )
        helm.chmod(0o755)
        self.environment_file = self.directory / "github-env"
        # Do not inherit shell hooks, credentials, or user Helm configuration.
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}{os.defpath}",
            "HOME": str(self.directory),
            "GITHUB_ENV": str(self.environment_file),
            "TEST_METADATA": str(self.metadata),
            "CC_HELM_PACKAGE_DIR": str(self.package),
            "CC_HELM_NGC_PUSH": "false",
            "CC_HELM_NGC_DUPLICATE": "skip",
        }

    def run_utils(self, function, *args, **environment):
        self.metadata.write_text(json.dumps(self.chart))
        self.environment_file.write_text("")
        return subprocess.run(
            ["/bin/bash", str(UTILS), function, *args],
            env={**self.env, **environment}, cwd=self.directory,
            capture_output=True, text=True, timeout=10,
        )

    def assert_push_metadata(self):
        result = self.run_utils("helm_init_push")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_environment(self.environment_file), {
            "CC_HELM_CHART_TGZ": str(self.package / "test-chart.tgz"),
            "CC_HELM_CHART_NAME": self.chart["name"],
            "CC_HELM_CHART_DESCRIPTION": self.chart["description"],
            "CC_HELM_CHART_VERSION": self.chart["version"],
            "CC_HELM_NGC_DUPLICATE": self.env["CC_HELM_NGC_DUPLICATE"],
        })

    def test_valid_metadata(self):
        self.assert_push_metadata()

    def test_multiline_description_is_one_value(self):
        self.chart["description"] = "Line one\nLine two\nLine three"
        self.assert_push_metadata()

    def test_metadata_cannot_inject_variables(self):
        for field in ("name", "description", "version"):
            for newline in ("\n", "\r\n"):
                with self.subTest(field=field, newline=newline):
                    original = self.chart[field]
                    self.chart[field] = f"value{newline}INJECTED=bad{newline}PATH=/untrusted"
                    self.assert_push_metadata()
                    self.chart[field] = original

    def test_package_path_and_duplicate_policy_cannot_inject_variables(self):
        self.package = self.directory / "package\nINJECTED=path"
        self.package.mkdir()
        (self.package / "test-chart.tgz").touch()
        self.env["CC_HELM_PACKAGE_DIR"] = str(self.package)
        self.env["CC_HELM_NGC_DUPLICATE"] = "skip\nINJECTED=policy"
        self.assert_push_metadata()

    def test_fetch_inputs_and_suffix_cannot_inject_variables(self):
        result = self.run_utils(
            "helm_init_fetch", HELM_CHART_NAME="chart\nINJECTED=name",
            HELM_CHART_VERSION="1.0.0\nINJECTED=version",
            HELM_CHART_VERSION_SUFFIX="-rc.1\nINJECTED=suffix",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_environment(self.environment_file), {
            "CC_HELM_CHART_NAME": "chart\nINJECTED=name",
            "CC_HELM_CHART_VERSION": "1.0.0\nINJECTED=version-rc.1\nINJECTED=suffix",
        })

    def test_package_chart_yaml_cannot_inject_variables(self):
        chart_directory = self.directory / "chart"
        chart_directory.mkdir()
        metadata = {"version": "1.2.3\nINJECTED=version", "appVersion": "v1\nINJECTED=app"}
        (chart_directory / "Chart.yaml").write_text(json.dumps(metadata))
        result = self.run_utils("helm_init_package", CC_HELM_CHART_PATH=str(chart_directory))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_environment(self.environment_file), {
            "CC_HELM_CHART_VERSION": metadata["version"],
            "CC_HELM_CHART_APP_VERSION": metadata["appVersion"],
        })

    def test_package_input_overrides_cannot_inject_variables(self):
        result = self.run_utils(
            "helm_init_package", HELM_CHART_VERSION="1.0.0\nINJECTED=version",
            HELM_CHART_APP_VERSION="v1\nINJECTED=app",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_environment(self.environment_file), {
            "CC_HELM_CHART_VERSION": "1.0.0\nINJECTED=version",
            "CC_HELM_CHART_APP_VERSION": "v1\nINJECTED=app",
        })

    def test_multiline_json_round_trips(self):
        cases = (
            ("helm_extra_repos_var", "CC_HELM_EXTRA_REPOS", [{"name": "example", "url": "https://example.com"}]),
            ("helm_value_overrides_var", "CC_HELM_VALUE_OVERRIDES", ["replicas=2", "label=INJECTED=value"]),
        )
        for function, name, values in cases:
            with self.subTest(function=function):
                value = json.dumps(values, indent=2)
                result = self.run_utils(function, **{name: value})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(read_environment(self.environment_file), {name: value})

    def test_helper_preserves_special_characters_and_empty_values(self):
        for value in ("", "-n", r"literal\nbackslash", "$(touch injected)", "value\n\n", "value\rINJECTED=bad"):
            with self.subTest(value=value):
                result = self.run_utils("write_env", "SAFE", value)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(read_environment(self.environment_file), {"SAFE": value})
                self.assertFalse((self.directory / "injected").exists())

    def test_single_line_status_format_remains_compatible(self):
        result = self.run_utils("write_env", "CC_HELM_NGC_PUSH_STATUS", "success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.environment_file.read_text(), "CC_HELM_NGC_PUSH_STATUS=success\n")

    def test_invalid_key_is_rejected_without_writing(self):
        result = self.run_utils("write_env", "SAFE\nINJECTED", "value")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.environment_file.read_text(), "")

    def test_delimiter_collision_is_regenerated(self):
        for ending in ("\n", "\r\n"):
            with self.subTest(ending=ending):
                self.environment_file.write_text("")
                result = subprocess.run(
                    ["/bin/bash", "-c", '''
source "$1" log_info loaded
RANDOM=123
first=$RANDOM
second=$RANDOM
candidate="ghadelimiter_${first}_${second}_$$"
value="first${ENDING}${candidate}${ENDING}INJECTED=bad"
RANDOM=123
write_env SAFE "$value"
printf 'CANDIDATE=%s\n' "$candidate"
''', "test", str(UTILS)],
                    env={**self.env, "ENDING": ending}, cwd=self.directory,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                candidate = result.stdout.split("CANDIDATE=", 1)[1].strip()
                self.assertNotEqual(self.environment_file.read_text().splitlines()[0], f"SAFE<<{candidate}")
                self.assertEqual(read_environment(self.environment_file), {
                    "SAFE": f"first{ending}{candidate}{ending}INJECTED=bad",
                })


INSTALL_CLI = r'''
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["INSTALL_TEST_STATE"])
state = json.loads(state_path.read_text())
state["calls"].append([tool, *args])
state_path.write_text(json.dumps(state))
if tool == "curl":
    url = next(arg for arg in args if arg.startswith("https://"))
    part = "checksum" if url.endswith(".sha256") else "archive"
    if state.get("failure") == part:
        sys.exit(22)
    destination = Path(args[args.index("-o") + 1])
    if part == "archive":
        shutil.copyfile(state["archive"], destination)
    elif state.get("omit_checksum"):
        pass
    else:
        destination.write_text(state["checksum"])
elif tool == "uname":
    print("Linux" if args == ["-s"] else "x86_64")
elif tool == "tar":
    sys.exit(subprocess.run([state["tar"], *args]).returncode)
else:
    sys.exit("Unexpected command")
'''


class InstallSecurityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="helm-install-security-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        # Isolate PATH so the test cannot invoke the user's Helm or real curl.
        for tool in ("bash", "tr", "mktemp", "rm", "mkdir", "cp", "chmod", "gzip"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.hashers = []
        for tool in ("sha256sum", "shasum"):
            binary = shutil.which(tool)
            if binary:
                (self.bin / tool).symlink_to(binary)
                self.hashers.append(tool)
        if not self.hashers:
            self.skipTest("Checksum tests require sha256sum or shasum")
        for tool in ("curl", "uname", "tar"):
            binary = self.bin / tool
            binary.write_text(f"#!{sys.executable}\n{INSTALL_CLI}")
            binary.chmod(0o755)
        self.archive = self.directory / "archive.tar.gz"
        contents = b'#!/bin/sh\nprintf "fixture-version\\n"\nprintf "%s\\n" "$*" >> "$HELM_EXECUTION_LOG"\n'
        with tarfile.open(self.archive, "w:gz") as archive:
            member = tarfile.TarInfo("linux-amd64/helm")
            member.mode = 0o755
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.state_file = self.directory / "state.json"
        self.temp_root = self.directory / "tmp"
        self.temp_root.mkdir()
        self.home = self.directory / "home"
        self.installed = self.home / ".local" / "bin" / "helm"
        self.execution_log = self.directory / "helm-execution.log"
        self.github_path = self.directory / "github-path"
        self.env = {
            "PATH": str(self.bin), "HOME": str(self.home),
            "TMPDIR": str(self.temp_root), "GITHUB_PATH": str(self.github_path),
            "HELM_EXECUTION_LOG": str(self.execution_log),
            "INSTALL_TEST_STATE": str(self.state_file),
        }

    def run_install(self, *args, checksum=None, pin=None, **state):
        self.state_file.write_text(json.dumps({
            "archive": str(self.archive), "checksum": self.digest + "\n" if checksum is None else checksum,
            "tar": shutil.which("tar"), "calls": [], **state,
        }))
        env = dict(self.env)
        if pin is not None:
            env["HELM_SHA256"] = pin
        result = subprocess.run(
            ["/bin/bash", str(INSTALLER), *args], env=env, cwd=self.directory,
            capture_output=True, text=True, timeout=10,
        )
        self.calls = json.loads(self.state_file.read_text())["calls"]
        self.assertEqual(list(self.temp_root.iterdir()), [], "Installer leaked temporary downloads")
        return result

    def assert_failed_before_extract(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(call[0] == "tar" for call in self.calls))
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.execution_log.exists())
        self.assertFalse(self.github_path.exists())

    def test_matching_checksum_installs_default_version(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.installed.is_file())
        self.assertEqual(self.execution_log.read_text(), "version\n")
        self.assertEqual(self.github_path.read_text(), str(self.installed.parent) + "\n")
        downloads = [call for call in self.calls if call[0] == "curl"]
        self.assertEqual(len(downloads), 2)
        for call in downloads:
            self.assertIn("--proto", call)
            self.assertEqual(call[call.index("--proto") + 1], "=https")
            self.assertEqual(call[call.index("--proto-redir") + 1], "=https")
            self.assertIn("-fsSL", call)
        self.assertIn("https://get.helm.sh/helm-v3.13.2-linux-amd64.tar.gz", downloads[0])
        self.assertIn("https://get.helm.sh/helm-v3.13.2-linux-amd64.tar.gz.sha256", downloads[1])

    def test_explicit_version_and_force_remain_supported(self):
        result = self.run_install("v3.18.3", "--force")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any("https://get.helm.sh/helm-v3.18.3-linux-amd64.tar.gz" in call for call in self.calls))

    def test_mismatching_checksum_never_extracts(self):
        self.assert_failed_before_extract(self.run_install(checksum="0" * 64))

    def test_invalid_or_missing_checksum_never_extracts(self):
        for checksum in ("", "\n", "not-a-checksum", "a" * 63, "a" * 65, "f" * 64 + "\nEXTRA=bad", "<html>not found</html>"):
            with self.subTest(checksum=checksum):
                self.assert_failed_before_extract(self.run_install(checksum=checksum))
        self.assert_failed_before_extract(self.run_install(omit_checksum=True))

    def test_failed_download_never_extracts(self):
        for part in ("archive", "checksum"):
            with self.subTest(part=part):
                self.assert_failed_before_extract(self.run_install(failure=part))

    def test_optional_pin_does_not_download_sidecar(self):
        result = self.run_install(pin=self.digest.upper(), failure="checksum")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len([call for call in self.calls if call[0] == "curl"]), 1)
        self.assertTrue(self.installed.exists())

    def test_invalid_or_mismatching_pin_does_not_fall_back(self):
        for pin in ("invalid", "0" * 64, self.digest + "\nINJECTED=bad"):
            with self.subTest(pin=pin):
                self.assert_failed_before_extract(self.run_install(pin=pin))
                self.assertEqual(len([call for call in self.calls if call[0] == "curl"]), 1)

    def test_shasum_fallback(self):
        if "shasum" not in self.hashers:
            self.skipTest("shasum is unavailable")
        if (self.bin / "sha256sum").exists():
            (self.bin / "sha256sum").unlink()
        self.assert_failed_before_extract(self.run_install(checksum="0" * 64))
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_checksum_tool_fails_closed(self):
        for tool in self.hashers:
            (self.bin / tool).unlink()
        self.assert_failed_before_extract(self.run_install())

    def test_invalid_version_does_not_download(self):
        self.assert_failed_before_extract(self.run_install("v3.13.2/../../untrusted"))
        self.assertEqual(self.calls, [])

    def test_existing_helm_is_skipped_unless_forced(self):
        existing = self.bin / "helm"
        existing.write_text('#!/bin/sh\nprintf "existing-version\\n"\n')
        existing.chmod(0o755)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("existing-version", result.stdout)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.installed.exists())
        result = self.run_install("--force")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.installed.exists())


if __name__ == "__main__":
    unittest.main()
