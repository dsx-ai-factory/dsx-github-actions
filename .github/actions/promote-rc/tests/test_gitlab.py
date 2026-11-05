# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gitlab as gl
import promotion as p
import test_github
from test_promotion import COMMIT, IMAGE, PUBLISHER_SHA, RC, MemoryGitHub, MemoryRegistry, archive, config

MASTER = "f" * 40
API = "https://gitlab.example.com/api/v4"
ROOT = "/projects/123"
TAG = f"v{RC}"
POLICY_API = f"{ROOT}/repository/files/.gitlab%2Frc-promotion.json/raw?ref="


def document():
    return {**config(), "gitlab": {
        "api_url": API, "project_id": 123, "repository": "example/product",
        "deploy_group_ids": [10], "approver_group_ids": [20],
    }}


def pipeline(identity=501, ref=TAG, sha=COMMIT, status="success", source="push"):
    return {"id": identity, "project_id": 123, "ref": ref, "sha": sha,
            "status": status, "source": source, "user": {"id": 55}}


def job(identity=1001, name="verify-rc", status="success", producing=True):
    return {"id": identity, "name": name, "status": status, "tag": producing,
            "ref": TAG if producing else "master", "commit": {"id": COMMIT if producing else MASTER},
            "pipeline": pipeline() if producing else pipeline(502, "master", MASTER, "running", "web"),
            "user": {"id": 66}, "artifacts_file": {"filename": "artifacts.zip", "size": 1024}}


def source_context():
    return {"tag": TAG, "rc_version": RC, "commit": COMMIT, "rc_commit": COMMIT,
            "ref": TAG, "tag_pipeline": True, "pipeline_id": 501, "job_id": 1001,
            "pipeline_user_id": 55, "job_user_id": 66}


def target_context():
    return {**source_context(), "ref": "master", "tag_pipeline": False,
            "commit": MASTER, "pipeline_id": 502, "job_id": 1002}


def environment():
    return {"CI_API_V4_URL": API, "CI_PROJECT_ID": "123", "CI_PIPELINE_ID": "502",
            "CI_JOB_ID": "1002", "CI_COMMIT_SHA": MASTER, "CI_COMMIT_BRANCH": "master",
            "CI_COMMIT_REF_PROTECTED": "true", "RC_VERSION": RC,
            "CI_JOB_MANUAL": "true", "CI_ENVIRONMENT_NAME": "stable-release"}


