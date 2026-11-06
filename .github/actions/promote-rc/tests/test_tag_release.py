# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_promotion import COMMIT, PUBLISHER_SHA, RC, charts, config, p
import tag_release as t


def document():
    return {**config(), "gitlab": {
        "api_url": "https://gitlab.example.com/api/v4", "project_id": 123,
        "repository": "example/product", "authorization": "maintainer", "stable_tag_pipeline": True,
    }}


class GitHubFixture(p.GitHub):
    def __init__(self):
        super().__init__("example/product", write_token="test-release-token")
        self.refs = {f"v{RC}": {"type": "commit", "sha": COMMIT}}
        self.objects = {}
        self.releases = []
        self.writes = []
        self.selected = None
        self.notes_calls = 0
        self.fail_release = False
        self.policy_document = document()
        self.latest_attempt = 1

    def request(self, path, method="GET", data=None, **kwargs):
        self.assert_root(path)
        path = path.removeprefix(self.root + "/")
        if method != "GET":
            self.writes.append((path, copy.deepcopy(data)))
        if path.startswith("git/ref/tags/"):
            obj = self.refs.get(path.removeprefix("git/ref/tags/"))
            return {"object": obj} if obj else None
        if path.startswith("git/tags/"):
            return self.objects[path.removeprefix("git/tags/")]
        if path.startswith("releases?"):
            return self.releases
        if path.startswith("contents/"):
            raw = p.canonical(self.policy_document)
            return {"encoding": "base64", "size": len(raw),
                    "content": base64.b64encode(raw).decode()}
        if path == "git/tags" and method == "POST":
            sha = f"{len(self.objects) + 1:040x}"
            self.objects[sha] = {
                "sha": sha, "tag": data["tag"], "message": data["message"],
                "object": {"type": data["type"], "sha": data["object"]},
            }
            return self.objects[sha]
        if path == "git/refs" and method == "POST":
            tag = data["ref"].removeprefix("refs/tags/")
            if tag in self.refs:
                raise p.PromotionError("tag already exists")
            self.refs[tag] = {"type": "tag", "sha": data["sha"]}
            return {"object": self.refs[tag]}
        if path == "releases" and method == "POST":
            if self.fail_release:
                raise p.PromotionError("release unavailable")
            release = {"id": 1, **data}
            self.releases.append(release)
            return release
        raise AssertionError((path, method))

    def assert_root(self, path):
        if not path.startswith(self.root + "/"):
            raise AssertionError("request left the product repository")

    def source_run(self, policy, rc, *, selected=None):
        self.selected = selected
        run = selected or {"id": 12, "run_attempt": self.latest_attempt}
        return self.tag(f"v{rc}"), run, PUBLISHER_SHA

    def release_notes(self, stable, commit):
        self.notes_calls += 1
        return "## 1.2.3\n\n### Fixes\n\n* verified fix\n"


