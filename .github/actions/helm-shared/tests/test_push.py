# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
CHART = "nico"
VERSION = "2.4.0-pr-291.g90dfaf12e"
TARGET = f"example/team/{CHART}:{VERSION}"
PRESENT = [{"name": f"helm-repo-ngc/{CHART}", "version": VERSION}]

# Each CLI consumes queued responses, repeating the last response for retries.
# The subprocess boundary keeps the real shell's exit and environment behavior.
CLI_STUB = r'''
import json
import os
from pathlib import Path
import shutil
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["PUBLISH_TEST_STATE"])
state = json.loads(state_path.read_text())
state["calls"].append([tool, *args])
if tool == "sleep":
    operation = "sleep"
elif tool == "helm":
    operation = {
        ("repo", "update"): "update", ("search", "repo"): "search",
        ("env", "HELM_REPOSITORY_CONFIG"): "env", ("show", "chart"): "show",
    }.get(tuple(args[:2]))
    if args[0] == "pull":
        operation = "pull"
    if operation != "env":
        environment = {name: os.environ.get(name, "") for name in (
            "HELM_REPOSITORY_CONFIG", "HELM_REPOSITORY_CACHE", "KUBECONFIG", "HELM_PLUGINS",
        )}
        config_path = Path(environment["HELM_REPOSITORY_CONFIG"])
        environment["repository_config"] = config_path.read_text() if config_path.is_file() else ""
        state["helm_environments"].append(environment)
elif args[:2] == ["config", "current"]:
    operation = "config"
elif args[:3] == ["registry", "chart", "info"]:
    operation = "version_info" if ":" in args[3] else "chart_info"
else:
    operation = args[2] if args[:2] == ["registry", "chart"] else None
if operation not in state["responses"]:
    state["unexpected_commands"].append([tool, *args])
    state_path.write_text(json.dumps(state))
    sys.exit("Unexpected command: " + " ".join([tool, *args]))
responses = state["responses"][operation]
reply = responses.pop(0) if len(responses) > 1 else responses[0]
state_path.write_text(json.dumps(state))
if operation == "pull" and reply.get("code", 0) == 0:
    destination = Path(args[args.index("--destination") + 1])
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(state["remote"], destination / Path(state["remote"]).name)
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("code", 0))
'''


def search_response(rows):
    return {"stdout": json.dumps(rows)}


class HelmPushTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="helm-push-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        for tool in ("helm", "ngc", "sleep"):
            executable = self.bin / tool
            executable.write_text(f"#!{sys.executable}\n{CLI_STUB}")
            executable.chmod(0o755)
        self.local = self.directory / "local" / f"{CHART}-{VERSION}.tgz"
        self.remote = self.directory / "remote" / self.local.name
        self.write_chart(self.local, mtime=1)
        self.write_chart(self.remote, mtime=2)
        self.environment_file = self.directory / "github-env"
        self.state_file = self.directory / "state.json"
        self.temp_root = self.directory / "tmp"
        self.temp_root.mkdir()
        self.repository_config = self.directory / "repositories.yaml"
        self.repository_config.write_text(
            "apiVersion: v1\nrepositories:\n"
            "  - name: helm-repo-ngc\n"
            "    url: https://helm.ngc.example/example/team\n"
            "    username: $oauthtoken\n"
            "    password: test-repository-secret\n"
            "  - name: unrelated-repository\n"
            "    url: https://unrelated.example/charts\n"
        )
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PUBLISH_TEST_STATE": str(self.state_file),
            "GITHUB_ENV": str(self.environment_file),
            "TMPDIR": str(self.temp_root),
            "HELM_REPOSITORY_CONFIG": str(self.repository_config),
            "HELM_REPOSITORY_CACHE": str(self.directory / "shared-cache"),
            "KUBECONFIG": str(self.directory / "inherited-kubeconfig"),
            "HELM_PLUGINS": str(self.directory / "inherited-plugins"),
            "CC_HELM_NGC_PUSH": "true",
            "CC_HELM_NGC_KEY": "test-secret",
            "CC_HELM_NGC_PATH": "example/team",
            "CC_HELM_CHART_NAME": CHART,
            "CC_HELM_CHART_VERSION": VERSION,
            "CC_HELM_CHART_DESCRIPTION": "Test chart",
            "CC_HELM_CHART_TGZ": str(self.local),
        }
        self.env.pop("CC_HELM_NGC_PUSH_STATUS", None)
        self.env.pop("CC_HELM_NGC_RETRIES", None)

    def write_chart(self, path, values=b"replicas: 1\n", mtime=1):
        path.parent.mkdir(parents=True, exist_ok=True)
        files = {
            f"{CHART}/Chart.yaml": f"apiVersion: v2\nname: {CHART}\nversion: {VERSION}\n".encode(),
            f"{CHART}/values.yaml": values,
        }
        with tarfile.open(path, "w:gz") as archive:
            for name, contents in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                member.mtime = mtime
                archive.addfile(member, io.BytesIO(contents))

    def run_push(self, policy="verify", retries=None, **responses):
        defaults = {
            "config": [{}], "chart_info": [{}], "version_info": [{}],
            "create": [{}], "remove": [{}], "push": [{}],
            "update": [{}], "search": [search_response([])],
            "pull": [{}], "sleep": [{}], "show": [{}],
            "env": [{"stdout": str(self.repository_config) + "\n"}],
        }
        self.state_file.write_text(json.dumps({
            "responses": {**defaults, **responses}, "calls": [], "remote": str(self.remote),
            "unexpected_commands": [], "helm_environments": [],
        }))
        if not self.environment_file.is_dir():
            self.environment_file.write_text("")
        # The publisher's exit status controls the action step. GITHUB_ENV
        # assignments only reach subsequent steps.
        env = {**self.env, "CC_HELM_NGC_DUPLICATE": policy}
        if retries is not None:
            env["CC_HELM_NGC_RETRIES"] = str(retries)
        result = subprocess.run(
            [str(SCRIPTS / "utils.sh"), "helm_ngc_push_with_retries"],
            env=env,
            capture_output=True, text=True, timeout=20,
        )
        state = json.loads(self.state_file.read_text())
        self.calls = state["calls"]
        self.helm_environments = state["helm_environments"]
        self.status = self.environment_file.read_text().splitlines() if self.environment_file.is_file() else []
        self.assertEqual(state["unexpected_commands"], [])
        self.assertEqual(list(self.temp_root.iterdir()), [], "publisher temporary files were not cleaned up")
        return result

    def assert_status(self, result, status):
        self.assertEqual(result.returncode == 0, status != "failed", result.stdout + result.stderr)
        self.assertEqual(self.status, [f"CC_HELM_NGC_PUSH_STATUS={status}"])

    def assert_push_skipped_output(self, expected):
        for action_name in ("helm-package-push", "helm-push-ngc"):
            action = SCRIPTS.parents[1] / action_name / "action.yml"
            script = subprocess.check_output(
                ["yq", "-r", '.runs.steps[] | select(.id == "set_outputs") | .run', str(action)],
                text=True,
            )
            output = self.directory / "github-output"
            output.write_text("")
            # Execute the action's actual output step with the final GITHUB_ENV.
            env = {**self.env, **dict(line.split("=", 1) for line in self.status),
                   "GITHUB_OUTPUT": str(output)}
            subprocess.run(["/bin/bash", "-eu", "-c", script], env=env, check=True)
            self.assertIn(f"push-skipped={str(expected).lower()}", output.read_text().splitlines())

    def command_calls(self, *prefix):
        return [call for call in self.calls if call[:len(prefix)] == list(prefix)]

    def assert_no_mutations(self):
        for operation in ("create", "remove", "push"):
            self.assertEqual(self.command_calls("ngc", "registry", "chart", operation), [])

    def test_default_and_zero_do_not_retry_failed_publication(self):
        for retries in (None, 0):
            with self.subTest(retries=retries):
                result = self.run_push(
                    policy="skip", retries=retries, version_info=[{"code": 1}],
                    push=[{"code": 1, "stderr": "already exists in the repository\n"}, {}],
                )
                self.assert_status(result, "failed")
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 1)
                self.assertEqual(self.command_calls("sleep"), [])

    def test_retry_uploads_again_and_stops_after_success(self):
        self.env["CC_HELM_NGC_PUSH_STATUS"] = "skipped"
        for policy in ("skip", "verify"):
            with self.subTest(policy=policy):
                result = self.run_push(
                    policy=policy, retries="0002", version_info=[{"code": 1}],
                    push=[{"code": 1, "stderr": "already exists in the repository\n"}, {}],
                )
                self.assert_status(result, "success")
                self.assert_push_skipped_output(False)
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 2)
                self.assertEqual(
                    self.command_calls("sleep"),
                    [["sleep", "10"]] * (5 if policy == "verify" else 0) + [["sleep", "5"]],
                )
                if policy == "verify":
                    self.assertEqual(len(self.command_calls("helm", "repo", "update")), 8)

    def test_retry_exhaustion_propagates_failure(self):
        self.env["CC_HELM_NGC_PUSH_STATUS"] = "success"
        result = self.run_push(
            policy="skip", retries=3, version_info=[{"code": 1}], push=[{"code": 1}],
        )
        self.assert_status(result, "failed")
        self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 4)
        self.assertIn("NGC publish failed: reached the configured retry limit (3 retries).", result.stderr)
        events = [
            call[1] if call[0] == "sleep" else "push" for call in self.calls
            if call[0] == "sleep" or call[:4] == ["ngc", "registry", "chart", "push"]
        ]
        self.assertEqual(events, ["push", "5", "push", "10", "push", "15", "push"])
        self.assertEqual(self.command_calls("ngc", "registry", "chart", "remove"), [])

    def test_invalid_retries_fail_before_publication(self):
        for retries in ("", "-1", "+1", "1.5", " 1", "1 ", "1\n", "abc", "1+1", "$(exit 0)"):
            with self.subTest(retries=retries):
                result = self.run_push(retries=retries)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("retries must be a nonnegative integer", result.stderr)
                self.assertEqual(self.calls, [])

    def test_retry_reuses_chart_that_appears_after_failed_attempt(self):
        result = self.run_push(
            retries=1, push=[{"code": 1}],
            search=[search_response([])] * 7 + [search_response(PRESENT)],
        )
        self.assert_status(result, "skipped")
        self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 1)
        self.assertEqual(len(self.command_calls("helm", "pull")), 1)
        self.assertEqual(len(self.command_calls("helm", "repo", "update")), 8)
        self.assert_push_skipped_output(True)

    def test_retries_preserve_content_checks(self):
        self.write_chart(self.remote, values=b"replicas: 2\n")
        for searches, uploads in (([search_response(PRESENT)], 0),
                                  ([search_response([]), search_response(PRESENT)], 1)):
            with self.subTest(uploads=uploads):
                result = self.run_push(retries=1, search=searches, push=[{"code": 1}])
                self.assert_status(result, "failed")
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), uploads)
                self.assertEqual(self.command_calls("ngc", "registry", "chart", "remove"), [])
                self.assertIn("chart contents differ", result.stderr)

    def test_existing_legacy_policies_keep_their_declared_behavior(self):
        for policy, status, mutations in (
            ("skip", "skipped", []),
            ("fail", "failed", []),
            ("overwrite", "success", ["remove", "push"]),
        ):
            with self.subTest(policy=policy):
                result = self.run_push(policy=policy)
                self.assert_status(result, status)
                self.assertEqual(
                    [call[3] for call in self.calls
                     if call[:3] == ["ngc", "registry", "chart"] and call[3] in ("remove", "push")],
                    mutations,
                )
                self.assertEqual(self.command_calls("helm"), [])

    def test_matching_existing_prerelease_is_reused_without_a_write(self):
        result = self.run_push(search=[search_response(PRESENT)])
        self.assert_status(result, "skipped")
        self.assert_no_mutations()
        self.assertEqual(self.command_calls("helm", "repo", "update"), [
            ["helm", "repo", "update", "helm-repo-ngc", "--fail-on-repo-update-fail"],
        ])
        self.assertEqual(self.command_calls("helm", "search", "repo"), [
            ["helm", "search", "repo", f"helm-repo-ngc/{CHART}",
             "--versions", "--version", ">=0.0.0-0", "--output", "json"],
        ])
        pull = self.command_calls("helm", "pull")[0]
        self.assertEqual(pull[2], f"helm-repo-ngc/{CHART}")
        self.assertEqual(pull[pull.index("--version") + 1], VERSION)
        self.assertEqual(self.command_calls("helm", "env"), [["helm", "env", "HELM_REPOSITORY_CONFIG"]])
        self.assertTrue(self.helm_environments)
        for environment in self.helm_environments:
            for name in ("HELM_REPOSITORY_CONFIG", "HELM_REPOSITORY_CACHE", "KUBECONFIG", "HELM_PLUGINS"):
                self.assertTrue(Path(environment[name]).is_relative_to(self.temp_root), (name, environment))
            self.assertIn("helm-repo-ngc", environment["repository_config"])
            self.assertIn("$oauthtoken", environment["repository_config"])
            self.assertIn("test-repository-secret", environment["repository_config"])
            self.assertNotIn("unrelated-repository", environment["repository_config"])

    def test_legacy_skip_uploads_an_absent_version(self):
        result = self.run_push(policy="skip", version_info=[{"code": 1}])
        self.assert_status(result, "success")
        self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 1)
        self.assertEqual(self.command_calls("ngc", "registry", "chart", "remove"), [])

    def test_repository_selection_must_be_unambiguous_before_registry_writes(self):
        entry = {"name": "helm-repo-ngc", "url": "https://helm.ngc.example/example/team"}
        for entries in ([], [entry, entry]):
            with self.subTest(entries=len(entries)):
                self.repository_config.write_text(json.dumps({"repositories": entries}))
                result = self.run_push()
                self.assert_status(result, "failed")
                self.assert_no_mutations()
                self.assertIn("Unable to isolate the configured NGC repository.", result.stderr)

    def test_inherited_failed_status_does_not_override_this_publication(self):
        self.env["CC_HELM_NGC_PUSH_STATUS"] = "failed"
        for rows, status in (([], "success"), (PRESENT, "skipped")):
            with self.subTest(status=status):
                result = self.run_push(search=[search_response(rows)])
                self.assert_status(result, status)

    def test_reporting_failure_still_cleans_up_temporary_files(self):
        self.environment_file.mkdir()
        result = self.run_push()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status, [])
        self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 1)

    def test_existing_different_chart_is_rejected_without_overwriting(self):
        self.write_chart(self.remote, values=b"replicas: 2\n")
        result = self.run_push(search=[search_response(PRESENT)])
        self.assert_status(result, "failed")
        self.assert_no_mutations()
        self.assertIn("chart contents differ", result.stderr)
        self.assertIn("Existing NGC chart could not be verified", result.stderr)

    def test_existing_chart_with_denied_download_is_not_reused_or_overwritten(self):
        result = self.run_push(
            search=[search_response(PRESENT)], pull=[{"code": 1, "stderr": "download denied\n"}],
        )
        self.assert_status(result, "failed")
        self.assert_no_mutations()
        self.assertEqual(len(self.command_calls("helm", "pull")), 1)
        self.assertEqual(self.command_calls("sleep"), [])
        self.assertIn("Unable to download the indexed NGC chart version.", result.stderr)
        self.assertIn("Existing NGC chart could not be verified", result.stderr)

    def test_search_substrings_do_not_count_as_the_exact_chart_version(self):
        rows = [
            {"name": "helm-repo-ngc/nico-prereqs", "version": VERSION},
            {"name": "helm-repo-ngc/nico", "version": VERSION + ".other"},
        ]
        result = self.run_push(search=[search_response(rows)])
        self.assert_status(result, "success")
        self.assertEqual(self.command_calls("helm", "pull"), [])
        self.assertEqual(self.command_calls("ngc", "registry", "chart", "push"), [
            ["ngc", "registry", "chart", "push", "--source", str(self.local.parent), TARGET],
        ])

    def test_uncertain_index_or_search_never_becomes_absence(self):
        cases = {
            "index error": (
                {"update": [{"code": 1, "stderr": "index unavailable\n"}]},
                "Unable to refresh the NGC chart index",
            ),
            "silent index error": ({"update": [{"code": 1}]}, "Unable to refresh the NGC chart index"),
            "index warning": (
                {"update": [{"stderr": "cached index retained\n"}]}, "NGC chart index refresh reported errors",
            ),
            "silent search error": (
                {"search": [{"code": 1, "stdout": "[]"}]}, "Unable to search the NGC chart index",
            ),
            "search warning": (
                {"search": [{"stdout": "[]", "stderr": "cache unreadable\n"}]},
                "NGC chart index search reported errors",
            ),
            "invalid JSON": ({"search": [{"stdout": "not JSON"}]}, "Invalid NGC chart index search results"),
            "non-array JSON": ({"search": [search_response({})]}, "Invalid NGC chart index search results"),
            "invalid row": (
                {"search": [search_response([{"name": "helm-repo-ngc/nico"}])]},
                "Invalid NGC chart index search results",
            ),
            "multiple documents": ({"search": [{"stdout": "[]\n[]"}]}, "Invalid NGC chart index search results"),
            "ambiguous version": (
                {"search": [search_response(PRESENT + PRESENT)]}, "Invalid NGC chart index search results",
            ),
        }
        for name, (responses, reason) in cases.items():
            with self.subTest(name=name):
                result = self.run_push(**responses)
                self.assert_status(result, "failed")
                self.assert_no_mutations()
                self.assertIn(reason, result.stderr)
                self.assertIn("Refusing to publish without a fresh NGC version lookup.", result.stderr)

    def test_failed_upload_is_reused_on_last_attempt_after_transient_read_failures(self):
        result = self.run_push(
            push=[{"code": 1, "stderr": "upload connection reset\n"}],
            update=[{}, {"code": 1, "stderr": "index temporarily unavailable\n"}, {}],
            search=[search_response(rows) for rows in ([], [], PRESENT, [], [], PRESENT)],
            pull=[{"code": 1, "stderr": "download temporarily unavailable\n"}, {}],
        )
        self.assert_status(result, "skipped")
        self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 1)
        self.assertEqual(len(self.command_calls("helm", "repo", "update")), 7)
        self.assertEqual(len(self.command_calls("helm", "search", "repo")), 6)
        self.assertEqual(self.command_calls("sleep"), [["sleep", "10"]] * 5)
        self.assertEqual(len(self.command_calls("helm", "pull")), 2)
        self.assertIn("Unable to refresh the NGC chart index", result.stderr)
        self.assertIn("Unable to download the indexed NGC chart version.", result.stderr)

    def test_failed_upload_needs_readable_matching_remote_content(self):
        cases = {
            "missing": (
                {"search": [search_response([])]}, 7,
                "NGC upload failed and no identical chart could be verified after 6 attempts.",
            ),
            "index error": (
                {"update": [{}, {"code": 1, "stderr": "index unavailable\n"}]}, 7,
                "Unable to refresh the NGC chart index",
            ),
            "unreadable": (
                {
                    "search": [search_response([]), search_response(PRESENT)],
                    "pull": [{"code": 1, "stderr": "download denied\n"}],
                }, 7, "Unable to download the indexed NGC chart version.",
            ),
            "mismatched": (
                {"search": [search_response([]), search_response(PRESENT)]}, 2,
                "Published NGC chart differs or is invalid; refusing to reuse it.",
            ),
        }
        self.write_chart(self.remote, values=b"replicas: 2\n")
        for name, (responses, expected_attempts, reason) in cases.items():
            with self.subTest(name=name):
                result = self.run_push(push=[{"code": 1, "stderr": "already exists\n"}], **responses)
                self.assert_status(result, "failed")
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), 1)
                self.assertEqual(self.command_calls("ngc", "registry", "chart", "remove"), [])
                self.assertEqual(len(self.command_calls("helm", "repo", "update")), expected_attempts)
                self.assertEqual(len(self.command_calls("sleep")), expected_attempts - 2)
                self.assertIn(reason, result.stderr)

    def test_missing_chart_metadata_requires_successful_creation_or_readback(self):
        cases = [
            ("created", [{"code": 1}], {}, "success", 1),
            ("creation race", [{"code": 1}, {}], {"code": 1}, "success", 2),
            ("unconfirmed", [{"code": 1}], {"code": 1}, "failed", 2),
        ]
        for name, info, create, status, reads in cases:
            with self.subTest(name=name):
                result = self.run_push(chart_info=info, create=[create])
                self.assert_status(result, status)
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "create")), 1)
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "info")), reads)
                self.assertEqual(len(self.command_calls("ngc", "registry", "chart", "push")), int(status == "success"))
                if status == "failed":
                    self.assertIn("Unable to confirm the NGC chart record after lookup and creation failures.", result.stderr)

    def test_missing_verification_tools_fail_before_registry_writes(self):
        for missing in ("python3", "yq", "jq"):
            with self.subTest(missing=missing):
                minimal_bin = self.directory / f"without-{missing}"
                minimal_bin.mkdir()
                for tool in ("bash", "dirname", "python3", "yq", "jq", "mktemp", "rm"):
                    if tool != missing:
                        executable = shutil.which(tool)
                        self.assertIsNotNone(executable, f"test prerequisite missing: {tool}")
                        (minimal_bin / tool).symlink_to(executable)
                for tool in ("helm", "ngc", "sleep"):
                    (minimal_bin / tool).symlink_to(self.bin / tool)
                self.env["PATH"] = str(minimal_bin)
                result = self.run_push()
                self.assert_status(result, "failed")
                self.assertIn(f"requires {missing}.", result.stderr)
                self.assert_no_mutations()


if __name__ == "__main__":
    unittest.main()
