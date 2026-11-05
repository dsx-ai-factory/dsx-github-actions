# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest

from test_promotion import charts

ROOT = Path(__file__).resolve().parents[3]


def job_enabled(expression, context):
    # Evaluate only the boolean conditions used by the RC wrapper, with fixture contexts.
    def literal(match):
        value = context
        for key in match.group().split("."):
            value = value[key]
        return json.dumps(value)

    expression = re.sub(r"\b(?:inputs|needs|github|steps)(?:\.[A-Za-z0-9_-]+)+", literal, expression)
    result = subprocess.run([
        "node", "-e",
        "const vm = require('node:vm'); const expression = JSON.parse(process.argv[1]);"
        "const enabled = vm.runInNewContext(expression, {always: () => true, "
        "startsWith: (value, prefix) => value.startsWith(prefix)}, {timeout: 1000});"
        "process.stdout.write(JSON.stringify(Boolean(enabled)));",
        json.dumps(expression),
    ], capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


class WorkflowTests(unittest.TestCase):
    def test_node_setup_uses_the_security_patched_runtime(self):
        configured = set()
        for path in (ROOT / "workflows").glob("*.yml"):
            workflow = charts.yaml_object(path.read_bytes())
            for job in workflow.get("jobs", {}).values():
                for step in job.get("steps", []):
                    if step.get("uses", "").startswith("actions/setup-node@"):
                        with self.subTest(workflow=path.name):
                            self.assertEqual(step["with"]["node-version"], "24.22.1")
                            self.assertIs(step["with"]["package-manager-cache"], False)
                        configured.add(path.name)
        self.assertTrue({"release.yml", "release-candidate.yml",
                         "release-candidate-publish.yml", "release-candidate-checks.yml",
                         "promote-rc.yml", "validate.yml"}.issubset(configured))
        commitlint = charts.yaml_object((ROOT / "actions/commitlint/action.yml").read_bytes())
        self.assertEqual(commitlint["inputs"]["node-version"]["default"], "24.22.1")
        tests = ROOT / "actions/release-candidate/tests"
        package = json.loads((tests / "package.json").read_text())
        lock = json.loads((tests / "package-lock.json").read_text())
        self.assertEqual(package["engines"], {"node": ">=24.22.1 <25"})
        self.assertEqual(lock["packages"][""]["engines"], package["engines"])

    def test_only_manual_version_input_and_fixed_publishing_gate(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        self.assertEqual(set(workflow["on"]), {"workflow_call"})
        call = workflow["on"]["workflow_call"]
        self.assertEqual(set(call), {"inputs", "secrets"})
        self.assertEqual(set(call["inputs"]), {"rc_version"})
        self.assertEqual({name: settings["required"] for name, settings in call["secrets"].items()}, {
            "RC_PROMOTION_APP_PRIVATE_KEY": False,
            "RC_PROMOTION_PAT": False,
            "RC_PROMOTION_SOURCE_NGC_KEY": True,
            "RC_PROMOTION_DESTINATION_NGC_KEY": True,
        })
        self.assertEqual(workflow["concurrency"], {
            "group": "dsx-stable-release-${{ github.repository }}", "cancel-in-progress": False,
        })
        admission = workflow["jobs"]["admission"]
        self.assertNotIn("environment", admission)
        self.assertNotIn("permissions", admission)
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        gate = admission["steps"][0]
        self.assertEqual(gate["env"]["RELEASE_MODE"], "${{ vars.DSX_RELEASE_MODE }}")
        for check in ['"$EVENT" == workflow_dispatch', '"$PROTECTED" == true',
                      '"$REF" == "refs/heads/$DEFAULT_BRANCH"', '"$RELEASE_MODE" == rc-promotion']:
            self.assertIn(check, gate["run"])
        publish = workflow["jobs"]["promote"]
        self.assertEqual(publish["needs"], "admission")
        self.assertEqual(publish["environment"], "stable-release")
        self.assertEqual(publish["permissions"], {
            "contents": "read", "actions": "read", "deployments": "read",
        })
        step = next(step for step in publish["steps"] if step.get("name") == "Verify and promote the selected RC")
        for key in ("RC_PROMOTION_SOURCE_NGC_KEY", "RC_PROMOTION_DESTINATION_NGC_KEY"):
            self.assertEqual(step["env"][key], "${{ secrets." + key + " }}")
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertEqual(step["env"]["RC_PROMOTION_GITHUB_TOKEN"], "${{ steps.release-token.outputs.token }}")
        self.assertEqual(step["env"]["DSX_RELEASE_MODE"], "${{ needs.admission.outputs.release-mode }}")

    def test_release_auth_defaults_to_app_and_accepts_explicit_pat_before_tools(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        select = workflow["jobs"]["promote"]["steps"][0]
        self.assertEqual(select["id"], "release-auth")
        self.assertEqual(select["env"], {
            "AUTH_MODE": "${{ vars.RC_PROMOTION_AUTH_MODE || 'app' }}",
        })
        self.assertNotIn("if", select)
        self.assertNotIn("continue-on-error", select)
        for mode in ("app", "pat"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                result = subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", select["run"]],
                    cwd=directory, env={"AUTH_MODE": mode, "GITHUB_OUTPUT": str(output),
                                        "PATH": directory},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), f"mode={mode}\n")

    def test_invalid_auth_mode_fails_without_emitting_a_mode(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        select = workflow["jobs"]["promote"]["steps"][0]
        for mode in ("", "APP", "PAT", "auto", " app", "pat ", "app\npat", "app; echo pat"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                output.touch()
                result = subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", select["run"]],
                    cwd=directory, env={"AUTH_MODE": mode, "GITHUB_OUTPUT": str(output),
                                        "PATH": directory},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_text(), "")

    def test_selected_credentials_are_checked_before_tools(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        check = workflow["jobs"]["promote"]["steps"][1]
        self.assertEqual(check["name"], "Check selected promotion credentials")
        self.assertNotIn("if", check)
        self.assertNotIn("continue-on-error", check)
        self.assertEqual(check["env"], {
            "AUTH_MODE": "${{ steps.release-auth.outputs.mode }}",
            "RC_PROMOTION_APP_ID": "${{ vars.RC_PROMOTION_APP_ID }}",
            "RC_PROMOTION_APP_PRIVATE_KEY": "${{ steps.release-auth.outputs.mode == 'app' && secrets.RC_PROMOTION_APP_PRIVATE_KEY || '' }}",
            "RC_PROMOTION_PAT": "${{ steps.release-auth.outputs.mode == 'pat' && secrets.RC_PROMOTION_PAT || '' }}",
            "RC_PROMOTION_SOURCE_NGC_KEY": "${{ secrets.RC_PROMOTION_SOURCE_NGC_KEY }}",
            "RC_PROMOTION_DESTINATION_NGC_KEY": "${{ secrets.RC_PROMOTION_DESTINATION_NGC_KEY }}",
        })
        registry = {
            "RC_PROMOTION_SOURCE_NGC_KEY": "test-source-key",
            "RC_PROMOTION_DESTINATION_NGC_KEY": "test-destination-key",
        }
        credentials = {
            "app": {**registry, "RC_PROMOTION_APP_ID": "12345",
                    "RC_PROMOTION_APP_PRIVATE_KEY": "test-private-key\nsecond-line"},
            "pat": {**registry, "RC_PROMOTION_PAT": "test-pat-token"},
        }
        for mode, selected in credentials.items():
            for missing in (None, *selected):
                for absent in (False, True):
                    with self.subTest(mode=mode, missing=missing, absent=absent), \
                            tempfile.TemporaryDirectory() as directory:
                        env = {"PATH": directory, "AUTH_MODE": mode, **selected}
                        # An unselected credential must never rescue a missing selected one.
                        if mode == "app":
                            env["RC_PROMOTION_PAT"] = "test-unselected-pat"
                        else:
                            env["RC_PROMOTION_APP_PRIVATE_KEY"] = "test-unselected-key"
                        if missing:
                            if absent:
                                del env[missing]
                            else:
                                env[missing] = ""
                        result = subprocess.run(
                            ["/bin/bash", "--noprofile", "--norc", "-ex", "-o", "pipefail", "-c", check["run"]],
                            cwd=directory, env=env, capture_output=True, text=True, timeout=10,
                        )
                        if missing:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn(f"Missing {missing}.", result.stderr)
                            self.assertIn("caller's explicit secret mappings", result.stderr)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                        for value in (*selected.values(), "test-unselected-pat", "test-unselected-key"):
                            self.assertNotIn(value, result.stdout + result.stderr)

    def test_pat_is_injected_only_into_explicit_pat_checks_and_promotion(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        step = next(step for step in workflow["jobs"]["promote"]["steps"]
                    if step.get("name") == "Verify and promote the selected RC")
        self.assertEqual(step["env"]["RC_PROMOTION_AUTH_MODE"], "${{ steps.release-auth.outputs.mode }}")
        self.assertEqual(step["env"]["RC_PROMOTION_PAT"],
                         "${{ steps.release-auth.outputs.mode == 'pat' && secrets.RC_PROMOTION_PAT || '' }}")
        self.assertEqual(json.dumps(workflow).count("secrets.RC_PROMOTION_PAT"), 2)
        self.assertNotIn("if", step)
        self.assertNotIn("continue-on-error", step)

    def test_verify_shell_selects_only_explicit_pat_and_removes_credential_alias(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        step = next(step for step in workflow["jobs"]["promote"]["steps"]
                    if step.get("name") == "Verify and promote the selected RC")
        cases = [
            ("app", "test-app-token", "", "test-app-token"),
            ("app", "test-app-token", "test-pat-token", "test-app-token"),
            ("pat", "", "test-pat-token", "test-pat-token"),
            ("pat", "test-app-token", "test-pat-token", "test-pat-token"),
            ("app", "", "test-pat-token", ""),
            ("pat", "test-app-token", "", ""),
        ]
        for mode, app_token, pat_token, selected in cases:
            with self.subTest(mode=mode, app=bool(app_token), pat=bool(pat_token)), \
                    tempfile.TemporaryDirectory() as directory:
                # An isolated PATH can only execute this stub, never a live promotion tool.
                python = Path(directory) / "python3"
                python.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, sys\n"
                    "from pathlib import Path\n"
                    "keys = ('GH_TOKEN', 'RC_PROMOTION_GITHUB_TOKEN', 'RC_PROMOTION_PAT')\n"
                    "Path('python-env.json').write_text(json.dumps({\n"
                    "    'argv': sys.argv[1:],\n"
                    "    'env': {key: os.environ[key] for key in keys if key in os.environ},\n"
                    "}))\n"
                )
                python.chmod(0o700)
                result = subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-ex", "-o", "pipefail", "-c", step["run"]],
                    cwd=directory, env={
                        "PATH": directory, "GH_TOKEN": "test-workflow-token",
                        "RC_PROMOTION_AUTH_MODE": mode, "RC_PROMOTION_GITHUB_TOKEN": app_token,
                        "RC_PROMOTION_PAT": pat_token,
                    }, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = json.loads((Path(directory) / "python-env.json").read_text())
                self.assertEqual(observed["argv"], [
                    "dsx-promotion-tools/.github/actions/promote-rc/promotion.py", "promote",
                    "--policy", "promotion-source/.github/rc-promotion.json",
                ])
                self.assertEqual(observed["env"], {
                    "GH_TOKEN": "test-workflow-token", "RC_PROMOTION_GITHUB_TOKEN": selected,
                })
                for token in ("test-app-token", "test-pat-token", "test-workflow-token"):
                    self.assertNotIn(token, result.stdout + result.stderr)

    def test_app_token_is_environment_scoped_repository_restricted_and_revoked(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        steps = workflow["jobs"]["promote"]["steps"]
        mint = next(step for step in steps if step.get("id") == "release-token")
        self.assertEqual(mint["if"], "steps.release-auth.outputs.mode == 'app'")
        self.assertNotIn("continue-on-error", mint)
        self.assertEqual(mint["uses"],
                         "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510")
        self.assertEqual(mint["with"], {
            "app-id": "${{ vars.RC_PROMOTION_APP_ID }}",
            "private-key": "${{ secrets.RC_PROMOTION_APP_PRIVATE_KEY }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "${{ github.event.repository.name }}",
            "permission-contents": "write", "permission-workflows": "write",
            "skip-token-revoke": False,
        })
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout@"):
                self.assertLess(steps.index(step), steps.index(mint))
                self.assertIs(step["with"]["persist-credentials"], False)
                self.assertNotIn("token", step["with"])
            if step.get("name") != "Verify and promote the selected RC":
                self.assertNotIn("steps.release-token.outputs.token", str(step))

    def test_promotion_does_not_rebuild_or_run_the_rc_publisher(self):
        workflow = charts.yaml_object((ROOT / "workflows/promote-rc.yml").read_bytes())
        for job in workflow["jobs"].values():
            self.assertNotIn("uses", job)
            for step in job["steps"]:
                self.assertNotIn("docker-build", step.get("uses", ""))
                self.assertNotIn("build-push-action", step.get("uses", ""))
                for build_command in ("docker build ", "buildx build ", "helm package ",
                                      "helm dependency build", "helm dependency update"):
                    self.assertNotIn(build_command, step.get("run", ""))

    def test_rc_evidence_is_additive_opt_in_and_waits_for_all_artifacts(self):
        workflow = charts.yaml_object((ROOT / "workflows/release-candidate-publish.yml").read_bytes())
        option = workflow["on"]["workflow_call"]["inputs"]["promotion-manifest"]
        self.assertEqual(option["type"], "boolean")
        self.assertIs(option["default"], False)
        job = workflow["jobs"]["promotion-manifest"]
        self.assertEqual(job["needs"], ["plan", "release", "images", "charts"])
        for condition in [
            "inputs.promotion-manifest", "needs.release.outputs.should-publish == 'true'",
            "needs.images.result == 'success' || needs.plan.outputs.image-count == '0'",
            "needs.charts.result == 'success' || needs.plan.outputs.chart-count == '0'",
        ]:
            self.assertIn(condition, job["if"])
        upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"]["path"], "rc-promotion-manifest.json")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["name"],
                         "rc-promotion-manifest-${{ needs.release.outputs.version }}-${{ github.run_attempt }}")

    def test_external_publisher_keeps_source_release_without_registry_credentials(self):
        workflow = charts.yaml_object((ROOT / "workflows/release-candidate-publish.yml").read_bytes())
        self.assertEqual(workflow["on"]["workflow_call"]["inputs"]["artifact-publisher"]["default"],
                         "github")
        call = workflow["on"]["workflow_call"]
        self.assertEqual({name for name, settings in call["inputs"].items() if settings.get("required")},
                         {"runner"})
        for name in ("environment", "ngc-path"):
            self.assertEqual(call["inputs"][name]["default"], "")
            self.assertFalse(call["inputs"][name].get("required", False))
        self.assertFalse(call["secrets"]["NGC_DSX_COMPONENTS_PUSH_KEY"]["required"])
        jobs = workflow["jobs"]
        self.assertEqual(jobs["release"]["needs"], "plan")
        self.assertEqual(jobs["release"]["uses"], "./.github/workflows/release-candidate.yml")
        self.assertEqual(jobs["release"]["secrets"], {
            "release-deploy-key": "${{ secrets.release-deploy-key }}",
        })
        for name in ("plan", "release"):
            self.assertNotIn("environment", jobs[name])
            self.assertNotIn("NGC_DSX_COMPONENTS_PUSH_KEY", json.dumps(jobs[name]))
        plan = next(step for step in jobs["plan"]["steps"] if step.get("id") == "plan")
        self.assertEqual(plan["env"]["RC_ARTIFACT_PUBLISHER"], "${{ inputs.artifact-publisher }}")
        self.assertNotIn("RC_PROMOTION_MANIFEST", plan["env"])
        for name in ("validate-images", "validate-charts", "images", "charts", "promotion-manifest"):
            self.assertIn("inputs.artifact-publisher == 'github'", jobs[name]["if"])
        job = jobs["promotion-manifest"]
        self.assertEqual(job["timeout-minutes"], 20)
        capture = next(step for step in job["steps"] if step.get("name") == "Capture every verified RC artifact")
        self.assertNotIn("RC_CAPTURE_WAIT_SECONDS", capture["env"])
        self.assertEqual(capture["run"], "python3 dsx-release-tools/.github/actions/promote-rc/promotion.py capture")
        self.assertNotIn("continue-on-error", capture)
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ job.workflow_sha }}")

    def test_source_release_has_no_artifact_secrets_or_registry_operations(self):
        workflow = charts.yaml_object((ROOT / "workflows/release-candidate.yml").read_bytes())
        self.assertEqual(set(workflow["on"]["workflow_call"]["secrets"]), {"release-deploy-key"})
        self.assertNotRegex(json.dumps(workflow), r"NGC|nvcr\.io|helm\.ngc|docker/login|promote-rc")
        self.assertNotIn("environment", workflow["jobs"]["release"])

    def test_external_skips_all_artifact_jobs_even_with_legacy_options_enabled(self):
        workflow = charts.yaml_object((ROOT / "workflows/release-candidate-publish.yml").read_bytes())
        jobs = workflow["jobs"]
        for publisher in ("github", "external"):
            for manifest in (False, True):
                for event, ref in (("push", "refs/heads/release/1.16.0"),
                                   ("push", "refs/heads/pull-request/99"),
                                   ("pull_request", "refs/pull/99/merge")):
                    with self.subTest(publisher=publisher, manifest=manifest, event=event, ref=ref):
                        releasing = ref.startswith("refs/heads/release/") and event == "push"
                        context = {
                            "inputs": {"artifact-publisher": publisher, "promotion-manifest": manifest,
                                       "validate-artifacts": True},
                            "github": {"event_name": event, "ref": ref},
                            "needs": {
                                "plan": {"outputs": {"image-count": "1", "chart-count": "1"}},
                                "release": {"outputs": {"should-publish": "true" if releasing else "false"}},
                                "images": {"result": "success"}, "charts": {"result": "success"},
                            },
                            "steps": {"plan": {"outputs": {"image-count": "1"}}},
                        }
                        for name in ("validate-images", "validate-charts", "images", "charts", "promotion-manifest"):
                            expected = publisher == "github" and (
                                not releasing if name.startswith("validate-") else releasing)
                            if name == "promotion-manifest":
                                expected = expected and manifest
                            self.assertEqual(job_enabled(jobs[name]["if"], context), expected, name)
                        self.assertEqual(job_enabled(jobs["release"]["if"], context), releasing)
                        for step in jobs["plan"]["steps"]:
                            if "if" in step and "inputs.artifact-publisher" in step["if"]:
                                self.assertEqual(job_enabled(step["if"], context), publisher == "github")

    def test_github_publisher_still_requires_an_environment_before_tag_creation(self):
        workflow = charts.yaml_object((ROOT / "workflows/release-candidate-publish.yml").read_bytes())
        step = workflow["jobs"]["plan"]["steps"][0]
        self.assertEqual(step["name"], "Require the GitHub publishing environment")
        self.assertEqual(step["if"], "inputs.artifact-publisher == 'github'")
        for environment in ("", "release-candidate"):
            result = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
                env={"PUBLISHING_ENVIRONMENT": environment, "PATH": ""},
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0 if environment else 1)


if __name__ == "__main__":
    unittest.main()