class TagTests(unittest.TestCase):
    def setUp(self):
        self.github = GitHubFixture()
        for name in ("Registry", "authenticate_registries"):
            patcher = patch.object(p, name, side_effect=AssertionError("registry access"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(p.GitHub, "evidence", side_effect=AssertionError("artifact access"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_creates_annotated_tag_before_release_without_registry_access(self):
        intent = t.promote(document(), self.github, RC)
        self.assertEqual(set(intent), t.FIELDS)
        self.assertEqual(intent, t.read_tag(self.github, "1.2.3"))
        self.assertEqual(self.github.tag("v1.2.3"), COMMIT)
        self.assertEqual(intent["source_run_id"], 12)
        self.assertEqual([path for path, _ in self.github.writes],
                         ["git/tags", "git/refs", "releases"])
        self.assertEqual(self.github.releases[0]["body"], "## 1.2.3\n\n### Fixes\n\n* verified fix\n")
        self.assertFalse(self.github.releases[0]["prerelease"])

    def test_identical_retry_preserves_tag_release_notes_and_source_attempt(self):
        expected = t.promote(document(), self.github, RC)
        self.github.releases[0]["body"] = "Human-edited release notes"
        writes = copy.deepcopy(self.github.writes)
        self.assertEqual(t.promote(document(), self.github, RC), expected)
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.notes_calls, 1)
        self.assertEqual(self.github.selected, {"id": 12, "run_attempt": 1})
        self.assertEqual(self.github.releases[0]["body"], "Human-edited release notes")

    def test_tag_survives_release_failure_and_is_sufficient_for_mirror_consumer(self):
        self.github.fail_release = True
        with self.assertRaisesRegex(p.PromotionError, "release unavailable"):
            t.promote(document(), self.github, RC)
        intent = t.read_tag(self.github, "1.2.3")
        self.assertEqual(intent["rc_version"], RC)
        self.assertEqual(self.github.releases, [])
        self.github.fail_release = False
        self.github.writes.clear()
        self.assertEqual(t.promote(document(), self.github, RC), intent)
        self.assertEqual([path for path, _ in self.github.writes], ["releases"])

    def test_notes_or_source_validation_failure_never_tags(self):
        for method in ("source_run", "release_notes"):
            with self.subTest(method=method), patch.object(
                self.github, method, side_effect=p.PromotionError("rejected")
            ), self.assertRaises(p.PromotionError):
                t.promote(document(), self.github, RC)
            self.assertEqual(self.github.writes, [])

    def test_rc_without_stable_pipeline_capability_stops_before_tagging(self):
        for enabled in (False, None):
            self.github.policy_document["gitlab"].pop("stable_tag_pipeline", None)
            if enabled is not None:
                self.github.policy_document["gitlab"]["stable_tag_pipeline"] = enabled
            with self.subTest(enabled=enabled), self.assertRaisesRegex(p.PromotionError, "does not enable"):
                t.promote(document(), self.github, RC)
            self.assertEqual(self.github.writes, [])

    def test_rc_inventory_mismatch_stops_before_tagging(self):
        self.github.policy_document["images"].append("another-service")
        with self.assertRaisesRegex(p.PromotionError, "policies differ; create a new RC"):
            t.promote(document(), self.github, RC)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(self.github.notes_calls, 0)

    def test_rc_gitlab_gate_mismatch_stops_before_tagging(self):
        for key, value in [("project_id", 456), ("api_url", "https://other.example.com/api/v4"),
                           ("artifact_download_hosts", ["artifacts.example.com"])]:
            self.github.policy_document = document()
            self.github.policy_document["gitlab"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(p.PromotionError, "policies differ"):
                t.promote(document(), self.github, RC)
            self.assertEqual(self.github.writes, [])
            self.assertEqual(self.github.notes_calls, 0)

    def test_initial_selection_rejects_rerun_without_inferring_gitlab_evidence(self):
        self.github.latest_attempt = 2
        with self.assertRaisesRegex(p.PromotionError, "RC publishing was rerun; create a new RC"):
            t.promote(document(), self.github, RC)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(self.github.notes_calls, 0)

    def test_partial_tag_retry_uses_recorded_attempt_after_source_workflow_rerun(self):
        self.github.fail_release = True
        with self.assertRaisesRegex(p.PromotionError, "release unavailable"):
            t.promote(document(), self.github, RC)
        intent = t.read_tag(self.github, "1.2.3")
        self.github.latest_attempt = 2
        self.github.fail_release = False
        self.github.writes.clear()
        self.assertEqual(t.promote(document(), self.github, RC), intent)
        self.assertEqual(self.github.selected, {"id": 12, "run_attempt": 1})
        self.assertEqual([path for path, _ in self.github.writes], ["releases"])

    def test_existing_tag_can_retry_its_previously_recorded_later_attempt(self):
        intent = t.promote(document(), self.github, RC)
        intent["source_run_attempt"] = 2
        sha = self.github.refs["v1.2.3"]["sha"]
        self.github.objects[sha]["message"] = p.canonical(intent).decode()
        self.github.latest_attempt = 3
        self.github.writes.clear()
        self.assertEqual(t.promote(document(), self.github, RC), intent)
        self.assertEqual(self.github.selected, {"id": 12, "run_attempt": 2})
        self.assertEqual(self.github.writes, [])

    def test_rc_moved_after_provenance_check_stops_before_writes(self):
        with patch.object(self.github, "tag", return_value="f" * 40), patch.object(
            self.github, "source_run", return_value=(COMMIT, {"id": 12, "run_attempt": 1}, PUBLISHER_SHA)
        ), self.assertRaisesRegex(p.PromotionError, "RC tag changed"):
            t.promote(document(), self.github, RC)
        self.assertEqual(self.github.writes, [])

    def test_conflicting_rc_or_legacy_lightweight_tag_is_never_replaced(self):
        t.promote(document(), self.github, RC)
        self.github.writes.clear()
        with self.assertRaisesRegex(p.PromotionError, "another RC"):
            t.promote(document(), self.github, "1.2.3-rc.8")
        self.assertEqual(self.github.writes, [])
        self.github.refs["v1.2.3"] = {"type": "commit", "sha": COMMIT}
        with self.assertRaisesRegex(p.PromotionError, "annotated"):
            t.promote(document(), self.github, RC)
        self.assertEqual(self.github.writes, [])

    def test_preexisting_legacy_release_cannot_be_taken_over(self):
        self.github.releases = [{"tag_name": "v1.2.3", "draft": True, "prerelease": False}]
        with self.assertRaisesRegex(p.PromotionError, "completed tag-only"):
            t.promote(document(), self.github, RC)
        self.assertEqual(self.github.writes, [])

    def test_annotation_rejects_tampering_and_nested_tags(self):
        intent = t.promote(document(), self.github, RC)
        sha = self.github.refs["v1.2.3"]["sha"]
        original = copy.deepcopy(self.github.objects[sha])
        for key, value in [("schema", True), ("kind", "other"), ("repository", "other/repo"),
                           ("stable_version", "9.9.9"), ("commit", "b" * 40),
                           ("source_run_id", True), ("source_run_attempt", 0),
                           ("source_publisher_sha", "main"), ("unexpected", True)]:
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.github.objects[sha]["message"] = p.canonical({**intent, key: value}).decode()
                t.read_tag(self.github, "1.2.3")
        self.github.objects[sha] = copy.deepcopy(original)
        self.github.objects[sha]["object"]["type"] = "tag"
        with self.assertRaisesRegex(p.PromotionError, "directly"):
            t.read_tag(self.github, "1.2.3")

    def test_duplicate_json_fields_are_not_a_canonical_annotation(self):
        t.promote(document(), self.github, RC)
        sha = self.github.refs["v1.2.3"]["sha"]
        original = self.github.objects[sha]["message"]
        self.github.objects[sha]["message"] = '{"schema":2,' + original[1:]
        with self.assertRaises(p.PromotionError):
            t.read_tag(self.github, "1.2.3")

    def test_annotation_round_trips_through_real_git_tag_serialization(self):
        intent = t.promote(document(), self.github, RC)
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "Release Test", "GIT_AUTHOR_EMAIL": "release@example.test",
                   "GIT_COMMITTER_NAME": "Release Test", "GIT_COMMITTER_EMAIL": "release@example.test"}

            def git(*args, data=None):
                return subprocess.run(["git", *args], cwd=directory, env=env, input=data,
                                      capture_output=True, check=True, timeout=10).stdout

            git("init", "--quiet")
            tree = git("write-tree").decode().strip()
            commit = git("commit-tree", tree, data=b"fixture\n").decode().strip()
            intent["commit"] = commit
            message = p.canonical(intent)
            raw = (f"object {commit}\ntype commit\ntag v1.2.3\n"
                   "tagger Release Test <release@example.test> 1700000000 +0000\n\n").encode() + message
            sha = git("mktag", data=raw).decode().strip()
            git("update-ref", "refs/tags/v1.2.3", sha)
            restored = git("cat-file", "tag", "refs/tags/v1.2.3").split(b"\n\n", 1)[1]
            self.assertEqual(restored, message)
            self.github.refs["v1.2.3"] = {"type": "tag", "sha": sha}
            self.github.objects[sha] = {"sha": sha, "tag": "v1.2.3", "message": restored.decode(),
                                        "object": {"type": "commit", "sha": commit}}
            self.assertEqual(t.read_tag(self.github, "1.2.3"), intent)


class PolicyTests(unittest.TestCase):
    def test_load_policy_preserves_default_surface_and_exposes_full_document(self):
        github = GitHubFixture()
        self.assertEqual(t.load_policy(github, p.POLICY_PATH, COMMIT), config())
        self.assertEqual(t.load_policy(github, p.POLICY_PATH, COMMIT, full=True), document())

    def test_shared_policy_is_validated_without_discarding_inventory(self):
        self.assertEqual(t.policy(document(), "example/product"), config())
        for key, value in [("repository", "other/repo"), ("project_id", True), ("unknown", True),
                           ("stable_tag_pipeline", False), ("stable_tag_pipeline", "true")]:
            value_document = document()
            value_document["gitlab"][key] = value
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                t.policy(value_document, "example/product")

    def test_main_fetches_exact_policy_and_stops_on_failed_review_gate(self):
        raw = p.canonical(document())
        response = {"encoding": "base64", "size": len(raw),
                    "content": base64.b64encode(raw).decode()}
        env = {
            "RC_PROMOTION_GITHUB_TOKEN": "test-release-token", "DSX_RELEASE_MODE": "rc-promotion",
            "GITHUB_REPOSITORY": "example/product", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true", "GITHUB_SHA": COMMIT,
            "RC_VERSION": RC,
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "sys.argv", ["tag_release.py", "--policy", ".gitlab/rc-promotion.json"]
        ), patch.object(p.GitHub, "request", return_value=response) as request, patch.object(
            p.GitHub, "gates", side_effect=p.PromotionError("review missing")
        ) as gate, patch.object(t, "promote") as promote:
            with self.assertRaisesRegex(p.PromotionError, "review missing"):
                t.main()
            request.assert_called_once_with(
                f"repos/example/product/contents/.gitlab/rc-promotion.json?ref={COMMIT}")
            gate.assert_called_once()
            promote.assert_not_called()
            self.assertNotIn("RC_PROMOTION_GITHUB_TOKEN", os.environ)

    def test_main_checks_both_policy_commits_and_keeps_registry_access_unavailable(self):
        github = GitHubFixture()
        env = {
            "RC_PROMOTION_GITHUB_TOKEN": "test-release-token", "DSX_RELEASE_MODE": "rc-promotion",
            "GITHUB_REPOSITORY": "example/product", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true", "GITHUB_SHA": "e" * 40,
            "RC_VERSION": RC,
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "sys.argv", ["tag_release.py", "--policy", ".gitlab/rc-promotion.json"]
        ), patch.object(p, "GitHub", return_value=github), patch.object(
            github, "gates"
        ) as gates, patch.object(github, "request", wraps=github.request) as request, patch.object(
            p, "Registry", side_effect=AssertionError("registry access")
        ), patch.object(p, "authenticate_registries", side_effect=AssertionError("registry auth")), \
                redirect_stdout(io.StringIO()):
            t.main()
            gates.assert_called_once()
            paths = [call.args[0] for call in request.call_args_list]
            for commit in ("e" * 40, COMMIT):
                self.assertIn(f"repos/example/product/contents/.gitlab/rc-promotion.json?ref={commit}", paths)
            self.assertEqual(github.tag("v1.2.3"), COMMIT)

    def test_annotated_tag_write_uses_only_the_scoped_writer_token(self):
        github = p.GitHub("example/product", write_token="test-writer")
        with patch.dict(os.environ, {"GH_TOKEN": "test-reader"}, clear=True), patch.object(
            p, "command", return_value=b"{}"
        ) as command:
            github.request("repos/example/product/git/tags", "POST", {"tag": "v1.2.3"})
            self.assertEqual(command.call_args.kwargs["env"]["GH_TOKEN"], "test-writer")
            self.assertNotIn("test-writer", str(command.call_args.args))


class WorkflowTests(unittest.TestCase):
    def workflow(self):
        return charts.yaml_object((Path(__file__).resolve().parents[3] /
                                   "workflows/promote-rc-tag.yml").read_bytes())

    def test_workflow_only_receives_the_release_app_secret(self):
        workflow = self.workflow()
        self.assertEqual(set(workflow["on"]["workflow_call"]["secrets"]),
                         {"RC_PROMOTION_APP_PRIVATE_KEY"})
        encoded = json.dumps(workflow)
        for tool in ("NGC", "ngc", "docker", "skopeo", "install-helm", "secrets: inherit"):
            self.assertNotIn(tool, encoded)
        self.assertEqual(workflow["jobs"]["tag"]["environment"], "stable-release")

    def test_admission_accepts_only_enabled_protected_default_branch_dispatch(self):
        script = self.workflow()["jobs"]["admission"]["steps"][0]["run"]
        base = {"EVENT": "workflow_dispatch", "REF": "refs/heads/master", "DEFAULT_BRANCH": "master",
                "PROTECTED": "true", "WORKFLOW_SHA": PUBLISHER_SHA, "RELEASE_MODE": "rc-promotion"}
        cases = [({}, True), ({"EVENT": "push"}, False), ({"PROTECTED": "false"}, False),
                 ({"REF": "refs/heads/release/1.2.3"}, False), ({"RELEASE_MODE": ""}, False),
                 ({"WORKFLOW_SHA": "main"}, False)]
        for changes, allowed in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                result = subprocess.run(["bash", "-c", script], capture_output=True, timeout=10,
                                        env={**base, **changes, "GITHUB_OUTPUT": str(output)})
                self.assertEqual(result.returncode == 0, allowed)
                if allowed:
                    self.assertEqual(output.read_text(), "release-mode=rc-promotion\n")


if __name__ == "__main__":
    unittest.main()
