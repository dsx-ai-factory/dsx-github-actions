# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import zipfile

from test_promotion import COMMIT, PUBLISHER_SHA, RC, archive, charts, config, evidence, p


class AuthenticationTests(unittest.TestCase):
    write_token = "test-app-token"
    auth_mode = "app"

    def setUp(self):
        self.github = p.GitHub("example/product", write_token=self.write_token)

    def test_source_and_protection_reads_keep_the_normal_workflow_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "test-workflow-token"}, clear=True), \
                patch.object(p, "command", return_value=b"{}") as command:
            for path in ("actions/runs/12", f"releases/tags/v{RC}", "environments/stable-release",
                         "git/ref/tags/v1.2.3", "contents/.github/rc-promotion.json",
                         "tags", f"commits?sha={COMMIT}", f"compare/{'b' * 40}...{COMMIT}"):
                with self.subTest(path=path):
                    self.github.request(f"repos/example/product/{path}")
                    self.assertIsNone(command.call_args.kwargs["env"])
            self.assertEqual(os.environ["GH_TOKEN"], "test-workflow-token")

    def test_destination_draft_and_intent_reads_keep_write_token_visibility_for_retries(self):
        with patch.object(p, "command", return_value=b"{}") as command:
            for path in ("releases?per_page=100&page=1", "releases/1", "releases/1/assets?page=1"):
                with self.subTest(path=path):
                    self.github.request(f"repos/example/product/{path}")
                    self.assertEqual(command.call_args.kwargs["env"]["GH_TOKEN"], self.write_token)
            self.github.pages = Mock(return_value=[{"name": "intent.json", "id": 2, "size": 2}])
            self.github.read_asset({"id": 1}, "intent.json")
            self.assertEqual(command.call_args.kwargs["env"]["GH_TOKEN"], self.write_token)

    def test_existing_draft_reservation_is_reused_with_write_authenticated_reads(self):
        intent = {"rc_version": RC, "commit": COMMIT}
        draft = {
            "id": 1, "tag_name": "v1.2.3", "prerelease": False, "draft": True,
            "body": "Verified RC promotion intent.\n\n```json\n" + p.canonical(intent).decode() + "```\n",
        }
        self.github.tag = Mock(return_value=None)

        def response(args, data=None, **kwargs):
            self.assertIsNone(data)
            self.assertEqual(kwargs["env"]["GH_TOKEN"], self.write_token)
            if "Accept: application/octet-stream" in args:
                return p.canonical(intent)
            self.assertEqual(args[args.index("--method") + 1], "GET")
            if "/releases/1/assets?" in args[-1]:
                return p.canonical([{"id": 2, "name": "promotion-intent.json", "size": 100}])
            self.assertIn("/releases?", args[-1])
            return p.canonical([draft])

        with patch.object(p, "command", side_effect=response) as command:
            self.assertEqual(self.github.reserve("1.2.3", COMMIT, intent), draft)
            self.assertEqual(command.call_count, 3)

    def test_only_release_ref_and_asset_writes_receive_the_write_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "test-workflow-token"}, clear=True), \
                patch.object(p, "command", return_value=b"{}") as command:
            for path, method in [("releases", "POST"), ("releases/1", "PATCH"),
                                 ("git/refs", "POST")]:
                with self.subTest(path=path):
                    self.github.request(f"repos/example/product/{path}", method, {})
                    self.assertEqual(command.call_args.kwargs["env"]["GH_TOKEN"], self.write_token)
                    self.assertNotIn(self.write_token, str(command.call_args.args))
            self.github.upload({"tag_name": "v1.2.3"}, "intent.json", {})
            self.assertEqual(command.call_args.kwargs["env"]["GH_TOKEN"], self.write_token)
            self.assertNotIn(self.write_token, str(command.call_args.args))
            self.assertEqual(os.environ["GH_TOKEN"], "test-workflow-token")

    def test_missing_write_token_never_falls_back_to_ambient_credentials(self):
        env = {"GH_TOKEN": "test-workflow-token", "GITHUB_TOKEN": "test-other-token",
               "RC_PROMOTION_AUTH_MODE": self.auth_mode, "RC_PROMOTION_PAT": "test-unselected-pat"}
        with patch.dict(os.environ, env, clear=True), patch.object(p, "command") as command:
            for token in (None, "", " "):
                github = p.GitHub("example/product", write_token=token)
                for path, method in [("releases", "POST"), ("releases/1", "PATCH"),
                                     ("git/refs", "POST")]:
                    with self.subTest(token=token, path=path), self.assertRaisesRegex(
                        p.PromotionError, "^the environment-scoped release token is missing$"
                    ):
                        github.request(f"repos/example/product/{path}", method, {})
                with self.assertRaisesRegex(p.PromotionError, "^the environment-scoped release token is missing$"):
                    github.upload({"tag_name": "v1.2.3"}, "intent.json", {})
            command.assert_not_called()

    def test_other_repository_and_unrelated_mutations_are_rejected(self):
        with patch.object(p, "command") as command:
            for path, method in [("repos/other/product/releases", "POST"),
                                 ("repos/example/product/contents/file", "PUT"),
                                 ("repos/example/product/releases/1", "DELETE"),
                                 ("repos/example/product/actions/workflows/ci.yml/dispatches", "POST")]:
                with self.subTest(path=path), self.assertRaises(p.PromotionError):
                    self.github.request(path, method, {})
            command.assert_not_called()

    def test_missing_selected_token_stops_main_before_evidence_or_registry_access(self):
        env = {"DSX_RELEASE_MODE": "rc-promotion", "GITHUB_REPOSITORY": "example/product",
               "GH_TOKEN": "test-workflow-token", "GITHUB_TOKEN": "test-other-token",
               "RC_PROMOTION_AUTH_MODE": self.auth_mode, "RC_PROMOTION_GITHUB_TOKEN": ""}
        with patch.dict(os.environ, env, clear=True), \
                patch("sys.argv", ["promotion.py", "promote"]), \
                patch.object(Path, "read_text", return_value=json.dumps(config())), \
                patch.object(p.GitHub, "gates") as gates, \
                patch.object(p.GitHub, "evidence") as evidence, \
                patch.object(p, "authenticate_registries") as auth, \
                patch.object(p, "command") as command:
            with self.assertRaisesRegex(p.PromotionError, "^the environment-scoped release token is missing$"):
                p.main()
            gates.assert_not_called()
            evidence.assert_not_called()
            auth.assert_not_called()
            command.assert_not_called()

    def test_main_does_not_leak_write_token_to_read_or_registry_tool_environment(self):
        env = {
            "DSX_RELEASE_MODE": "rc-promotion", "GITHUB_REPOSITORY": "example/product",
            "GH_TOKEN": "test-workflow-token", "RC_PROMOTION_GITHUB_TOKEN": self.write_token,
            "RC_PROMOTION_AUTH_MODE": self.auth_mode,
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": COMMIT, "GITHUB_REF_PROTECTED": "true", "RC_VERSION": RC,
            "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1",
        }

        def check_environment(*args):
            self.assertNotIn("RC_PROMOTION_GITHUB_TOKEN", os.environ)
            self.assertNotIn("RC_PROMOTION_PAT", os.environ)
            self.assertEqual(os.environ["GH_TOKEN"], "test-workflow-token")

        with patch.dict(os.environ, env, clear=True), \
                patch("sys.argv", ["promotion.py", "promote"]), \
                patch.object(Path, "read_text", return_value=json.dumps(config())), \
                patch.object(Path, "write_bytes"), \
                patch.object(p.GitHub, "gates", side_effect=check_environment), \
                patch.object(p.GitHub, "evidence", side_effect=check_environment), \
                patch.object(p, "authenticate_registries", side_effect=check_environment), \
                patch.object(p, "promote", return_value={}) as promote:
            p.main()
            self.assertEqual(promote.call_args.args[2].write_token, self.write_token)
            check_environment()


