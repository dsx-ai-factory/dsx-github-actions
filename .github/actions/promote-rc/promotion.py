# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Promote verified RC registry artifacts under a fixed GitHub environment."""

import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.parse
import zipfile

import charts

ENVIRONMENT = "stable-release"
POLICY_PATH = ".github/rc-promotion.json"
SOURCE_PUBLISHER = "dsx-ai-factory/dsx-github-actions/.github/workflows/release-candidate-publish.yml"
SHA = r"[0-9a-f]{40}"
DIGEST = r"sha256:[0-9a-f]{64}"
NAME = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
NGC_PATH = r"[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*"
STABLE = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
RC = rf"{STABLE}-rc\.([1-9][0-9]*)"
INTENT_PREFIX = "Verified RC promotion intent.\n\n```json\n"


class PromotionError(ValueError):
    """A bounded diagnostic that never includes command output or credentials."""


def require(condition, message):
    if not condition:
        raise PromotionError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def version(rc):
    require(matches(RC, rc) and len(rc) <= 96, "rc_version must be exactly X.Y.Z-rc.N")
    return rc.rsplit("-rc.", 1)[0]


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def command(args, data=None, env=None, missing=None, clean_stderr=False):
    result = subprocess.run(
        args, input=data, capture_output=True, check=False, timeout=600, env=env,
    )
    if result.returncode:
        if missing and missing(result.stderr.decode("utf8", errors="replace")):
            return None
        raise PromotionError(f"{Path(args[0]).name} operation failed")
    require(not clean_stderr or not result.stderr, "registry lookup reported warnings")
    return result.stdout