class FakeGitHub(MemoryGitHub):
    def __init__(self):
        super().__init__()
        self.repository = "example/product"
        self.root = "repos/example/product"
        self.write_token = None
        self.write_environment = p.GitHub.write_environment.__get__(self)
        self.source_run = Mock(return_value=(COMMIT, {"id": 12, "run_attempt": 2}, PUBLISHER_SHA))
        self.intent = p.GitHub.intent
        self.request = Mock(side_effect=self.metadata)

    def metadata(self, path):
        if path == self.root:
            return {"default_branch": "master"}
        if path.endswith("/branches/master"):
            return {"protected": True, "commit": {"sha": MASTER}}
        if "/contents/" in path:
            return {"encoding": "base64", "content": base64.b64encode(p.canonical(document())).decode()}
        raise AssertionError(path)

    def release(self, stable):
        if self.reservation:
            return {"body": p.INTENT_PREFIX + p.canonical(self.reservation).decode() + "```\n"}
        return None


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.document = document()
        self.config, self.gate = gl.policy(self.document)
        self.client = gl.GitLab(self.gate, "read-token", "job-token")
        self.github = FakeGitHub()
        self.registry = MemoryRegistry(archive())
        self.wrapper = gl.verify(self.config, self.document, self.client, source_context(),
                                 self.github, self.registry)
        self.project = {"id": 123, "archived": False, "default_branch": "master",
                        "allow_pipeline_trigger_approve_deployment": False}
        self.protected = {
            "name": "stable-release", "deploy_access_levels": [{"group_id": 10}],
            "approval_rules": [{"group_id": 20, "required_approvals": 1}],
        }
        self.deployment = {
            "id": 99, "environment": {"name": "stable-release"}, "ref": "master", "sha": MASTER,
            "status": "running", "deployable": job(1002, "promote-rc", "running", False),
            "user": {"id": 66}, "pending_approval_count": 0,
            "approval_summary": {"rules": [{"group_id": 20, "required_approvals": 1,
                "deployment_approvals": [{"status": "approved", "user": {"id": 77, "state": "active"}}]}]},
        }
        self.responses = {
            ROOT: self.project,
            f"{ROOT}/repository/branches/master": {"protected": True, "commit": {"id": MASTER}},
            f"{ROOT}/repository/tags/{TAG}": {"name": TAG, "protected": True, "commit": {"id": COMMIT}},
            f"{ROOT}/pipelines/501": pipeline(),
            f"{ROOT}/pipelines/502": pipeline(502, "master", MASTER, "running", "web"),
            "/job": job(1002, "promote-rc", "running", False),
            f"{ROOT}/pipelines?ref={TAG}&sha={COMMIT}&source=push&per_page=100&page=1": [pipeline()],
            f"{ROOT}/pipelines/501/jobs?include_retried=false&per_page=100&page=1": [job()],
            f"{ROOT}/jobs/1001": job(),
            f"{ROOT}/jobs/1001/artifacts/{gl.MANIFEST}": p.canonical(self.wrapper),
            POLICY_API + COMMIT: p.canonical(self.document),
            POLICY_API + MASTER: p.canonical(self.document),
            f"{ROOT}/protected_environments/stable-release": self.protected,
            f"{ROOT}/deployments?environment=stable-release&status=running&order_by=id&sort=desc&per_page=100&page=1": [self.deployment],
            f"{ROOT}/deployments/99": self.deployment,
        }
        self.client.request = Mock(side_effect=lambda path, **kwargs: copy.deepcopy(self.responses[path]))

    def update_artifact(self):
        self.responses[f"{ROOT}/jobs/1001/artifacts/{gl.MANIFEST}"] = p.canonical(self.wrapper)

    def evidence(self):
        return gl.evidence(self.config, self.client, target_context(), self.github, gl.POLICY_PATH)

    def promote(self, path, authenticate=None, write_access=None):
        return gl.promote(self.config, self.client, target_context(), self.project, environment(),
                          self.github, self.registry, path, authenticate=authenticate, write_access=write_access)

    def test_verify_keeps_genuine_github_identity_and_separate_gitlab_producer(self):
        self.assertEqual(self.wrapper["manifest"]["run_id"], 12)
        self.assertEqual(self.wrapper["manifest"]["run_attempt"], 2)
        self.assertEqual(self.wrapper["producer"]["pipeline_id"], 501)
        self.assertEqual(self.wrapper["producer"]["job_id"], 1001)
        self.assertEqual(self.wrapper["producer"]["policy_digest"], gl.charts.digest(p.canonical(self.document)))
        self.assertFalse(self.registry.writes)

    def test_promote_context_requires_current_protected_master_web_or_api_pipeline(self):
        for source in ("web", "api"):
            self.responses[f"{ROOT}/pipelines/502"]["source"] = source
            actual, project = self.client.context("promote", environment())
            self.assertEqual(actual, target_context())
            self.assertEqual(project, self.project)
        self.client.request.assert_any_call("/job", job_token=True)

    def test_verify_context_requires_canonical_protected_tag_push(self):
        env = {**environment(), "CI_PIPELINE_ID": "501", "CI_JOB_ID": "1001",
               "CI_COMMIT_SHA": COMMIT, "CI_COMMIT_TAG": TAG, "CI_COMMIT_BRANCH": ""}
        self.responses[f"{ROOT}/pipelines/501"]["status"] = "running"
        self.responses["/job"] = job(status="running")
        context, _ = self.client.context("verify", env)
        self.assertEqual(context, source_context())
        for source in ("web", "api", "schedule", "trigger", "pipeline", "merge_request_event"):
            with self.subTest(source=source), self.assertRaises(p.PromotionError):
                self.responses[f"{ROOT}/pipelines/501"]["source"] = source
                self.client.context("verify", env)

    def test_context_rejects_forged_or_unprotected_inputs(self):
        for key, value in (("CI_COMMIT_TAG", TAG), ("CI_COMMIT_BRANCH", "release/1.2.3"),
                           ("CI_COMMIT_REF_PROTECTED", "false"), ("CI_API_V4_URL", "https://evil.test/api/v4"),
                           ("CI_PROJECT_ID", "124"), ("CI_JOB_ID", "777"), ("CI_COMMIT_SHA", COMMIT),
                           ("RC_VERSION", "v" + RC), ("RC_VERSION", "1.2.3")):
            with self.subTest(key=key, value=value), self.assertRaises(p.PromotionError):
                self.client.context("promote", {**environment(), key: value})

    def test_promote_rejects_push_schedule_child_or_mr_pipeline(self):
        for source in ("push", "schedule", "pipeline", "parent_pipeline", "trigger", "merge_request_event"):
            self.responses[f"{ROOT}/pipelines/502"]["source"] = source
            with self.subTest(source=source), self.assertRaises(p.PromotionError):
                self.client.context("promote", environment())

    def test_protected_branch_or_tag_drift_is_rejected(self):
        for path, replacement in (
            (f"{ROOT}/repository/branches/master", {"protected": False, "commit": {"id": MASTER}}),
            (f"{ROOT}/repository/branches/master", {"protected": True, "commit": {"id": COMMIT}}),
            (f"{ROOT}/repository/tags/{TAG}", {"name": TAG, "protected": False, "commit": {"id": COMMIT}}),
            (ROOT, {**self.project, "forked_from_project": {"id": 124}}),
            (ROOT, {**self.project, "archived": True}),
        ):
            old = self.responses[path]
            self.responses[path] = replacement
            with self.subTest(path=path), self.assertRaises(p.PromotionError):
                self.client.context("promote", environment())
            self.responses[path] = old

    def test_policy_matches_both_repositories_and_live_default_branch(self):
        self.client.check_policy(gl.POLICY_PATH, self.document, target_context(), self.github)
        self.github.request.assert_any_call(f"{self.github.root}/branches/master")
        self.responses[POLICY_API + MASTER] = p.canonical({**self.document, "images": []})
        with self.assertRaisesRegex(p.PromotionError, "local policy differs"):
            self.client.check_policy(gl.POLICY_PATH, self.document, target_context(), self.github)

    def test_github_default_branch_drift_is_rejected(self):
        self.github.request.side_effect = lambda path: (
            {"protected": True, "commit": {"sha": COMMIT}} if path.endswith("/branches/master")
            else self.github.metadata(path))
        with self.assertRaisesRegex(p.PromotionError, "GitHub default branch changed"):
            self.client.check_policy(gl.POLICY_PATH, self.document, target_context(), self.github)

    def test_evidence_downloads_exact_job_artifact_and_rechecks_exact_github_attempt(self):
        self.assertEqual(self.evidence(), self.wrapper)
        self.client.request.assert_any_call(f"{ROOT}/jobs/1001/artifacts/{gl.MANIFEST}", binary=True)
        self.github.source_run.assert_called_with(self.config, RC, selected={"id": 12, "run_attempt": 2})
        self.assertFalse(any("latest" in call.args[0] for call in self.client.request.call_args_list))

    def test_missing_failed_ambiguous_or_wrong_verify_job_is_rejected(self):
        path = f"{ROOT}/pipelines/501/jobs?include_retried=false&per_page=100&page=1"
        for jobs in ([], [job(status="failed")], [job(), job(identity=1003)], [job(name="other")]):
            self.responses[path] = jobs
            with self.subTest(jobs=jobs), self.assertRaises(p.PromotionError):
                self.evidence()

    def test_authoritative_job_must_match_name_pipeline_project_commit_tag(self):
        for key, value in (("id", 1003), ("name", "other"), ("status", "running"),
                           ("tag", False), ("ref", "master"), ("commit", {"id": MASTER}),
                           ("pipeline", {**pipeline(), "project_id": 124}),
                           ("pipeline", {**pipeline(), "id": 503}), ("erased_at", "yesterday"),
                           ("artifacts_file", {})):
            self.responses[f"{ROOT}/jobs/1001"] = {**job(), key: value}
            with self.subTest(key=key, value=value), self.assertRaises(p.PromotionError):
                self.evidence()

    def test_actual_source_pipeline_rejects_manual_source_or_wrong_sha(self):
        for key, value in (("source", "web"), ("source", "api"), ("sha", MASTER),
                           ("ref", "master"), ("status", "running"), ("project_id", 124)):
            self.responses[f"{ROOT}/pipelines/501"] = {**pipeline(), key: value}
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.evidence()

    def test_missing_artifact_and_auth_errors_fail_closed(self):
        for needle in ("artifacts/", "pipelines?", "jobs/1001"):
            def response(path, **kwargs):
                if needle in path:
                    raise p.PromotionError("GitLab read failed")
                return copy.deepcopy(self.responses[path])
            self.client.request.side_effect = response
            with self.subTest(needle=needle), self.assertRaises(p.PromotionError):
                self.evidence()
        self.assertFalse(self.registry.writes)

    def test_tampered_producer_cannot_be_substituted(self):
        original = copy.deepcopy(self.wrapper["producer"])
        for key, value in (("kind", "github"), ("api_url", "https://other.test/api/v4"),
                           ("job_id", 1003), ("job_name", "other"), ("pipeline_id", 502),
                           ("project_id", 124), ("tag", "v1.2.4-rc.1"), ("commit", MASTER),
                           ("policy_digest", "sha256:" + "0" * 64)):
            self.wrapper["producer"] = {**original, key: value}
            self.update_artifact()
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.evidence()

    def test_manifest_identity_inventory_and_publisher_must_match(self):
        original = copy.deepcopy(self.wrapper)
        for key, value in (("commit", MASTER), ("repository", "evil/fork"),
                           ("source_workflow", ".github/workflows/other.yml"), ("run_id", 999),
                           ("run_attempt", 999), ("images", []), ("charts", []),
                           ("source_ngc_path", "other/team")):
            self.wrapper = copy.deepcopy(original)
            self.wrapper["manifest"][key] = value
            self.update_artifact()
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.evidence()
        self.wrapper = copy.deepcopy(original)
        self.wrapper["source_publisher_sha"] = MASTER
        self.update_artifact()
        with self.assertRaises(p.PromotionError):
            self.evidence()

    def test_source_policy_groups_can_be_empty_before_master_promotion_setup(self):
        old_document = copy.deepcopy(self.document)
        old_document["gitlab"]["deploy_group_ids"] = []
        old_document["gitlab"]["approver_group_ids"] = []
        self.responses[POLICY_API + COMMIT] = p.canonical(old_document)
        self.wrapper["producer"] = self.client.producer(source_context(), old_document, 1001)
        self.update_artifact()
        self.assertEqual(self.evidence(), self.wrapper)

    def test_current_master_policy_still_controls_allowed_inventory_and_publishers(self):
        self.config["images"].append("not-verified")
        with self.assertRaisesRegex(p.PromotionError, "inventory"):
            self.evidence()

    def test_source_run_does_not_download_a_github_artifact_or_read_stable_reservations(self):
        fixture = test_github.EvidenceTests()
        fixture.setUp()
        commit, run, publisher = fixture.github.source_run(config(), RC)
        self.assertEqual(commit, COMMIT)
        self.assertEqual(publisher, PUBLISHER_SHA)
        self.assertEqual(run["id"], 12)
        fixture.github.release.assert_not_called()
        self.assertFalse(any("artifacts" in call.args[0]
                             for call in fixture.github.request.call_args_list))
        self.assertFalse(any("artifacts" in call.args[0]
                             for call in fixture.github.pages.call_args_list))

    def test_source_run_rejects_invented_ids_and_unsuccessful_exact_attempt(self):
        fixture = test_github.EvidenceTests()
        fixture.setUp()
        for selected in ({"id": True, "run_attempt": 1}, {"id": 12, "run_attempt": 0},
                         {"id": "12", "run_attempt": 1}):
            with self.subTest(selected=selected), self.assertRaises(p.PromotionError):
                fixture.github.source_run(config(), RC, selected=selected)
        fixture.run["conclusion"] = "failure"
        with self.assertRaises(p.PromotionError):
            fixture.github.source_run(config(), RC, selected={"id": 12, "run_attempt": 1})

    def test_changed_github_tag_or_unsuccessful_run_stops_before_registry_login(self):
        auth = Mock()
        self.github.source_run.return_value = (MASTER, {"id": 12, "run_attempt": 2}, PUBLISHER_SHA)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(p.PromotionError):
            self.promote(Path(directory) / gl.RESULT, authenticate=auth)
        auth.assert_not_called()
        self.github.source_run.side_effect = p.PromotionError("no successful RC run")
        with self.assertRaises(p.PromotionError):
            gl.verify(self.config, self.document, self.client, source_context(), self.github,
                      self.registry, authenticate=auth)
        auth.assert_not_called()

    def test_exact_independent_group_approval_uses_project_scoped_evidence_only(self):
        result = self.client.approvals(target_context(), self.project, environment())
        self.assertEqual(result, {"deployment_id": 99, "pipeline_id": 502, "job_id": 1002, "approved_by": [77]})
        self.assertFalse(any(call.args[0].startswith("/groups/") for call in self.client.request.call_args_list))

    def test_independent_approver_can_also_run_the_manual_job(self):
        context = {**target_context(), "job_user_id": 77}
        self.deployment["user"] = {"id": 77}
        result = self.client.approvals(context, self.project, environment())
        self.assertEqual(result["approved_by"], [77])

    def test_missing_pipeline_triggerer_is_rejected(self):
        with self.assertRaises(p.PromotionError):
            self.client.approvals({**target_context(), "pipeline_user_id": None},
                                  self.project, environment())

    def test_empty_groups_allow_rc_verification_but_fail_closed_before_promotion(self):
        candidate = document()
        candidate["gitlab"]["deploy_group_ids"] = []
        candidate["gitlab"]["approver_group_ids"] = []
        core, gate = gl.policy(candidate)
        client = gl.GitLab(gate, "read-token", "job-token")
        proof = gl.verify(core, candidate, client, source_context(), self.github, self.registry)
        self.assertEqual(proof["manifest"]["run_id"], 12)
        client.request = Mock()
        with self.assertRaisesRegex(p.PromotionError, "stable promotion is not configured"):
            client.approvals(target_context(), self.project, environment())
        client.request.assert_not_called()

    def test_missing_protected_environment_or_deployment_access_fails_closed(self):
        for needle in ("protected_environments", "/deployments?"):
            def response(path, **kwargs):
                if needle in path:
                    raise p.PromotionError("GitLab read failed")
                return copy.deepcopy(self.responses[path])
            self.client.request.side_effect = response
            with self.subTest(needle=needle), self.assertRaises(p.PromotionError):
                self.client.approvals(target_context(), self.project, environment())

    def test_manual_and_environment_and_no_self_approval_are_required(self):
        for key, value in (("CI_JOB_MANUAL", "false"), ("CI_ENVIRONMENT_NAME", "test")):
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.client.approvals(target_context(), self.project, {**environment(), key: value})
        for value in (True, None):
            with self.assertRaises(p.PromotionError):
                self.client.approvals(target_context(), {**self.project,
                    "allow_pipeline_trigger_approve_deployment": value}, environment())

    def test_broad_users_roles_or_nonallowlisted_groups_are_rejected(self):
        original = copy.deepcopy(self.protected)
        for key, value in (("deploy_access_levels", []), ("deploy_access_levels", [{"access_level": 40}]),
                           ("deploy_access_levels", [{"user_id": 66}]),
                           ("approval_rules", []), ("approval_rules", [{"group_id": 21, "required_approvals": 1}]),
                           ("approval_rules", [{"group_id": 20, "required_approvals": 0}])):
            self.responses[f"{ROOT}/protected_environments/stable-release"] = {**original, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(p.PromotionError):
                self.client.approvals(target_context(), self.project, environment())

    def test_wrong_deployment_or_pending_approvals_rejected(self):
        original = copy.deepcopy(self.deployment)
        for key, value in (("id", 98), ("environment", {"name": "test"}), ("ref", TAG),
                           ("sha", COMMIT), ("status", "blocked"), ("pending_approval_count", 1),
                           ("deployable", job(1003)), ("approval_summary", {})):
            self.responses[f"{ROOT}/deployments/99"] = {**original, key: value}
            with self.subTest(key=key), self.assertRaises(p.PromotionError):
                self.client.approvals(target_context(), self.project, environment())

    def test_missing_self_rejected_or_duplicate_approvals_rejected(self):
        rule = self.deployment["approval_summary"]["rules"][0]
        approval = copy.deepcopy(rule["deployment_approvals"][0])
        for approvals in ([], [approval, approval], [{**approval, "status": "rejected"}],
                          [{**approval, "user": {"id": 55, "state": "active"}}],
                          [{**approval, "user": {"id": 77, "state": "blocked"}}]):
            rule["deployment_approvals"] = approvals
            with self.subTest(approvals=approvals), self.assertRaises(p.PromotionError):
                self.client.approvals(target_context(), self.project, environment())

    def test_no_admin_or_unknown_group_approval_bypass(self):
        self.deployment["approval_summary"]["rules"][0]["group_id"] = 999
        with self.assertRaises(p.PromotionError):
            self.client.approvals(target_context(), self.project, environment())
        self.deployment["approval_summary"]["rules"] = []
        with self.assertRaises(p.PromotionError):
            self.client.approvals(target_context(), self.project, environment())

    def test_approval_configuration_drift_rejected(self):
        self.protected["approval_rules"][0]["required_approvals"] = 2
        with self.assertRaisesRegex(p.PromotionError, "requirements changed"):
            self.client.approvals(target_context(), self.project, environment())

    def test_promotion_uses_existing_engine_same_digests_and_records_gitlab_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            result = self.promote(path)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(self.github.finished)
            self.assertEqual(self.registry.images["service", "1.2.3"], IMAGE)
            self.assertEqual(result["intent"]["source_run_id"], 12)
            self.assertEqual(result["intent"]["source_run_attempt"], 2)
            self.assertEqual(result["intent"]["producer"], self.wrapper["producer"])
            self.assertEqual(json.loads(path.read_text())["gitlab_approval"]["approved_by"], [77])

    def test_partial_copy_retry_reuses_original_producer_even_if_new_pipeline_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            self.registry.fail_chart = True
            with self.assertRaises(p.PromotionError):
                self.promote(path)
            self.assertFalse(self.github.finished)
            self.assertEqual(json.loads(path.read_text())["status"], "partial")
            self.registry.fail_chart = False
            list_path = f"{ROOT}/pipelines?ref={TAG}&sha={COMMIT}&source=push&per_page=100&page=1"
            self.responses[list_path] = [pipeline(999)]
            self.client.request.reset_mock()
            result = self.promote(path)
            self.assertEqual(result["status"], "completed")
            self.assertFalse(any(call.args[0] == list_path for call in self.client.request.call_args_list))
            self.assertEqual(len([write for write in self.registry.writes if write[0] == "image"]), 1)

    def test_conflicting_stable_image_or_tampered_manifest_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            self.registry.images["service", "1.2.3"] = "sha256:" + "e" * 64
            with self.assertRaisesRegex(p.PromotionError, "different digest"):
                self.promote(path)
            self.assertFalse(self.registry.writes)
            self.registry.images.clear()
            self.wrapper["manifest"]["images"][0]["digest"] = "sha256:" + "e" * 64
            self.update_artifact()
            with self.assertRaisesRegex(p.PromotionError, "changed after verification"):
                self.promote(path)
            self.assertFalse(self.registry.writes)
            self.assertIsNone(self.github.reservation)

    def test_registry_errors_do_not_create_stable_tags_or_releases(self):
        self.registry.fail_read = True
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(p.PromotionError):
            self.promote(Path(directory) / gl.RESULT)
        self.assertIsNone(self.github.reservation)
        self.assertFalse(self.github.finished)

    def test_app_write_token_is_minted_only_after_approvals_and_revoked_on_failure(self):
        events = []

        @contextmanager
        def write_access():
            events.append("mint-write")
            try:
                yield "short-lived-write-token"
            finally:
                events.append("revoke-write")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / gl.RESULT
            self.project["allow_pipeline_trigger_approve_deployment"] = True
            with self.assertRaises(p.PromotionError):
                self.promote(path, write_access=write_access)
            self.assertEqual(events, [])
            self.project["allow_pipeline_trigger_approve_deployment"] = False
            self.registry.fail_chart = True
            with self.assertRaises(p.PromotionError):
                self.promote(path, write_access=write_access)
            self.assertEqual(events, ["mint-write", "revoke-write"])
            self.assertIsNone(self.github.write_token)

    def test_cli_optional_app_auth_revokes_tokens_and_removes_key_from_subprocess_environment(self):
        original_directory = os.getcwd()
        for operation in ("verify", "promote"):
            events = []

            @contextmanager
            def token(repository, mode, **kwargs):
                self.assertEqual(repository, "example/product")
                self.assertEqual(kwargs["private_key"], "private-key-not-for-child-processes")
                self.assertNotIn("RC_PROMOTION_APP_PRIVATE_KEY", os.environ)
                self.assertNotIn("GITLAB_RELEASE_READ_TOKEN", os.environ)
                self.assertNotIn("CI_JOB_TOKEN", os.environ)
                events.append("mint-" + mode)
                try:
                    yield "temporary-" + mode
                finally:
                    events.append("revoke-" + mode)

            env = {**environment(), "PATH": os.environ["PATH"],
                   "GITLAB_RELEASE_READ_TOKEN": "read-token", "CI_JOB_TOKEN": "job-token",
                   "RC_PROMOTION_APP_ID": "1",
                   "RC_PROMOTION_APP_INSTALLATION_ID": "2",
                   "RC_PROMOTION_APP_PRIVATE_KEY": "private-key-not-for-child-processes"}
            context = source_context() if operation == "verify" else target_context()
            self.client.context = Mock(return_value=(context, self.project))
            self.client.check_policy = Mock()
            try:
                with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, env, clear=True):
                    os.chdir(directory)
                    Path(".gitlab").mkdir()
                    Path(gl.POLICY_PATH).write_bytes(p.canonical(self.document))
                    with patch.object(sys, "argv", ["gitlab.py", operation, "--policy", gl.POLICY_PATH]), \
                            patch.object(gl, "GitLab", return_value=self.client), \
                            patch.object(p, "GitHub", return_value=self.github), \
                            patch.object(p, "Registry", return_value=self.registry), \
                            patch.object(p, "authenticate_registries"), \
                            patch.object(gl, "installation_token", side_effect=token):
                        gl.main()
                    self.assertNotIn("GH_TOKEN", os.environ)
                    self.assertFalse(Path(os.environ["PROMOTION_AUTH_ROOT"]).exists())
                    self.assertTrue(Path(gl.MANIFEST if operation == "verify" else gl.RESULT).exists())
            finally:
                os.chdir(original_directory)
            expected = (["mint-read", "revoke-read"] if operation == "verify" else
                        ["mint-read", "mint-write", "revoke-write", "revoke-read"])
            self.assertEqual(events, expected)

    @contextmanager
    def cli(self, operation, credentials):
        context = source_context() if operation == "verify" else target_context()
        self.client.context = Mock(return_value=(context, self.project))
        self.client.check_policy = Mock()
        original_directory = os.getcwd()
        env = {**environment(), "PATH": os.environ["PATH"],
               "GITLAB_RELEASE_READ_TOKEN": "read-token", "CI_JOB_TOKEN": "job-token",
               **credentials}
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, env, clear=True):
                os.chdir(directory)
                Path(".gitlab").mkdir()
                Path(gl.POLICY_PATH).write_bytes(p.canonical(self.document))
                with patch.object(sys, "argv", ["gitlab.py", operation]), \
                        patch.object(gl, "GitLab", return_value=self.client), \
                        patch.object(p, "GitHub", return_value=self.github) as github, \
                        patch.object(p, "Registry", return_value=self.registry), \
                        patch.object(p, "authenticate_registries"):
                    yield github
        finally:
            os.chdir(original_directory)

    def test_separate_verification_app_works_without_writer_and_takes_read_precedence(self):
        for source in ("PRIVATE_KEY", "PRIVATE_KEY_FILE"):
            for writer_present in (False, True):
                credentials = {"GH_TOKEN": "original-read", "RC_VERIFICATION_APP_ID": "3",
                               "RC_VERIFICATION_APP_INSTALLATION_ID": "4",
                               f"RC_VERIFICATION_APP_{source}": "reader-key-source"}
                if writer_present:
                    credentials.update({"RC_PROMOTION_APP_ID": "1", "RC_PROMOTION_APP_INSTALLATION_ID": "2",
                                        "RC_PROMOTION_APP_PRIVATE_KEY_FILE": "writer-key-file"})
                events = []

                @contextmanager
                def token(repository, mode, **kwargs):
                    self.assertEqual(repository, "example/product")
                    self.assertEqual(mode, "read")
                    self.assertEqual(kwargs, {"app_id": "3", "installation_id": "4",
                                             "private_key": "reader-key-source" if source == "PRIVATE_KEY" else None,
                                             "private_key_file": "reader-key-source" if source == "PRIVATE_KEY_FILE" else None})
                    self.assertFalse(any("_APP_" in key for key in os.environ))
                    self.assertIsNone(self.github.write_token)
                    events.append("read")
                    try:
                        yield "reader-token"
                    finally:
                        events.append("revoke-read")

                def check_policy(*args):
                    self.assertEqual(os.environ["GH_TOKEN"], "reader-token")

                with self.subTest(source=source, writer_present=writer_present), \
                        self.cli("verify", credentials) as github, \
                        patch.object(gl, "installation_token", side_effect=token):
                    self.client.check_policy.side_effect = check_policy
                    gl.main()
                    github.assert_called_once_with("example/product")
                    self.assertEqual(events, ["read", "revoke-read"])
                    self.assertEqual(os.environ["GH_TOKEN"], "original-read")
                    self.assertFalse(Path(os.environ["PROMOTION_AUTH_ROOT"]).exists())
                    self.assertTrue(Path(gl.MANIFEST).exists())

    def test_separate_apps_promote_after_gates_and_clean_up_on_failure(self):
        credentials = {"RC_VERIFICATION_APP_ID": "3", "RC_VERIFICATION_APP_INSTALLATION_ID": "4",
                       "RC_VERIFICATION_APP_PRIVATE_KEY_FILE": "reader-key-file",
                       "RC_PROMOTION_APP_ID": "1", "RC_PROMOTION_APP_INSTALLATION_ID": "2",
                       "RC_PROMOTION_APP_PRIVATE_KEY_FILE": "writer-key-file"}
        events = []
        approvals = self.client.approvals

        def approve(*args):
            result = approvals(*args)
            events.append("approved")
            return result

        self.client.approvals = Mock(side_effect=approve)

        @contextmanager
        def token(repository, mode, **kwargs):
            self.assertEqual(repository, "example/product")
            if mode == "write":
                self.assertIn("approved", events)
                self.assertEqual(os.environ["GH_TOKEN"], "reader-token")
            self.assertEqual(kwargs, {"app_id": "3" if mode == "read" else "1",
                                     "installation_id": "4" if mode == "read" else "2",
                                     "private_key": None,
                                     "private_key_file": "reader-key-file" if mode == "read" else "writer-key-file"})
            self.assertFalse(any("_APP_" in key for key in os.environ))
            events.append("mint-" + mode)
            try:
                yield "reader-token" if mode == "read" else "writer-token"
            finally:
                events.append("revoke-" + mode)

        for failure in ("approval", "copy", None):
            events.clear()
            self.project["allow_pipeline_trigger_approve_deployment"] = failure == "approval"
            self.registry.fail_chart = failure == "copy"
            with self.subTest(failure=failure), self.cli("promote", credentials), \
                    patch.object(gl, "installation_token", side_effect=token):
                if failure:
                    with self.assertRaises(p.PromotionError):
                        gl.main()
                else:
                    gl.main()
                    self.assertTrue(self.github.finished)
                self.assertNotIn("GH_TOKEN", os.environ)
                self.assertIsNone(self.github.write_token)
                self.assertFalse(Path(os.environ["PROMOTION_AUTH_ROOT"]).exists())
                self.assertEqual(events, ["mint-read", "revoke-read"] if failure == "approval" else
                                 ["mint-read", "approved", "mint-write", "revoke-write", "revoke-read"])

    def test_gh_token_read_compatibility_does_not_ask_writer_app_for_actions(self):
        credentials = {"GH_TOKEN": "approved-read-token", "RC_PROMOTION_APP_ID": "1",
                       "RC_PROMOTION_APP_INSTALLATION_ID": "2",
                       "RC_PROMOTION_APP_PRIVATE_KEY_FILE": "writer-key-file"}
        with self.cli("verify", credentials), patch.object(gl, "installation_token") as mint:
            gl.main()
            mint.assert_not_called()
            self.assertEqual(os.environ["GH_TOKEN"], "approved-read-token")

    def test_dedicated_write_token_compatibility_stays_behind_approval(self):
        credentials = {"GH_TOKEN": "approved-read-token", "RC_PROMOTION_GITHUB_TOKEN": "dedicated-writer"}
        promote = p.promote

        def checked_promote(*args, **kwargs):
            self.assertEqual(self.github.write_token, "dedicated-writer")
            self.assertEqual(os.environ["GH_TOKEN"], "approved-read-token")
            self.assertNotIn("RC_PROMOTION_GITHUB_TOKEN", os.environ)
            return promote(*args, **kwargs)

        with self.cli("promote", credentials) as github, \
                patch.object(gl, "installation_token") as mint, \
                patch.object(p, "promote", side_effect=checked_promote):
            gl.main()
            mint.assert_not_called()
            github.assert_called_once_with("example/product")
            self.assertTrue(self.github.finished)
            self.assertIsNone(self.github.write_token)

    def test_partial_or_mixed_app_credentials_never_fall_back_or_mint(self):
        reader = {"RC_VERIFICATION_APP_ID": "3", "RC_VERIFICATION_APP_INSTALLATION_ID": "4",
                  "RC_VERIFICATION_APP_PRIVATE_KEY_FILE": "reader-key-file"}
        writer = {"RC_PROMOTION_APP_ID": "1", "RC_PROMOTION_APP_INSTALLATION_ID": "2",
                  "RC_PROMOTION_APP_PRIVATE_KEY_FILE": "writer-key-file"}
        invalid = [{**reader, "RC_VERIFICATION_APP_PRIVATE_KEY": "also-text"},
                   {**reader, "RC_VERIFICATION_APP_PRIVATE_KEY_FILE": ""},
                   {**reader, "RC_VERIFICATION_APP_ID": "invalid"},
                   {**writer, "RC_VERIFICATION_APP_ID": "3"},
                   {**writer, "RC_PROMOTION_APP_PRIVATE_KEY": "also-text"},
                   {**writer, "RC_PROMOTION_GITHUB_TOKEN": "ambiguous-writer"}]
        for complete in (reader, writer):
            invalid.extend({key: value for key, value in complete.items() if key != missing}
                           for missing in complete)
        for credentials in invalid:
            with self.subTest(fields=list(credentials)), \
                    self.cli("verify", {"GH_TOKEN": "must-not-hide-misconfiguration", **credentials}), \
                    patch.object(gl, "installation_token") as mint, self.assertRaises(p.PromotionError):
                try:
                    gl.main()
                finally:
                    mint.assert_not_called()
                    self.client.context.assert_not_called()