class PATAuthenticationTests(AuthenticationTests):
    write_token = "test-pat-token"
    auth_mode = "pat"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.github = p.GitHub("example/product")
        self.github.tag = Mock(return_value=COMMIT)
        self.github.release = Mock(return_value=None)
        self.manifest = evidence(archive())
        self.run = {
            "id": 12, "run_attempt": 1, "head_sha": COMMIT,
            "head_branch": "release/1.2.3", "event": "push", "status": "completed",
            "conclusion": "success", "path": ".github/workflows/release-rc.yml",
            "head_repository": {"full_name": "example/product"},
            "referenced_workflows": [{
                "path": f"{p.SOURCE_PUBLISHER}@{PUBLISHER_SHA}", "sha": PUBLISHER_SHA,
            }],
        }
        self.artifact = {
            "id": 42, "name": f"rc-promotion-manifest-{RC}-1", "expired": False,
            "workflow_run": {"id": 12, "head_sha": COMMIT},
        }
        self.repack()
        self.release = {"tag_name": f"v{RC}", "draft": False, "prerelease": True}
        self.github.request = Mock(side_effect=self.request)
        self.github.pages = Mock(side_effect=self.pages)

    def repack(self, extra=False):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as zipped:
            zipped.writestr("rc-promotion-manifest.json", p.canonical(self.manifest))
            if extra:
                zipped.writestr("unexpected", b"extra")
        self.raw = output.getvalue()
        self.artifact.update(digest=charts.digest(self.raw), size_in_bytes=len(self.raw))

    def request(self, path, **kwargs):
        if "/releases/tags/" in path:
            return self.release
        if path.endswith("/actions/artifacts/42/zip"):
            self.assertTrue(kwargs["binary"])
            return self.raw
        if path.endswith("/actions/runs/12/attempts/1"):
            return self.run
        self.fail(path)

    def pages(self, path, key):
        if path.startswith("actions/workflows/release-rc.yml/runs?"):
            return [self.run]
        if path == "actions/runs/12/artifacts":
            return [self.artifact]
        self.fail(path)

    def test_exact_successful_run_and_manifest(self):
        self.assertEqual(self.github.evidence(config(), RC), self.manifest)

    def test_caller_path_forms_in_selection_and_exact_attempt(self):
        caller = config()["source_workflow"]
        paths = [caller, f"{caller}@release/1.2.3", f"{caller}@refs/heads/release/1.2.3",
                 f"{caller}@{COMMIT}"]
        listed = copy.deepcopy(self.run)
        self.github.pages.side_effect = lambda path, key: (
            [listed] if path.startswith("actions/workflows/") else [self.artifact]
        )
        for listed_path in paths:
            for attempt_path in paths:
                with self.subTest(listed_path=listed_path, attempt_path=attempt_path):
                    listed["path"] = listed_path
                    self.run["path"] = attempt_path
                    self.assertEqual(self.github.evidence(config(), RC), self.manifest)

    def test_wrong_caller_refs_rejected_in_selection_attempt_and_resume(self):
        caller = config()["source_workflow"]
        paths = [
            f"{caller}@main", f"{caller}@release/1.2.4", f"{caller}@refs/heads/main",
            f"{caller}@refs/tags/release/1.2.3", f"{caller}@{'b' * 40}",
            f"{caller}@", f"{caller}@release/1.2.3@extra", None,
            ".github/workflows/other.yml@release/1.2.3",
        ]
        for stage in ("selection", "attempt", "resume"):
            for invalid in paths:
                with self.subTest(stage=stage, path=invalid):
                    self.setUp()
                    listed = copy.deepcopy(self.run)
                    self.github.pages.side_effect = lambda path, key: (
                        [listed] if path.startswith("actions/workflows/") else [self.artifact]
                    )
                    if stage == "selection":
                        listed["path"] = invalid
                    else:
                        self.run["path"] = invalid
                    if stage == "resume":
                        intent = {"rc_version": RC, "commit": COMMIT,
                                  "source_run_id": 12, "source_run_attempt": 1}
                        self.github.release.return_value = {
                            "body": "Verified RC promotion intent.\n\n```json\n" +
                                    p.canonical(intent).decode() + "```\n",
                        }
                    with self.assertRaises(p.PromotionError):
                        self.github.evidence(config(), RC)

    def test_list_may_omit_provenance_but_exact_attempt_must_supply_it(self):
        listed = {key: value for key, value in self.run.items() if key != "referenced_workflows"}
        self.github.pages.side_effect = lambda path, key: (
            [listed] if path.startswith("actions/workflows/") else [self.artifact]
        )
        self.assertEqual(self.github.evidence(config(), RC), self.manifest)
        self.github.request.assert_any_call("repos/example/product/actions/runs/12/attempts/1")
        self.run.pop("referenced_workflows")
        with self.assertRaisesRegex(p.PromotionError, "shared publisher provenance"):
            self.github.evidence(config(), RC)

    def test_missing_mutable_wrong_or_ambiguous_publishers_are_rejected(self):
        valid = copy.deepcopy(self.run["referenced_workflows"][0])
        cases = [
            None, [],
            [{**valid, "path": f"{p.SOURCE_PUBLISHER}@main", "ref": "refs/heads/main"}],
            [{**valid, "path": f"{p.SOURCE_PUBLISHER}@v1.2.3"}],
            [{**valid, "path": f"other/repo/.github/workflows/release-candidate-publish.yml@{PUBLISHER_SHA}"}],
            [{**valid, "path": f"{p.SOURCE_PUBLISHER}@{'e' * 40}", "sha": "e" * 40}],
            [{**valid, "sha": "e" * 40}],
            [{**valid, "ref": "refs/heads/main"}],
            [valid, valid],
        ]
        for referenced in cases:
            with self.subTest(referenced=referenced), self.assertRaises(p.PromotionError):
                self.run["referenced_workflows"] = referenced
                self.github.evidence(config(), RC)

    def test_attempt_must_revalidate_identity_even_when_list_is_successful(self):
        listed = copy.deepcopy(self.run)
        self.github.pages.side_effect = lambda path, key: (
            [listed] if path.startswith("actions/workflows/") else [self.artifact]
        )
        for field, value in [("run_attempt", 2), ("conclusion", "failure"),
                             ("head_sha", "b" * 40), ("id", 99)]:
            with self.subTest(field=field), self.assertRaises(p.PromotionError):
                self.run = {**listed, field: value}
                self.github.evidence(config(), RC)

    def test_wrong_run_identity_status_or_origin(self):
        for field, value in [
            ("head_sha", "b" * 40), ("head_branch", "main"), ("event", "pull_request"),
            ("status", "in_progress"), ("conclusion", "failure"),
            ("path", ".github/workflows/other.yml"),
            ("head_repository", {"full_name": "fork/product"}),
        ]:
            with self.subTest(field=field):
                self.setUp()
                self.run[field] = value
                with self.assertRaises(p.PromotionError):
                    self.github.evidence(config(), RC)

    def test_artifact_provenance_size_expiry_and_digest(self):
        for field, value in [
            ("expired", True), ("name", "wrong"), ("digest", "sha256:" + "0" * 64),
            ("workflow_run", {"id": 99, "head_sha": COMMIT}),
            ("size_in_bytes", 1024 * 1024 + 1), ("size_in_bytes", -1),
        ]:
            with self.subTest(field=field):
                self.setUp()
                self.artifact[field] = value
                with self.assertRaises(p.PromotionError):
                    self.github.evidence(config(), RC)

    def test_extra_archive_members_and_wrong_manifest_are_rejected(self):
        self.repack(extra=True)
        with self.assertRaises(p.PromotionError):
            self.github.evidence(config(), RC)
        self.manifest["run_attempt"] = 99
        self.repack()
        with self.assertRaises(p.PromotionError):
            self.github.evidence(config(), RC)

    def test_resume_uses_original_run_attempt_not_a_newer_rc_run(self):
        intent = {"rc_version": RC, "commit": COMMIT, "source_run_id": 12, "source_run_attempt": 1}
        self.github.release.return_value = {
            "body": "Verified RC promotion intent.\n\n```json\n" +
                    p.canonical(intent).decode() + "```\n",
        }
        caller = config()["source_workflow"]
        for suffix in ("", "@release/1.2.3", "@refs/heads/release/1.2.3", f"@{COMMIT}"):
            with self.subTest(suffix=suffix):
                self.run["path"] = caller + suffix
                self.github.pages.reset_mock()
                self.assertEqual(self.github.evidence(config(), RC), self.manifest)
                self.github.request.assert_any_call("repos/example/product/actions/runs/12/attempts/1")
                self.assertEqual(self.github.pages.call_count, 1)

    def test_missing_tag_or_nonprerelease_fails(self):
        self.github.tag.return_value = None
        with self.assertRaises(p.PromotionError):
            self.github.evidence(config(), RC)
        self.github.tag.return_value = COMMIT
        self.release["prerelease"] = False
        with self.assertRaises(p.PromotionError):
            self.github.evidence(config(), RC)


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.github = p.GitHub("example/product")
        self.intent = {"rc_version": RC, "commit": COMMIT, "source_run_id": 12}
        self.github.tag = Mock(return_value=None)
        self.github.release = Mock(return_value=None)
        self.github.read_asset = Mock(return_value=None)
        self.github.upload = Mock()
        self.github.release_notes = Mock(return_value="## Features\n\n* New feature")
        self.github.request = Mock(side_effect=self.create)
        self.created = None

    def create(self, path, method, data):
        self.assertEqual(path, "repos/example/product/releases")
        self.assertEqual(method, "POST")
        self.created = {"id": 5, **copy.deepcopy(data)}
        return self.created

    def test_atomic_intent_survives_failed_asset_upload(self):
        self.github.upload.side_effect = p.PromotionError("upload failed")
        with self.assertRaises(p.PromotionError):
            self.github.reserve("1.2.3", COMMIT, self.intent)
        self.assertEqual(self.github.intent(self.created), self.intent)
        self.assertTrue(self.created["draft"])
        self.assertTrue(self.created["body"].startswith("## Features\n\n* New feature\n\n"))
        self.github.release.return_value = self.created
        self.github.upload.side_effect = None
        self.assertEqual(self.github.reserve("1.2.3", COMMIT, self.intent), self.created)
        self.assertEqual(self.github.request.call_count, 1)
        self.github.release_notes.assert_called_once_with("1.2.3", COMMIT)

    def test_notes_failure_never_reserves_or_tags(self):
        self.github.release_notes.side_effect = p.PromotionError("generator failed")
        with self.assertRaisesRegex(p.PromotionError, "generator failed"):
            self.github.reserve("1.2.3", COMMIT, self.intent)
        self.github.request.assert_not_called()
        self.github.upload.assert_not_called()

    def test_legacy_reservation_retries_without_regenerating_notes(self):
        legacy = {"id": 5, "draft": True, "prerelease": False,
                  "body": "Verified RC promotion intent.\n\n```json\n" +
                          p.canonical(self.intent).decode() + "```\n"}
        self.github.release.return_value = legacy
        self.assertEqual(self.github.reserve("1.2.3", COMMIT, self.intent), legacy)
        self.github.release_notes.assert_not_called()
        self.github.request.assert_not_called()

    def test_conflicting_tag_or_stored_intent_never_writes(self):
        self.github.tag.return_value = COMMIT
        with self.assertRaises(p.PromotionError):
            self.github.reserve("1.2.3", COMMIT, self.intent)
        self.github.request.assert_not_called()
        self.github.tag.return_value = None
        self.github.reserve("1.2.3", COMMIT, self.intent)
        self.github.release.return_value = self.created
        self.github.upload.reset_mock()
        with self.assertRaises(p.PromotionError):
            self.github.reserve("1.2.3", COMMIT, {**self.intent, "rc_version": "1.2.3-rc.8"})
        self.github.upload.assert_not_called()

    def test_different_intent_asset_rejects(self):
        self.github.reserve("1.2.3", COMMIT, self.intent)
        self.github.release.return_value = self.created
        self.github.read_asset.return_value = {"different": "intent"}
        with self.assertRaises(p.PromotionError):
            self.github.reserve("1.2.3", COMMIT, self.intent)

    def test_finish_creates_exact_tag_before_publishing(self):
        self.github.request = Mock()
        self.github.finish({"id": 5, "tag_name": "v1.2.3", "draft": True}, COMMIT)
        calls = self.github.request.call_args_list
        self.assertEqual(calls[0].args, (
            "repos/example/product/git/refs", "POST",
            {"ref": "refs/tags/v1.2.3", "sha": COMMIT},
        ))
        self.assertEqual(calls[1].args, (
            "repos/example/product/releases/5", "PATCH",
            {"draft": False, "prerelease": False, "make_latest": "false"},
        ))


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        self.github = p.GitHub("example/product")
        self.base = "b" * 40
        self.tags = [{"name": name, "commit": {"sha": self.base}} for name in
                     ["v1.2.0", "v1.10.0", "v1.11.0-rc.1", "v1", "v1.9.0", "v2.0.0",
                      "v01.10.1", "v1.010.1", "v1.10.01", "v1.10.1+build", "1.10.1", "v1.10.1\n"]]
        self.commits = [{"sha": COMMIT, "commit": {"message": "feat: full stable range"}}]
        self.github.request = Mock(return_value={"status": "ahead", "total_commits": 1})
        self.github.pages = Mock(side_effect=lambda path, key=None, **kwargs:
                                 self.tags if path == "tags" else self.commits)

    def generate(self):
        with patch.object(p, "command", return_value=b"## Features\n") as command:
            self.assertEqual(self.github.release_notes("1.11.0", COMMIT), "## Features")
            self.payload = json.loads(command.call_args.kwargs["data"])

    def test_selects_latest_ancestor_stable_by_semver_and_uses_exact_rc_commit(self):
        self.generate()
        self.assertEqual(self.payload["previousTag"], "v1.10.0")
        self.assertEqual(self.payload["version"], "1.11.0")
        self.assertEqual(self.payload["commit"], COMMIT)
        self.assertEqual(self.payload["commits"],
                         [{"hash": COMMIT, "message": "feat: full stable range"}])
        self.github.pages.assert_any_call(f"compare/{self.base}...{COMMIT}", "commits", max_pages=None)

    def test_first_release_uses_reachable_history(self):
        self.tags.clear()
        self.generate()
        self.assertIsNone(self.payload["previousTag"])
        self.github.pages.assert_any_call(f"commits?sha={COMMIT}", max_pages=None)

    def test_skips_stable_tags_outside_rc_ancestry(self):
        self.github.request.side_effect = [
            {"status": "diverged"}, {"status": "ahead", "total_commits": 1},
        ]
        self.generate()
        self.assertEqual(self.payload["previousTag"], "v1.9.0")

    def test_incomplete_comparison_fails_before_generation(self):
        self.github.request.return_value["total_commits"] = 2
        with patch.object(p, "command") as command, self.assertRaisesRegex(
            p.PromotionError, "incomplete release notes history"
        ):
            self.github.release_notes("1.11.0", COMMIT)
        command.assert_not_called()

    def test_comparison_paginates_past_first_hundred_commits(self):
        self.github.pages = p.GitHub.pages.__get__(self.github)
        self.github.request.side_effect = [
            [{"name": "v1.10.0", "commit": {"sha": self.base}}],
            {"status": "ahead", "total_commits": 101},
            {"commits": self.commits * 100}, {"commits": self.commits},
        ]
        self.generate()
        self.assertEqual(len(self.payload["commits"]), 101)
        self.assertTrue(self.github.request.call_args.args[0].endswith("per_page=100&page=2"))

    def test_comparison_history_at_and_beyond_ten_full_pages(self):
        for count in (1000, 1001):
            with self.subTest(commits=count):
                self.github.pages = p.GitHub.pages.__get__(self.github)
                self.github.request.side_effect = [
                    [{"name": "v1.10.0", "commit": {"sha": self.base}}],
                    {"status": "ahead", "total_commits": count},
                    *[{"commits": self.commits * 100} for _ in range(10)],
                    {"commits": self.commits * (count - 1000)},
                ]
                self.generate()
                self.assertEqual(len(self.payload["commits"]), count)
                self.assertTrue(self.github.request.call_args.args[0].endswith("page=11"))

    def test_first_release_history_at_and_beyond_ten_full_pages(self):
        for count in (1000, 1001):
            with self.subTest(commits=count):
                self.github.pages = p.GitHub.pages.__get__(self.github)
                self.github.request.side_effect = [
                    [], *[self.commits * 100 for _ in range(10)],
                    self.commits * (count - 1000),
                ]
                self.generate()
                self.assertIsNone(self.payload["previousTag"])
                self.assertEqual(len(self.payload["commits"]), count)
                self.assertTrue(self.github.request.call_args.args[0].endswith("page=11"))

    def test_tag_history_at_and_beyond_ten_full_pages(self):
        stable = {"name": "v1.10.0", "commit": {"sha": self.base}}
        for count in (1000, 1001):
            with self.subTest(tags=count):
                tags = [{"name": f"v1.11.0-rc.{index}"} for index in range(count - 1)] + [stable]
                self.github.pages = p.GitHub.pages.__get__(self.github)
                self.github.request.side_effect = [
                    *[tags[index:index + 100] for index in range(0, 1001, 100)],
                    {"status": "ahead", "total_commits": 1}, {"commits": self.commits},
                ]
                self.generate()
                self.assertEqual(self.payload["previousTag"], "v1.10.0")
                self.github.request.assert_any_call("repos/example/product/tags?per_page=100&page=11")

    def test_nonhistory_metadata_retains_its_page_limit(self):
        self.github.pages = p.GitHub.pages.__get__(self.github)
        self.github.request.side_effect = None
        self.github.request.return_value = [{}] * 100
        with self.assertRaisesRegex(p.PromotionError, "pagination limit exceeded"):
            self.github.pages("releases")
        self.assertEqual(self.github.request.call_count, 10)


if __name__ == "__main__":
    unittest.main()
