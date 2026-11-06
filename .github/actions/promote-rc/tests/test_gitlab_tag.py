# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gitlab as gl
import promotion as p
import test_gitlab as adapter
from test_gitlab import COMMIT, IMAGE, MASTER, POLICY_API, RC, ROOT, TAG, environment, pipeline


class StableTagTests(unittest.TestCase):
    def setUp(self):
        adapter.AdapterTests.setUp(self)
        self.document["gitlab"].update(authorization="maintainer", stable_tag_pipeline=True,
                                       deploy_group_ids=[], approver_group_ids=[])
        self.config, self.gate = gl.policy(self.document)
        self.client.gate = self.gate
        self.wrapper["producer"]["policy_digest"] = gl.charts.digest(p.canonical(self.document))
        self.responses[POLICY_API + COMMIT] = p.canonical(self.document)
        self.responses[f"{ROOT}/jobs/1001/artifacts/{gl.MANIFEST}"] = p.canonical(self.wrapper)
        self.stable = "v1.2.3"
        self.object_sha = "c" * 40
        self.selection = {
            "schema": 1, "kind": "dsx-rc-tag-promotion", "repository": "example/product",
            "rc_version": RC, "stable_version": "1.2.3", "commit": COMMIT,
            "source_workflow": self.config["source_workflow"], "source_run_id": 12,
            "source_run_attempt": 2, "source_publisher_sha": self.config["source_publisher_sha"],
        }
        self.env = {**environment(), "CI_COMMIT_SHA": COMMIT, "CI_COMMIT_TAG": self.stable,
                    "CI_COMMIT_BRANCH": "", "CI_JOB_MANUAL": "false"}
        self.responses[f"{ROOT}/pipelines/502"] = pipeline(502, self.stable, COMMIT, "running")
        current = self.responses["/job"]
        current.update(name="promote-release", tag=True, ref=self.stable, commit={"id": COMMIT},
                       pipeline=self.responses[f"{ROOT}/pipelines/502"])
        self.responses[f"{ROOT}/repository/tags/{self.stable}"] = {
            "name": self.stable, "protected": True, "target": self.object_sha,
            "commit": {"id": COMMIT}, "message": p.canonical(self.selection).decode(),
        }
        self.protected.update(deploy_access_levels=[{"access_level": 40}], approval_rules=[])
        self.deployment.update(ref=self.stable, sha=COMMIT, deployable=current)
        for user in (55, 66):
            self.responses[f"{ROOT}/members/all/{user}"] = {
                "id": user, "state": "active", "access_level": 40,
            }
        self.responses[self.stable_pipelines] = [self.responses[f"{ROOT}/pipelines/502"]]
        self.responses[f"{ROOT}/pipelines/502/jobs?include_retried=true&per_page=100&page=1"] = [current]
        self.github.pages = Mock(return_value=[{"id": 7, "target": "tag", "enforcement": "active"}])
        self.ruleset = {
            "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
            "rules": [{"type": name} for name in ("creation", "update", "deletion")],
        }
        self.github.request = Mock(side_effect=self.metadata)
        self.github.release = Mock(side_effect=AssertionError("draft release lookup is forbidden"))
        self.github.reserve = Mock(side_effect=AssertionError("GitHub mutation is forbidden"))
        self.github.upload = Mock(side_effect=AssertionError("GitHub mutation is forbidden"))
        self.github.finish = Mock(side_effect=AssertionError("GitHub mutation is forbidden"))
        reader = patch("tag_release.read_tag", side_effect=lambda *_: copy.deepcopy(self.selection))
        reader.start()
        self.addCleanup(reader.stop)

    @property
    def stable_pipelines(self):
        return f"{ROOT}/pipelines?ref={self.stable}&sha={COMMIT}&source=push&per_page=100&page=1"

    def metadata(self, path, **kwargs):
        if path == f"{self.github.root}/git/ref/tags/{self.stable}":
            return {"object": {"type": "tag", "sha": self.object_sha}}
        if path == f"{self.github.root}/rulesets/7":
            return copy.deepcopy(self.ruleset)
        if path == self.github.root:
            return {"default_branch": "master"}
        if path.endswith("/branches/master"):
            return {"protected": True, "commit": {"sha": MASTER}}
        if "/contents/" in path:
            return {"encoding": "base64", "content": base64.b64encode(p.canonical(self.document)).decode()}
        raise AssertionError(path)

    def context(self):
        return self.client.context("promote-tag", self.env, self.github)[0]

    def run_promotion(self, path):
        context = self.context()
        self.client.check_policy(gl.POLICY_PATH, self.document, context, self.github)
        return gl.promote_tag(self.config, self.client, context, self.project, self.env,
                              self.github, self.registry, path)

    def retain(self, path):
        previous = copy.deepcopy(self.responses["/job"])
        previous.update(id=999, status="failed", started_at="2026-11-01T00:00:00Z")
        self.responses[f"{ROOT}/pipelines/502/jobs?include_retried=true&per_page=100&page=1"].append(previous)
        self.responses[f"{ROOT}/jobs/999/artifacts/{gl.RESULT}"] = path.read_bytes()

    def test_automatic_stable_job_keeps_source_rc_and_destination_separate(self):
        context = self.context()
        self.assertEqual(context["ref"], self.stable)
        self.assertEqual(context["tag"], TAG)
        self.assertEqual(context["rc_commit"], COMMIT)
        self.assertTrue(context["tag_pipeline"])
        self.assertEqual(context["tag_object"], self.object_sha)
        self.client.approvals(context, self.project, self.env)

    def test_exact_protected_push_is_required(self):
        for key, value in (("CI_COMMIT_REF_PROTECTED", "false"), ("CI_COMMIT_TAG", TAG),
                           ("CI_COMMIT_TAG", "1.2.3"), ("CI_COMMIT_SHA", MASTER)):
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.client.context("promote-tag", {**self.env, key: value}, self.github)
        for source in ("web", "api", "schedule", "trigger", "pipeline", "merge_request_event"):
            self.responses[f"{ROOT}/pipelines/502"]["source"] = source
            with self.subTest(source=source), self.assertRaises(p.PromotionError):
                self.context()

    def test_no_capability_no_tag_pipeline(self):
        self.gate.pop("stable_tag_pipeline")
        with self.assertRaisesRegex(p.PromotionError, "not enabled"):
            self.context()
        for value in ("true", 1, None):
            self.document["gitlab"]["stable_tag_pipeline"] = value
            with self.subTest(value=value), self.assertRaises(p.PromotionError):
                gl.policy(self.document)

    def test_both_tag_object_and_protection_must_match(self):
        tag = self.responses[f"{ROOT}/repository/tags/{self.stable}"]
        for key, value in (("protected", False), ("target", "e" * 40), ("commit", {"id": MASTER})):
            original = tag[key]
            tag[key] = value
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.context()
            tag[key] = original
        self.ruleset["rules"] = [{"type": "deletion"}]
        with self.assertRaisesRegex(p.PromotionError, "protection"):
            self.context()

    def test_excluded_github_tag_is_not_protected(self):
        self.ruleset["conditions"]["ref_name"]["exclude"] = ["refs/tags/v1.*"]
        with self.assertRaisesRegex(p.PromotionError, "protection"):
            self.context()

    def test_current_default_policy_still_authorizes_stable_tag_code(self):
        current = copy.deepcopy(self.document)
        current["images"] = ["another-service"]
        original = self.metadata
        self.github.request.side_effect = lambda path, **kwargs: (
            {"encoding": "base64", "content": base64.b64encode(p.canonical(current)).decode()}
            if path.endswith(f"ref={MASTER}") else original(path, **kwargs))
        with self.assertRaisesRegex(p.PromotionError, "current protected default"):
            self.client.check_policy(gl.POLICY_PATH, self.document, self.context(), self.github)

    def test_promotes_same_digest_without_any_github_write_or_release_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            result = self.run_promotion(path)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.registry.images["service", "1.2.3"], IMAGE)
            self.assertEqual(json.loads(path.read_text())["tag_object"], self.object_sha)
            self.github.release.assert_not_called()
            self.github.reserve.assert_not_called()
            self.github.upload.assert_not_called()
            self.github.finish.assert_not_called()

    def test_partial_retry_keeps_first_producer_and_does_not_recopy_images(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            self.registry.fail_chart = True
            with self.assertRaises(p.PromotionError):
                self.run_promotion(path)
            self.retain(path)
            self.registry.fail_chart = False
            self.responses[f"{ROOT}/pipelines?ref={TAG}&sha={COMMIT}&source=push&per_page=100&page=1"] = [pipeline(999)]
            self.run_promotion(path)
            self.assertEqual(len([write for write in self.registry.writes if write[0] == "image"]), 1)
            self.assertEqual(json.loads(path.read_text())["intent"]["producer"], self.wrapper["producer"])

    def test_completed_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            self.run_promotion(path)
            self.retain(path)
            writes = list(self.registry.writes)
            self.run_promotion(path)
            self.assertEqual(self.registry.writes, writes)

    def test_conflicting_stable_artifact_is_never_overwritten(self):
        self.registry.images["service", "1.2.3"] = "sha256:" + "e" * 64
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(p.PromotionError, "different digest"):
            self.run_promotion(Path(directory) / gl.RESULT)
        self.assertFalse(self.registry.writes)

    def test_authentication_failure_without_report_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            with self.assertRaisesRegex(p.PromotionError, "registry unavailable"):
                gl.promote_tag(self.config, self.client, self.context(), self.project, self.env,
                               self.github, self.registry, path,
                               authenticate=Mock(side_effect=p.PromotionError("registry unavailable")))
            self.assertFalse(path.exists())
            self.assertFalse(self.registry.writes)
            previous = copy.deepcopy(self.responses["/job"])
            previous.update(id=999, status="failed", started_at="2026-11-01T00:00:00Z",
                            artifacts_file=None)
            self.responses[f"{ROOT}/pipelines/502/jobs?include_retried=true&per_page=100&page=1"].append(previous)
            self.assertEqual(self.run_promotion(path)["status"], "completed")

    def test_existing_but_tampered_prior_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            self.run_promotion(path)
            self.retain(path)
            record = json.loads(path.read_text())
            record["tag_object"] = "e" * 40
            self.responses[f"{ROOT}/jobs/999/artifacts/{gl.RESULT}"] = p.canonical(record)
            with self.assertRaisesRegex(p.PromotionError, "tag evidence differs"):
                self.run_promotion(path)

    def test_source_run_selection_cannot_drift(self):
        self.selection["source_run_attempt"] = 3
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(p.PromotionError, "source selection"):
            self.run_promotion(Path(directory) / gl.RESULT)
        self.assertFalse(self.registry.writes)

    def test_ambiguous_source_producers_are_not_resolved_by_latest(self):
        key = f"{ROOT}/pipelines?ref={TAG}&sha={COMMIT}&source=push&per_page=100&page=1"
        self.responses[key].append(pipeline(503))
        self.responses[f"{ROOT}/pipelines/503/jobs?include_retried=false&per_page=100&page=1"] = [
            {"id": 1003, "name": "verify-rc", "status": "success"},
        ]
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(p.PromotionError, "multiple RC"):
            self.run_promotion(Path(directory) / gl.RESULT)

    def test_automatic_actor_still_needs_maintainer_role(self):
        self.responses[f"{ROOT}/members/all/55"]["access_level"] = 30
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(p.PromotionError, "Maintainers"):
            self.run_promotion(Path(directory) / gl.RESULT)
        self.assertFalse(self.registry.writes)

    def test_cli_ignores_legacy_writer_credentials_and_uses_only_read_auth(self):
        env = {**self.env, "GH_TOKEN": "read-only", "GITLAB_RELEASE_READ_TOKEN": "read-api",
               "CI_JOB_TOKEN": "job-token", "RC_PROMOTION_GITHUB_TOKEN": "unused-writer",
               "RC_PROMOTION_APP_ID": "1", "RC_PROMOTION_APP_INSTALLATION_ID": "2",
               "RC_PROMOTION_APP_PRIVATE_KEY_B64": "unused-key"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(sys, "argv", ["gitlab.py", "promote-tag"]), \
                patch.object(gl.Path, "read_bytes", return_value=p.canonical(self.document)), \
                patch.object(gl, "GitLab", return_value=self.client), \
                patch.object(p, "GitHub", return_value=self.github), \
                patch.object(gl, "installation_token") as token, \
                patch.object(gl, "promote_tag") as promote:
            gl.main()
            promote.assert_called_once()
            token.assert_not_called()
            self.assertEqual(os.environ["GH_TOKEN"], "read-only")
            for key in ("RC_PROMOTION_GITHUB_TOKEN", "RC_PROMOTION_APP_ID",
                        "RC_PROMOTION_APP_INSTALLATION_ID", "RC_PROMOTION_APP_PRIVATE_KEY_B64"):
                self.assertNotIn(key, os.environ)


if __name__ == "__main__":
    unittest.main()