def value_paths(value):
    require(isinstance(value, list) and len(value) <= 32, "invalid chart value paths")
    seen = set()
    for keys in value:
        require(isinstance(keys, list) and 0 < len(keys) <= 16 and
                all(matches(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,127}", key) for key in keys),
                "invalid chart value path")
        require(tuple(keys) not in seen, "duplicate chart value path")
        seen.add(tuple(keys))
    return value


def policy(value):
    require(isinstance(value, dict) and set(value) == {
        "schema", "enabled", "source_workflow", "source_publisher_sha", "source_ngc_path",
        "destination_ngc_path", "images", "charts", "competing_workflows",
    }, "invalid promotion policy fields")
    require(type(value["schema"]) is int and value["schema"] == 1, "unsupported policy schema")
    require(value["enabled"] is True, "promotion policy is not enabled")
    require(matches(r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", value["source_workflow"]),
            "invalid source workflow")
    require(matches(SHA, value["source_publisher_sha"]), "source publisher must be an immutable SHA")
    for field in ("source_ngc_path", "destination_ngc_path"):
        require(matches(NGC_PATH, value[field]), "invalid NGC organization/team")
    require(isinstance(value["images"], list) and len(value["images"]) <= 32 and
            all(matches(NAME, name) for name in value["images"]) and
            len(set(value["images"])) == len(value["images"]), "invalid image inventory")
    require(isinstance(value["charts"], list) and len(value["charts"]) <= 32,
            "invalid chart inventory")
    names = []
    for chart in value["charts"]:
        require(isinstance(chart, dict) and set(chart) == {"name", "version_value_paths"} and
                matches(NAME, chart["name"]), "invalid chart declaration")
        names.append(chart["name"])
        value_paths(chart["version_value_paths"])
    require(len(set(names)) == len(names), "duplicate charts")
    require(not names or value["source_ngc_path"] == value["destination_ngc_path"],
            "chart promotion requires identical source and destination NGC paths")
    require(value["images"] or names, "empty promotion inventory")
    require(isinstance(value["competing_workflows"], list) and
            len(value["competing_workflows"]) <= 16 and
            all(matches(r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", item)
                for item in value["competing_workflows"]), "invalid competing workflows")
    return value


def manifest(value, config, repository, rc, commit, run):
    require(isinstance(value, dict) and set(value) == {
        "schema", "repository", "rc_version", "commit", "run_id", "run_attempt",
        "source_workflow", "source_ngc_path", "images", "charts",
    }, "invalid RC manifest fields")
    require(type(value["schema"]) is int and value["schema"] == 1 and
            type(value["run_id"]) is int and type(value["run_attempt"]) is int and
            value["repository"] == repository and
            value["rc_version"] == rc and value["commit"] == commit and
            value["run_id"] == run["id"] and value["run_attempt"] == run["run_attempt"] and
            value["source_workflow"] == config["source_workflow"] and
            value["source_ngc_path"] == config["source_ngc_path"],
            "RC manifest identity does not match the verified run")
    require(isinstance(value["images"], list) and isinstance(value["charts"], list),
            "invalid manifest inventory")
    images = {}
    for item in value["images"]:
        require(isinstance(item, dict) and set(item) == {"name", "digest"} and
                matches(NAME, item["name"]) and matches(DIGEST, item["digest"]) and
                item["name"] not in images, "invalid manifest image")
        images[item["name"]] = item
    chart_map = {}
    for item in value["charts"]:
        require(isinstance(item, dict) and set(item) == {
            "name", "digest", "version_value_paths",
        } and matches(NAME, item["name"]) and matches(DIGEST, item["digest"]) and
                item["name"] not in chart_map, "invalid manifest chart")
        value_paths(item["version_value_paths"])
        chart_map[item["name"]] = item
    require(set(images) == set(config["images"]) and
            set(chart_map) == {item["name"] for item in config["charts"]},
            "RC manifest inventory is incomplete or unexpected")
    for item in config["charts"]:
        require(chart_map[item["name"]]["version_value_paths"] == item["version_value_paths"],
                "chart version paths differ from the reviewed policy")
    return value


class GitHub:
    def __init__(self, repository, write_token=None):
        require(matches(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
                "invalid GitHub repository")
        self.repository = repository
        self.root = f"repos/{repository}"
        self.write_token = write_token

    def write_environment(self):
        require(isinstance(self.write_token, str) and bool(self.write_token.strip()),
                "the environment-scoped release token is missing")
        return {**os.environ, "GH_TOKEN": self.write_token}

    def request(self, path, method="GET", data=None, absent=False, binary=False):
        require(path.startswith(self.root + "/") or path == self.root,
                "GitHub request escaped the product repository")
        endpoint = path.partition("?")[0]
        # Destination draft state needs the same push-capable identity on retries.
        # Published source releases (/releases/tags/...) still use the normal token.
        draft_read = method == "GET" and (
            endpoint == f"{self.root}/releases" or re.fullmatch(
                re.escape(self.root) + r"/releases/(?:[0-9]+(?:/assets)?|assets/[0-9]+)", endpoint)
        )
        env = self.write_environment() if draft_read else None
        if method != "GET":
            require((method == "POST" and path in {
                f"{self.root}/releases", f"{self.root}/git/refs",
            }) or (method == "PATCH" and re.fullmatch(
                re.escape(self.root) + r"/releases/[0-9]+", path)),
                "unsupported GitHub promotion write")
            env = self.write_environment()
        args = ["gh", "api", "--method", method, "-H",
                "Accept: application/vnd.github+json", path]
        if data is not None:
            args += ["--input", "-"]
        output = command(
            args, canonical(data) if data is not None else None,
            missing=(lambda error: "(HTTP 404)" in error) if absent else None,
            env=env,
        )
        if output is None:
            return None
        if binary:
            return output
        try:
            return json.loads(output)
        except ValueError:
            raise PromotionError("invalid GitHub response") from None

    def pages(self, path, key=None, *, max_pages=10):
        """Collect API pages; release history opts out of the metadata page limit."""
        result = []
        page = 1
        while max_pages is None or page <= max_pages:
            join = "&" if "?" in path else "?"
            value = self.request(f"{self.root}/{path}{join}per_page=100&page={page}")
            items = value[key] if key else value
            require(isinstance(items, list), "invalid GitHub page")
            result.extend(items)
            if len(items) < 100:
                return result
            page += 1
        raise PromotionError("GitHub pagination limit exceeded")

    def tag(self, tag):
        item = self.request(f"{self.root}/git/ref/tags/{tag}", absent=True)
        if item is None:
            return None
        for _ in range(8):
            obj = item["object"]
            if obj["type"] == "commit":
                require(matches(SHA, obj["sha"]), "invalid tag commit")
                return obj["sha"]
            require(obj["type"] == "tag" and matches(SHA, obj["sha"]), "invalid tag object")
            item = self.request(f"{self.root}/git/tags/{obj['sha']}")
        raise PromotionError("tag chain is too deep")

    def gates(self, config, context):
        repo = self.request(self.root)
        branch = repo["default_branch"]
        require(context["event"] == "workflow_dispatch" and
                context["ref"] == f"refs/heads/{branch}" and
                context["protected"] == "true" and
                matches(SHA, context["sha"]), "promotion requires the protected default branch")
        live = self.request(f"{self.root}/branches/{urllib.parse.quote(branch, safe='')}")
        require(live["protected"] is True and live["commit"]["sha"] == context["sha"],
                "default branch changed or is not protected")
        environment = self.request(f"{self.root}/environments/{ENVIRONMENT}")
        reviewers = [rule for rule in environment["protection_rules"]
                     if rule["type"] == "required_reviewers"]
        require(environment.get("can_admins_bypass") is False and len(reviewers) == 1 and
                reviewers[0].get("reviewers") and reviewers[0].get("prevent_self_review") is True,
                "stable-release requires reviewers, no self-review, and no admin bypass")
        require(all(item.get("type") == "Team" for item in reviewers[0]["reviewers"]),
                "every required reviewer must be an authorized team")
        branch_policy = environment.get("deployment_branch_policy")
        require(branch_policy == {"protected_branches": False, "custom_branch_policies": True},
                "stable-release must admit only the protected default branch")
        branches = self.pages(f"environments/{ENVIRONMENT}/deployment-branch-policies",
                              "branch_policies")
        require(len(branches) == 1 and branches[0].get("name") == branch and
                branches[0].get("type") == "branch",
                "stable-release branch admission is not the exact default branch")
        # The conventional stable publisher is checked even if omitted from policy.
        for path in sorted(set(config["competing_workflows"] + [".github/workflows/release.yml"])):
            workflow = self.request(f"{self.root}/actions/workflows/{Path(path).name}", absent=True)
            if workflow:
                if workflow["state"] == "disabled_manually":
                    continue
                require(workflow["state"] == "active", "competing workflow state is unknown")
                contents = self.request(f"{self.root}/contents/{path}?ref={context['sha']}")
                require(contents.get("encoding") == "base64", "invalid competing workflow content")
                definition = charts.yaml_object(base64.b64decode(contents["content"], validate=False))
                jobs = definition.get("jobs")
                require(isinstance(jobs, dict) and jobs and all(
                    isinstance(job, dict) and job.get("if") in {
                        "vars.DSX_RELEASE_MODE != 'rc-promotion'",
                        "${{ vars.DSX_RELEASE_MODE != 'rc-promotion' }}",
                    } for job in jobs.values()
                ), "a competing stable workflow lacks the release-mode guard")
                require(definition.get("concurrency") == {
                    "group": "dsx-stable-release-${{ github.repository }}",
                    "cancel-in-progress": False,
                }, "competing stable workflows must share non-cancelling concurrency")
            else:
                contents = self.request(f"{self.root}/contents/{path}?ref={context['sha']}",
                                        absent=True)
                require(contents is None, "competing workflow status is unknown")

    def evidence(self, config, rc):
        commit = self.tag(f"v{rc}")
        require(commit is not None, "RC tag does not exist")
        release = self.request(f"{self.root}/releases/tags/v{rc}")
        require(release["tag_name"] == f"v{rc}" and release["draft"] is False and
                release["prerelease"] is True, "RC must be a published GitHub prerelease")
        workflow = Path(config["source_workflow"]).name
        branch = f"release/{version(rc)}"

        def successful(run):
            if not isinstance(run.get("path"), str):
                return False
            caller_path, separator, caller_ref = run["path"].partition("@")
            return (run["head_sha"] == commit and
                    run["head_branch"] == branch and
                    run["event"] == "push" and run["status"] == "completed" and
                    run["conclusion"] == "success" and caller_path == config["source_workflow"] and
                    (not separator or caller_ref in {branch, f"refs/heads/{branch}", commit}) and
                    run["head_repository"]["full_name"] == self.repository)

        reserved = self.release(version(rc))
        if reserved:
            intent = self.intent(reserved)
            require(intent["rc_version"] == rc and intent["commit"] == commit and
                    type(intent["source_run_id"]) is int and
                    type(intent["source_run_attempt"]) is int,
                    "stable release is reserved for another RC")
            selected = {"id": intent["source_run_id"], "run_attempt": intent["source_run_attempt"]}
        else:
            runs = self.pages(
                f"actions/workflows/{workflow}/runs?head_sha={commit}&event=push&status=success",
                "workflow_runs",
            )
            runs = [run for run in runs if successful(run)]
            require(runs, "no successful RC publishing run for this exact tag")
            selected = max(runs, key=lambda item: item["id"])
        # Fetch exact-attempt detail even when the list response omits provenance.
        run = self.request(f"{self.root}/actions/runs/{selected['id']}/attempts/"
                           f"{selected['run_attempt']}")
        require(run["id"] == selected["id"] and
                run["run_attempt"] == selected["run_attempt"] and successful(run),
                "selected RC attempt does not match the successful publishing run")
        referenced = run.get("referenced_workflows")
        require(isinstance(referenced, list), "RC attempt has no shared publisher provenance")
        publishers = [item for item in referenced if isinstance(item, dict) and
                      isinstance(item.get("path"), str) and
                      item["path"].split("@", 1)[0] == SOURCE_PUBLISHER]
        sha = config["source_publisher_sha"]
        require(len(publishers) == 1 and publishers[0]["path"] == f"{SOURCE_PUBLISHER}@{sha}" and
                publishers[0].get("sha") == sha and publishers[0].get("ref") in {None, sha},
                "RC attempt did not use the approved immutable shared publisher")
        artifacts = self.pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
        name = f"rc-promotion-manifest-{rc}-{run['run_attempt']}"
        artifacts = [item for item in artifacts if item["name"] == name and
                     item["expired"] is False]
        require(len(artifacts) == 1, "successful RC run has no unique unexpired promotion manifest")
        artifact = artifacts[0]
        require(artifact["workflow_run"]["id"] == run["id"] and
                artifact["workflow_run"]["head_sha"] == commit and
                matches(DIGEST, artifact.get("digest")), "invalid RC artifact provenance")
        require(type(artifact.get("size_in_bytes")) is int and
                0 < artifact["size_in_bytes"] <= 1024 * 1024,
                "RC manifest artifact exceeds the size limit")
        raw = self.request(f"{self.root}/actions/artifacts/{artifact['id']}/zip", binary=True)
        require(charts.digest(raw) == artifact["digest"], "RC artifact download digest mismatch")
        require(len(raw) <= 1024 * 1024, "RC manifest artifact exceeds the size limit")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            require(archive.namelist() == ["rc-promotion-manifest.json"] and
                    archive.infolist()[0].file_size <= 1024 * 1024,
                    "unexpected RC manifest archive contents")
            data = json.loads(archive.read("rc-promotion-manifest.json"))
        return manifest(data, config, self.repository, rc, commit, run)

    @staticmethod
    def intent(release):
        """Read the final promotion evidence block, with or without leading notes."""
        body = release.get("body", "")
        # New descriptions lead with release notes; legacy reservations contain
        # only this evidence block. Always parse the final block on retries.
        _, marker, stored = body.rpartition(INTENT_PREFIX)
        require(marker and stored.endswith("```\n"),
                "stable release has no verified promotion intent")
        try:
            result = json.loads(stored[:-4])
        except ValueError:
            raise PromotionError("invalid stored promotion intent") from None
        require(isinstance(result, dict), "invalid stored promotion intent")
        return result

    def release_notes(self, stable, commit):
        """Generate Conventional Commits notes from the preceding stable ancestor."""
        target = tuple(map(int, stable.split(".")))
        tags = [tag for tag in self.pages("tags", max_pages=None)
                if matches(f"v{STABLE}", tag["name"])]
        tags.sort(key=lambda tag: tuple(map(int, tag["name"][1:].split("."))), reverse=True)
        previous = None
        for tag in tags:
            if tuple(map(int, tag["name"][1:].split("."))) >= target:
                continue
            base = tag["commit"]["sha"]
            require(matches(SHA, base), "invalid release notes baseline")
            comparison = f"compare/{base}...{commit}"
            info = self.request(f"{self.root}/{comparison}?per_page=1")
            if info["status"] in {"ahead", "identical"}:
                previous = tag["name"]
                commits = self.pages(comparison, "commits", max_pages=None)
                require(len(commits) == info["total_commits"], "incomplete release notes history")
                break
        else:
            # A first stable release includes all history reachable from its RC.
            commits = self.pages(f"commits?sha={commit}", max_pages=None)
        payload = {
            "repository": self.repository, "version": stable, "previousTag": previous,
            "commit": commit,
            "commits": [{"hash": item["sha"], "message": item["commit"]["message"]}
                        for item in commits],
        }
        notes = command(["node", str(Path(__file__).with_name("release-notes.mjs"))],
                        data=canonical(payload)).decode().strip()
        require(notes, "release notes generator returned no description")
        return notes

    def release(self, stable):
        matches_ = [item for item in self.pages("releases")
                    if item["tag_name"] == f"v{stable}"]
        require(len(matches_) <= 1, "multiple stable releases use this version")
        return matches_[0] if matches_ else None

    def read_asset(self, release, name):
        assets = self.pages(f"releases/{release['id']}/assets")
        found = [item for item in assets if item["name"] == name]
        require(len(found) <= 1, "release has duplicate promotion intents")
        if not found:
            return None
        item = found[0]
        require(item["size"] <= 1024 * 1024, "promotion intent exceeds the size limit")
        args = ["gh", "api", "-H", "Accept: application/octet-stream",
                f"{self.root}/releases/assets/{item['id']}"]
        return json.loads(command(args, env=self.write_environment()))

    def upload(self, release, name, data):
        require(matches(r"[A-Za-z0-9_.-]+", name), "invalid release asset name")
        env = self.write_environment()
        # gh handles GitHub's asset upload host and never puts the token in argv.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(canonical(data))
            command(["gh", "release", "upload", release["tag_name"], str(path),
                     "--repo", self.repository], env=env)

    def reserve(self, stable, commit, intent):
        """Reuse matching evidence or reserve a draft with notes before tagging."""
        existing_tag = self.tag(f"v{stable}")
        release = self.release(stable)
        body = INTENT_PREFIX + canonical(intent).decode() + "```\n"
        if release:
            require(release["prerelease"] is False, "stable release is a prerelease")
            require(existing_tag in {None, commit}, "stable tag points to a different commit")
            # The atomic release creation stores the intent before its asset upload.
            require(self.intent(release) == intent, "stable release belongs to different promotion intent")
            asset = self.read_asset(release, "promotion-intent.json")
            require(asset is None or asset == intent,
                    "stable version belongs to different promotion evidence")
            if not release["draft"]:
                require(existing_tag == commit, "published stable tag is missing or changed")
            if asset is None:
                self.upload(release, "promotion-intent.json", intent)
            return release
        require(existing_tag is None, "stable tag exists without matching promotion intent")
        # Generate once before reserving or tagging. Retries retain the stored
        # description even if other stable releases have since been published.
        body = self.release_notes(stable, commit) + "\n\n" + body
        release = self.request(f"{self.root}/releases", "POST", {
            "tag_name": f"v{stable}", "target_commitish": commit, "name": f"v{stable}",
            "draft": True, "prerelease": False,
            "body": body,
        })
        self.upload(release, "promotion-intent.json", intent)
        return release

    def finish(self, release, commit):
        tag = self.tag(release["tag_name"])
        require(tag in {None, commit}, "stable tag changed during promotion")
        if tag is None:
            self.request(f"{self.root}/git/refs", "POST",
                         {"ref": f"refs/tags/{release['tag_name']}", "sha": commit})
        if release["draft"]:
            self.request(f"{self.root}/releases/{release['id']}", "PATCH", {
                "draft": False, "prerelease": False, "make_latest": "false",
            })


class Registry:
    def __init__(self, source, destination=None):
        self.source = source
        self.destination = destination or source
        self.tools = Path(__file__).resolve().parent.parent / "release-artifacts"

    def docker_env(self, role):
        require(role in {"source", "destination"}, "invalid registry credential role")
        key = f"{role.upper()}_DOCKER_CONFIG"
        return {**os.environ, "DOCKER_CONFIG": os.environ[key]}

    def image_digest(self, role, name, tag, absent=False):
        env = self.docker_env(role)
        namespace = getattr(self, role)
        ref = f"nvcr.io/{namespace}/{name}:{tag}"
        output = command(
            ["docker", "buildx", "imagetools", "inspect", "--raw", ref],
            env=env,
            missing=(lambda error: error.strip().removeprefix("ERROR: ") in {
                f"{ref}: not found", f"{ref}: manifest unknown",
                "manifest unknown", "manifest unknown: manifest unknown",
                f"{ref}: manifest unknown: manifest unknown",
                "MANIFEST_UNKNOWN: manifest unknown",
            }) if absent else None,
        )
        if output is None:
            return None
        json.loads(output)
        # Buildx's raw display can add a newline. Use the registry descriptor,
        # not a hash of terminal formatting, as the immutable image identity.
        descriptor = json.loads(command(
            ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", ref],
            env=env,
        ))
        require(matches(DIGEST, descriptor.get("digest")), "invalid registry manifest digest")
        return descriptor["digest"]

    def verify_image(self, name, digest, commit):
        command(["bash", str(self.tools / "verify-image.sh"),
                 f"nvcr.io/{self.source}/{name}@{digest}", commit],
                env=self.docker_env("source"))

    def copy_image(self, name, digest, stable):
        command(["skopeo", "copy", "--all", "--preserve-digests",
                 "--src-authfile", str(Path(os.environ["SOURCE_DOCKER_CONFIG"]) / "config.json"),
                 "--dest-authfile", str(Path(os.environ["DESTINATION_DOCKER_CONFIG"]) / "config.json"),
                 f"docker://nvcr.io/{self.source}/{name}@{digest}",
                 f"docker://nvcr.io/{self.destination}/{name}:{stable}"])

    def chart(self, role, name, version, absent=False):
        require(role in {"source", "destination"}, "invalid registry credential role")
        repo = f"promotion-{role}"
        # Refresh errors never mean "version absent".
        command(["helm", "repo", "update", repo, "--fail-on-repo-update-fail"], clean_stderr=True)
        rows = json.loads(command([
            "helm", "search", "repo", f"{repo}/{name}", "--versions", "--devel", "-o", "json",
        ], clean_stderr=True))
        require(isinstance(rows, list) and all(isinstance(row, dict) and
                isinstance(row.get("name"), str) and isinstance(row.get("version"), str)
                for row in rows), "invalid chart index")
        found = [row for row in rows if row["name"] == f"{repo}/{name}" and
                 row["version"] == version]
        require(len(found) <= 1, "ambiguous chart version")
        if not found:
            require(absent, "RC chart is missing")
            return None
        with tempfile.TemporaryDirectory() as directory:
            command(["helm", "pull", f"{repo}/{name}", "--version", version,
                     "--destination", directory])
            return (Path(directory) / f"{name}-{version}.tgz").read_bytes()

    def chart_destination(self, name):
        command(["ngc", "registry", "chart", "info", f"{self.destination}/{name}"])

    def publish_chart(self, name, stable, data):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / f"{name}-{stable}.tgz").write_bytes(data)
            command(["ngc", "registry", "chart", "push", "--source", directory,
                     f"{self.destination}/{name}:{stable}"])


def promote(config, evidence, github, registry, attempt, report_path=None):
    policy(config)
    rc = evidence["rc_version"]
    stable = version(rc)
    commit = evidence["commit"]
    manifest_digest = charts.digest(canonical(evidence))
    prepared = {}
    image_targets = []
    chart_targets = []
    # Complete the entire source and destination preflight before reserving a release.
    for item in evidence["images"]:
        actual = registry.image_digest("source", item["name"], rc)
        require(actual == item["digest"], "RC image tag changed after verification")
        registry.verify_image(item["name"], actual, commit)
        existing = registry.image_digest("destination", item["name"], stable, absent=True)
        require(existing in {None, actual}, "stable image already has a different digest")
        image_targets.append({"name": item["name"], "digest": actual})
    for item in evidence["charts"]:
        source = registry.chart("source", item["name"], rc)
        require(charts.digest(source) == item["digest"], "RC chart changed after verification")
        transformed = charts.promote(source, item["name"], rc, stable, commit,
                                     item["version_value_paths"], manifest_digest,
                                     {f"nvcr.io/{config['destination_ngc_path']}/{image['name']}:{stable}"
                                      for image in image_targets})
        prepared[item["name"]] = transformed
        registry.chart_destination(item["name"])
        existing = registry.chart("destination", item["name"], stable, absent=True)
        require(existing is None or existing == transformed,
                "stable chart already contains different files or provenance")
        chart_targets.append({"name": item["name"], "digest": charts.digest(transformed)})
    intent = {
        "schema": 1, "repository": evidence["repository"], "rc_version": rc,
        "stable_version": stable, "commit": commit, "rc_manifest_digest": manifest_digest,
        "source_run_id": evidence["run_id"], "source_run_attempt": evidence["run_attempt"],
        "source_publisher_sha": config["source_publisher_sha"],
        "source_ngc_path": config["source_ngc_path"],
        "destination_ngc_path": config["destination_ngc_path"],
        "images": image_targets, "charts": chart_targets,
    }
    release = github.reserve(stable, commit, intent)
    report = {"schema": 1, "intent": intent, "status": "partial", "artifacts": []}

    def save():
        if report_path is not None:
            report_path.write_bytes(canonical(report))

    save()
    try:
        for item in image_targets:
            current = registry.image_digest("destination", item["name"], stable, absent=True)
            require(current in {None, item["digest"]}, "stable image changed during promotion")
            if current is None:
                registry.copy_image(item["name"], item["digest"], stable)
            require(registry.image_digest("destination", item["name"], stable)
                    == item["digest"], "copied image digest does not match the RC")
            report["artifacts"].append({"kind": "image", **item, "verified": True})
            save()
        for item in chart_targets:
            current = registry.chart("destination", item["name"], stable, absent=True)
            require(current is None or current == prepared[item["name"]],
                    "stable chart changed during promotion")
            if current is None:
                registry.publish_chart(item["name"], stable, prepared[item["name"]])
            require(registry.chart("destination", item["name"], stable)
                    == prepared[item["name"]], "published chart differs from the prepared package")
            report["artifacts"].append({"kind": "chart", **item, "verified": True})
            save()
        # Preserve the verified report before exposing the stable tag and release.
        report["status"] = "artifacts_verified"
        save()
        github.upload(release, f"promotion-attempt-{attempt}.json", report)
        require(github.tag(f"v{rc}") == commit, "RC tag changed during promotion")
        github.finish(release, commit)
        report["status"] = "completed"
        save()
        return report
    except Exception:
        save()
        if report["status"] == "partial":
            github.upload(release, f"promotion-attempt-{attempt}.json", report)
        raise


def capture(config, registry, context):
    rc = context["rc_version"]
    version(rc)
    require(matches(SHA, context["commit"]), "invalid RC commit")
    result = {
        "schema": 1, **context, "source_ngc_path": config["source_ngc_path"],
        "images": [], "charts": [],
    }
    for name in sorted(config["images"]):
        value = registry.image_digest("source", name, rc)
        require(matches(DIGEST, value), "invalid image digest")
        registry.verify_image(name, value, context["commit"])
        result["images"].append({"name": name, "digest": value})
    for item in sorted(config["charts"], key=lambda item: item["name"]):
        data = registry.chart("source", item["name"], rc)
        charts.validate(data, item["name"], rc, context["commit"])
        result["charts"].append({**item, "digest": charts.digest(data)})
    return result


def authenticate_registries(config, capture_only=False):
    source_key = os.environ.get("RC_PROMOTION_SOURCE_NGC_KEY", "")
    target_key = os.environ.get("RC_PROMOTION_DESTINATION_NGC_KEY", "")
    require(source_key and (capture_only or target_key), "registry credentials are missing")
    # Keep Docker and Helm credentials in the runner's ephemeral workspace.
    for alias, namespace, key in (
        ("promotion-source", config["source_ngc_path"], source_key),
        ("promotion-destination", config.get("destination_ngc_path"), target_key),
    ):
        if not namespace:
            continue
        command(["helm", "repo", "add", alias, f"https://helm.ngc.nvidia.com/{namespace}",
                 "--username", "$oauthtoken", "--password-stdin"], data=(key + "\n").encode())
    # Docker resolves credentials by registry host, so keep distinct source and
    # destination configurations even when both repositories are on nvcr.io.
    for label, key in (("SOURCE", source_key), ("DESTINATION", target_key)):
        if not key:
            continue
        directory = Path(os.environ["PROMOTION_AUTH_ROOT"]) / label.lower()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / "config.json"
        auth = {"auths": {"nvcr.io": {
            "auth": base64.b64encode(f"$oauthtoken:{key}".encode()).decode(),
        }}}
        with open(path, "w", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
            json.dump(auth, output)
        os.environ[f"{label}_DOCKER_CONFIG"] = str(directory)
    if not capture_only:
        os.environ["NGC_CLI_API_KEY"] = target_key
        os.environ.pop("NGC_API_KEY", None)
        os.environ["NGC_CLI_ORG"], os.environ["NGC_CLI_TEAM"] = config["destination_ngc_path"].split("/")


def main():
    # Keep the write token out of all inherited registry, parser, and read-tool environments.
    write_token = os.environ.pop("RC_PROMOTION_GITHUB_TOKEN", "")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("capture", "promote"))
    parser.add_argument("--policy", default=POLICY_PATH)
    args = parser.parse_args()
    if args.operation == "capture":
        images = json.loads(os.environ["RC_IMAGES"])
        declared = json.loads(os.environ["RC_CHARTS"])
        config = {
            "source_ngc_path": os.environ["RC_NGC_PATH"],
            "images": [item["name"] for item in images],
            "charts": [{"name": item["name"], "version_value_paths": item.get("versionValuePaths", [])}
                       for item in declared],
        }
        authenticate_registries(config, capture_only=True)
        workflow = os.environ["GITHUB_WORKFLOW_REF"].split("@", 1)[0]
        prefix = os.environ["GITHUB_REPOSITORY"] + "/"
        require(workflow.startswith(prefix), "invalid RC workflow reference")
        context = {
            "repository": os.environ["GITHUB_REPOSITORY"], "rc_version": os.environ["RC_VERSION"],
            "commit": os.environ["GITHUB_SHA"], "run_id": int(os.environ["GITHUB_RUN_ID"]),
            "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "source_workflow": workflow[len(prefix):],
        }
        result = capture(config, Registry(config["source_ngc_path"]), context)
        Path("rc-promotion-manifest.json").write_bytes(canonical(result))
        return
    require(os.environ.get("DSX_RELEASE_MODE") == "rc-promotion", "promotion activation gate is off")
    config = policy(json.loads(Path(args.policy).read_text()))
    github = GitHub(os.environ["GITHUB_REPOSITORY"], write_token=write_token)
    github.write_environment()
    context = {
        "event": os.environ["GITHUB_EVENT_NAME"], "ref": os.environ["GITHUB_REF"],
        "sha": os.environ["GITHUB_SHA"], "protected": os.environ["GITHUB_REF_PROTECTED"],
    }
    github.gates(config, context)
    rc = os.environ["RC_VERSION"]
    version(rc)
    evidence = github.evidence(config, rc)
    authenticate_registries(config)
    result = promote(config, evidence, github,
                     Registry(config["source_ngc_path"], config["destination_ngc_path"]),
                     f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}",
                     Path("promotion-result.json"))
    Path("promotion-result.json").write_bytes(canonical(result))


if __name__ == "__main__":
    try:
        main()
    except (PromotionError, charts.InvalidChart) as error:
        print(f"Promotion stopped: {error}")
        raise SystemExit(1) from None
    except Exception:
        print("Promotion stopped: unexpected response or tool failure")
        raise SystemExit(1) from None