class PolicyAndTransportTests(unittest.TestCase):
    def test_policy_accepts_generic_repository_not_product_specific_version(self):
        core, gate = gl.policy(document())
        self.assertEqual(core, config())
        self.assertEqual(gate["repository"], "example/product")

    def test_no_missing_wildcard_duplicate_or_unbounded_groups(self):
        for field in ("deploy_group_ids", "approver_group_ids"):
            for value in ([True], [0], [-1], [1, 1], ["all"], list(range(1, 18))):
                candidate = document()
                candidate["gitlab"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(p.PromotionError):
                    gl.policy(candidate)

    def test_invalid_api_origin_is_rejected(self):
        for url in ("http://gitlab.test/api/v4", "https://token@gitlab.test/api/v4",
                    "https://gitlab.test/api/v4?token=x", "https://gitlab.test/api/v4#x",
                    "https://gitlab.test/api/v4/", "https://gitlab.test/not-api"):
            candidate = document()
            candidate["gitlab"]["api_url"] = url
            with self.subTest(url=url), self.assertRaises(p.PromotionError):
                gl.policy(candidate)

    def test_json_duplicate_invalid_or_oversized_fails_closed(self):
        for raw in (b'{"schema":1,"schema":2}', b'{', b'x' * (gl.MAX_BYTES + 1)):
            with self.assertRaises(p.PromotionError):
                gl.decode(raw)

    def test_http_auth_failures_are_not_missing_artifacts(self):
        client = gl.GitLab(document()["gitlab"], "read-secret", "job-secret")
        for code in (401, 403, 404, 500):
            client.artifact_opener.open = Mock(side_effect=urllib.error.HTTPError(
                API, code, "server body with secret", {}, None))
            with self.subTest(code=code), self.assertRaisesRegex(p.PromotionError, "GitLab read failed") as error:
                client.request(f"{ROOT}/jobs/1001/artifacts/{gl.MANIFEST}", binary=True)
            self.assertNotIn("secret", str(error.exception))

    def test_credentials_never_follow_redirects(self):
        with self.assertRaisesRegex(p.PromotionError, "redirect refused"):
            gl.NoRedirect().redirect_request(None, None, 302, None, None, "https://other.test")

    def test_artifact_redirect_to_exact_allowed_https_host_drops_all_authentication(self):
        handler = gl.ArtifactRedirect(API, ["artifacts.example.com"])
        original = urllib.request.Request(API + ROOT + "/jobs/1001/artifacts/" + gl.MANIFEST, headers={
            "PRIVATE-TOKEN": "read-secret", "JOB-TOKEN": "job-secret", "Cookie": "session=secret",
            "Authorization": "Bearer secret", "Referer": "secret",
        })
        for target in ("https://artifacts.example.com/object?signature=temporary",
                       "https://gitlab.example.com/storage/object?signature=temporary"):
            redirected = handler.redirect_request(original, None, 302, None, None, target)
            self.assertEqual(redirected.full_url, target)
            self.assertEqual(redirected.header_items(), [])
            self.assertEqual(redirected.get_method(), "GET")

    def test_artifact_redirect_rejects_unapproved_hosts_downgrade_userinfo_and_ports(self):
        handler = gl.ArtifactRedirect(API, ["artifacts.example.com"])
        for target in ("https://evil.example.com/object", "http://artifacts.example.com/object",
                       "https://token@artifacts.example.com/object", "https://artifacts.example.com:8080/object",
                       "https://artifacts.example.com.evil.test/object", "https://127.0.0.1/object",
                       "https://artifacts.example.com/object#fragment"):
            with self.subTest(target=target), self.assertRaises(p.PromotionError):
                handler.redirect_request(None, None, 302, None, None, target)
        self.assertEqual(handler.max_redirections, 3)

    def test_artifact_host_policy_does_not_accept_wildcards_urls_or_ips(self):
        for host in ("*.example.com", "https://example.com", "127.0.0.1", "example.com:443", "user@example.com"):
            candidate = document()
            candidate["gitlab"]["artifact_download_hosts"] = [host]
            with self.subTest(host=host), self.assertRaises(p.PromotionError):
                gl.policy(candidate)

    def test_only_exact_manifest_endpoint_can_use_artifact_redirect_handler(self):
        client = gl.GitLab(document()["gitlab"], "read-secret", "job-secret")
        response = Mock(status=200)
        response.read.return_value = b'{}'
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        client.opener.open = Mock(return_value=manager)
        client.artifact_opener.open = Mock(return_value=manager)
        client.request(f"{ROOT}/jobs/1001/artifacts/{gl.MANIFEST}", binary=True)
        client.artifact_opener.open.assert_called_once()
        client.opener.open.assert_not_called()
        client.request(POLICY_API + MASTER, binary=True)
        client.opener.open.assert_called_once()
        self.assertEqual(client.artifact_opener.open.call_count, 1)

    def test_api_paths_cannot_escape_reviewed_project_or_groups(self):
        client = gl.GitLab(document()["gitlab"], "read-secret", "job-secret")
        for path in ("/projects/124/jobs/1", "/groups/99/members/77", "/users", "/job",
                     ROOT + "/../variables", ROOT + "/jobs/1#fragment"):
            with self.subTest(path=path), self.assertRaises(p.PromotionError):
                client.request(path)

    def test_request_uses_job_token_only_for_current_job(self):
        client = gl.GitLab(document()["gitlab"], "read-secret", "job-secret")
        response = Mock(status=200)
        response.read.return_value = b'{}'
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        client.opener.open = Mock(return_value=manager)
        client.request("/job", job_token=True)
        headers = dict(client.opener.open.call_args.args[0].header_items())
        self.assertEqual(headers, {"Job-token": "job-secret"})
        client.request(ROOT)
        headers = dict(client.opener.open.call_args.args[0].header_items())
        self.assertEqual(headers, {"Private-token": "read-secret"})


if __name__ == "__main__":
    unittest.main()
