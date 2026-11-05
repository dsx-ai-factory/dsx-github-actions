# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify protected GitLab RC artifacts; promote from protected default-branch code.

The policy is the existing promotion policy plus a ``gitlab`` object containing
api_url, project_id, repository (GitHub owner/repo) and authorization settings.
All GitLab reads use read_api except /job, which authenticates
the running job with CI_JOB_TOKEN. There are no GitLab mutations in this adapter.
In group mode, empty lists permit verification but disable promotion. Promotion accepts
RC_VERSION only on the latest protected default branch's web/api pipeline.
Optional artifact_download_hosts lists exact HTTPS storage/CDN hosts. Redirected
artifact requests never carry GitLab credentials; metadata never follows redirects.
Authorization defaults to independent group approvals. The opt-in maintainer
mode checks the pipeline and manual-job users' effective project membership,
without a second approval. Native admin bypasses do not bypass these checks.
"""

import argparse
import base64
from contextlib import ExitStack, contextmanager, nullcontext
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import charts
import promotion as p
from github_app import GitHubAppError, installation_token

POLICY_PATH = ".gitlab/rc-promotion.json"
MANIFEST = "rc-promotion-manifest.json"
RESULT = "promotion-result.json"
MAX_BYTES = 1024 * 1024


def positive(value):
    return type(value) is int and value > 0


def integer(value):
    p.require(p.matches(r"[1-9][0-9]{0,19}", value), "invalid CI numeric identity")
    return int(value)


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            p.require(key not in result, "duplicate JSON field")
            result[key] = value
        return result

    p.require(len(raw) <= MAX_BYTES, "GitLab response exceeds the size limit")
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise p.PromotionError("invalid JSON response") from None


def policy(document):
    p.require(isinstance(document, dict), "invalid GitLab promotion policy")
    config = p.policy({key: value for key, value in document.items() if key != "gitlab"})
    gate = document.get("gitlab")
    p.require(isinstance(gate, dict) and set(gate) - {
        "artifact_download_hosts", "authorization", "deploy_group_ids", "approver_group_ids",
    } == {
        "api_url", "project_id", "repository",
    }, "invalid GitLab policy fields")
    authorization = gate.get("authorization", "groups")
    p.require(authorization in ("groups", "maintainer"), "invalid GitLab authorization mode")
    url = urllib.parse.urlsplit(gate["api_url"])
    p.require(url.scheme == "https" and url.hostname and not url.username and
              not url.password and not url.query and not url.fragment and
              url.path == "/api/v4", "GitLab API URL must be an HTTPS /api/v4 origin")
    p.require(positive(gate["project_id"]) and
              p.matches(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", gate["repository"]),
              "invalid GitLab project or GitHub repository")
    for field in ("deploy_group_ids", "approver_group_ids"):
        groups = gate.get(field, [] if authorization == "maintainer" else None)
        p.require(isinstance(groups, list) and len(groups) <= 16 and
                  all(positive(item) for item in groups) and len(groups) == len(set(groups)),
                  "GitLab deployment and approval groups must be explicit numeric allowlists")
        p.require(authorization != "maintainer" or not groups,
                  "maintainer authorization cannot include group allowlists")
    hosts = gate.get("artifact_download_hosts", [])
    p.require(isinstance(hosts, list) and len(hosts) <= 16 and
              all(p.matches(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}", host) for host in hosts) and
              len(set(hosts)) == len(hosts), "artifact download hosts must be exact DNS names")
    return config, gate


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward project or job credentials to an artifact/CDN redirect.
        raise p.PromotionError("GitLab API redirect refused")


class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def __init__(self, api_url, hosts):
        self.hosts = {urllib.parse.urlsplit(api_url).hostname, *hosts}

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        p.require(code in {301, 302, 303, 307, 308} and url.scheme == "https" and
                  url.hostname in self.hosts and url.port in {None, 443} and
                  not url.username and not url.password and not url.fragment,
                  "artifact redirect requires an explicitly allowed HTTPS storage host")
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            pass
        else:
            raise p.PromotionError("artifact redirects cannot target IP addresses")
        # A fresh request deliberately carries no PRIVATE-TOKEN, JOB-TOKEN,
        # Authorization, cookies or Referer, even when redirected to the API host.
        return urllib.request.Request(newurl, method="GET")


class GitLab:
    def __init__(self, gate, token, job_token):
        p.require(token and job_token, "GitLab read_api and CI job tokens are required")
        self.gate = gate
        self.token = token
        self.job_token = job_token
        self.root = f"/projects/{gate['project_id']}"
        self.opener = urllib.request.build_opener(NoRedirect())
        self.artifact_opener = urllib.request.build_opener(
            ArtifactRedirect(gate["api_url"], gate.get("artifact_download_hosts", [])))

    def request(self, path, *, binary=False, job_token=False):
        permitted = path == self.root or path.startswith(self.root + "/")
        p.require((job_token and path == "/job") or (not job_token and permitted),
                  "GitLab request escaped the reviewed policy")
        p.require(".." not in path and "#" not in path, "invalid GitLab request path")
        header = {"JOB-TOKEN": self.job_token} if job_token else {"PRIVATE-TOKEN": self.token}
        request = urllib.request.Request(self.gate["api_url"] + path, headers=header)
        artifact = binary and p.matches(
            rf"{self.root}/jobs/[1-9][0-9]*/artifacts/rc-promotion-manifest[.]json", path)
        opener = self.artifact_opener if artifact else self.opener
        try:
            with opener.open(request, timeout=60) as response:
                raw = response.read(MAX_BYTES + 1)
                p.require(response.status == 200, "unexpected GitLab API response")
        except urllib.error.HTTPError as error:
            error.close()
            raise p.PromotionError("GitLab read failed; check API access or artifact availability") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise p.PromotionError("GitLab read failed; check API access or artifact availability") from None
        p.require(len(raw) <= MAX_BYTES, "GitLab response exceeds the size limit")
        return raw if binary else decode(raw)

    def pages(self, path):
        result = []
        for page in range(1, 11):
            join = "&" if "?" in path else "?"
            items = self.request(f"{self.root}/{path}{join}per_page=100&page={page}")
            p.require(isinstance(items, list), "invalid GitLab page")
            result.extend(items)
            if len(items) < 100:
                return result
        raise p.PromotionError("GitLab pagination limit exceeded")

    def job(self, item, context, name, status):
        pipeline = item.get("pipeline", {})
        p.require(positive(item.get("id")) and item.get("name") == name and
                  item.get("status") == status and item.get("tag") is context["tag_pipeline"] and
                  item.get("ref") == context["ref"] and
                  item.get("commit", {}).get("id") == context["commit"] and
                  pipeline.get("id") == context["pipeline_id"] and
                  pipeline.get("project_id") == self.gate["project_id"] and
                  pipeline.get("sha") == context["commit"] and
                  pipeline.get("ref") == context["ref"] and not item.get("erased_at"),
                  "GitLab job does not match the protected pipeline")
        return item

    def context(self, operation, env):
        p.require(env.get("CI_API_V4_URL") == self.gate["api_url"] and
                  integer(env.get("CI_PROJECT_ID")) == self.gate["project_id"] and
                  env.get("CI_COMMIT_REF_PROTECTED") == "true",
                  "job is not in the reviewed project on a protected ref")
        verifying = operation == "verify"
        rc = env.get("CI_COMMIT_TAG", "")[1:] if verifying else env.get("RC_VERSION")
        p.version(rc)
        p.require(p.matches(p.SHA, env.get("CI_COMMIT_SHA")), "invalid pipeline commit")
        context = {"tag": f"v{rc}", "rc_version": rc, "commit": env["CI_COMMIT_SHA"],
                   "ref": env.get("CI_COMMIT_TAG") if verifying else env.get("CI_COMMIT_BRANCH"),
                   "tag_pipeline": verifying,
                   "pipeline_id": integer(env.get("CI_PIPELINE_ID")),
                   "job_id": integer(env.get("CI_JOB_ID"))}
        project = self.request(self.root)
        p.require(project.get("id") == self.gate["project_id"] and
                  not project.get("forked_from_project") and project.get("archived") is False,
                  "forked or archived projects cannot publish releases")
        if verifying:
            p.require(context["ref"] == context["tag"], "a canonical vX.Y.Z-rc.N tag is required")
        else:
            branch = project.get("default_branch")
            p.require(branch and context["ref"] == branch and not env.get("CI_COMMIT_TAG"),
                      "promotion must run on the protected default branch, not an RC tag")
            live = self.request(f"{self.root}/repository/branches/{urllib.parse.quote(branch, safe='')}")
            p.require(live.get("protected") is True and
                      live.get("commit", {}).get("id") == context["commit"],
                      "default branch changed or is not protected")
        pipeline = self.request(f"{self.root}/pipelines/{context['pipeline_id']}")
        p.require(pipeline.get("id") == context["pipeline_id"] and
                  pipeline.get("project_id") == self.gate["project_id"] and
                  pipeline.get("source") in ({"push"} if verifying else {"web", "api"}) and
                  pipeline.get("ref") == context["ref"] and
                  pipeline.get("sha") == context["commit"] and pipeline.get("status") == "running",
                  "pipeline source, ref or commit does not match the allowed operation")
        remote_tag = self.request(f"{self.root}/repository/tags/{context['tag']}")
        rc_commit = remote_tag.get("commit", {}).get("id")
        p.require(remote_tag.get("name") == context["tag"] and remote_tag.get("protected") is True and
                  p.matches(p.SHA, rc_commit) and (not verifying or rc_commit == context["commit"]),
                  "GitLab RC tag is unprotected or has changed")
        context["rc_commit"] = rc_commit
        current = self.job(self.request("/job", job_token=True), context,
                           f"{operation}-rc", "running")
        p.require(current["id"] == context["job_id"], "CI_JOB_ID differs from job-token identity")
        context["pipeline_user_id"] = pipeline.get("user", {}).get("id")
        context["job_user_id"] = current.get("user", {}).get("id")
        return context, project

    def check_policy(self, path, document, context, github):
        p.require(not Path(path).is_absolute() and ".." not in Path(path).parts and
                  p.matches(r"\.(?:gitlab|github)/[A-Za-z0-9_-]+\.json", path),
                  "policy must be a repository-relative reviewed JSON file")
        encoded = urllib.parse.quote(path, safe="")
        raw = self.request(f"{self.root}/repository/files/{encoded}/raw?ref={context['commit']}",
                           binary=True)
        p.require(decode(raw) == document, "local policy differs from the pipeline commit")
        remote = github.request(f"{github.root}/contents/{path}?ref={context['commit']}")
        p.require(remote.get("encoding") == "base64" and
                  decode(base64.b64decode(remote["content"])) == document,
                  "GitHub and GitLab RC policies differ")
        if not context["tag_pipeline"]:
            repo = github.request(github.root)
            p.require(repo.get("default_branch") == context["ref"], "GitHub default branch differs")
            branch = urllib.parse.quote(context["ref"], safe="")
            current = github.request(f"{github.root}/branches/{branch}")
            p.require(current.get("protected") is True and
                      current.get("commit", {}).get("sha") == context["commit"],
                      "GitHub default branch changed or is not protected")

    def producer(self, context, document, job_id):
        return {"kind": "gitlab", "api_url": self.gate["api_url"],
                "project_id": self.gate["project_id"], "pipeline_id": context["pipeline_id"],
                "job_id": job_id, "job_name": "verify-rc", "tag": context["tag"],
                "commit": context["commit"], "policy_digest": charts.digest(p.canonical(document))}

    def verified_artifact(self, context, path, github):
        reservation = github.release(p.version(context["rc_version"]))
        reserved = None
        if reservation:
            intent = github.intent(reservation)
            reserved = intent.get("producer", {})
            p.require(intent.get("rc_version") == context["rc_version"] and
                      intent.get("commit") == context["rc_commit"] and
                      reserved.get("kind") == "gitlab" and
                      reserved.get("api_url") == self.gate["api_url"] and
                      reserved.get("project_id") == self.gate["project_id"] and
                      positive(reserved.get("pipeline_id")) and positive(reserved.get("job_id")),
                      "stable release is reserved for another RC or artifact producer")
            pipeline_id = reserved["pipeline_id"]
            job_id = reserved["job_id"]
        else:
            pipelines = self.pages(f"pipelines?ref={context['tag']}&sha={context['rc_commit']}&source=push")
            found = []
            for pipeline in pipelines:
                if (pipeline.get("ref") != context["tag"] or
                        pipeline.get("sha") != context["rc_commit"] or
                        pipeline.get("status") != "success"):
                    continue
                p.require(positive(pipeline.get("id")), "invalid verification pipeline ID")
                jobs = self.pages(f"pipelines/{pipeline['id']}/jobs?include_retried=false")
                verified = [job for job in jobs if job.get("name") == "verify-rc"]
                p.require(len(verified) <= 1, "ambiguous verification jobs in producer pipeline")
                if verified and verified[0].get("status") == "success":
                    found.append((pipeline["id"], verified[0]["id"]))
            p.require(found, "no successful RC push pipeline with a successful verify-rc job")
            pipeline_id, job_id = max(found)
        p.require(positive(job_id), "invalid verification job ID")
        pipeline = self.request(f"{self.root}/pipelines/{pipeline_id}")
        p.require(pipeline.get("id") == pipeline_id and
                  pipeline.get("project_id") == self.gate["project_id"] and
                  pipeline.get("source") == "push" and pipeline.get("status") == "success" and
                  pipeline.get("ref") == context["tag"] and pipeline.get("sha") == context["rc_commit"],
                  "producer is not a successful exact RC tag push pipeline")
        source = {"pipeline_id": pipeline_id, "commit": context["rc_commit"],
                  "ref": context["tag"], "tag": context["tag"], "tag_pipeline": True}
        job = self.job(self.request(f"{self.root}/jobs/{job_id}"), source, "verify-rc", "success")
        p.require(job["id"] == job_id, "verification job identity changed")
        p.require((job.get("artifacts_file") or {}).get("size", 0) > 0,
                  "successful verify-rc job has no downloadable artifact")
        raw = self.request(f"{self.root}/jobs/{job_id}/artifacts/{MANIFEST}", binary=True)
        wrapper = decode(raw)
        p.require(isinstance(wrapper, dict) and set(wrapper) == {
            "schema", "producer", "source_publisher_sha", "manifest",
        } and type(wrapper["schema"]) is int and wrapper["schema"] == 1,
                  "invalid GitLab RC evidence envelope")
        encoded = urllib.parse.quote(path, safe="")
        document = decode(self.request(
            f"{self.root}/repository/files/{encoded}/raw?ref={context['rc_commit']}", binary=True))
        source_config, source_gate = policy(document)
        p.require(all(source_gate[key] == self.gate[key] for key in ("api_url", "project_id", "repository")),
                  "source RC policy belongs to another project")
        p.require(wrapper["producer"] == self.producer(source, document, job_id),
                  "manifest producer does not match the successful verify-rc job")
        p.require(reserved is None or reserved == wrapper["producer"], "reserved producer changed")
        proof = wrapper["manifest"]
        p.require(isinstance(proof, dict), "invalid verified manifest")
        p.manifest(proof, source_config, self.gate["repository"], context["rc_version"],
                   context["rc_commit"], {"id": proof.get("run_id"), "run_attempt": proof.get("run_attempt")})
        return wrapper

    def approvals(self, context, project, env):
        maintainer = self.gate.get("authorization", "groups") == "maintainer"
        if not maintainer:
            p.require(self.gate["deploy_group_ids"] and self.gate["approver_group_ids"],
                      "stable promotion is not configured: explicit deployment and approval groups are required")
            p.require(project.get("allow_pipeline_trigger_approve_deployment") is False,
                      "group promotion requires no self-approval")
        p.require(env.get("CI_JOB_MANUAL") == "true" and
                  env.get("CI_ENVIRONMENT_NAME") == p.ENVIRONMENT,
                  "promotion requires a manual stable-release deployment")
        protected = self.request(f"{self.root}/protected_environments/{p.ENVIRONMENT}")
        p.require(protected.get("name") == p.ENVIRONMENT, "wrong protected environment")

        def groups(items, allowed):
            p.require(isinstance(items, list) and items, "protected environment has no group gates")
            result = {}
            for item in items:
                group = item.get("group_id")
                p.require(positive(group) and group in allowed and not item.get("user_id") and
                          group not in result and item.get("group_inheritance_type", 0) in {0, 1},
                          "protected environment must contain only reviewed QA/dev group gates")
                result[group] = item
            return result

        if maintainer:
            levels = protected.get("deploy_access_levels")
            p.require(isinstance(levels, list) and len(levels) == 1 and
                      isinstance(levels[0], dict) and type(levels[0].get("access_level")) is int and
                      levels[0]["access_level"] == 40 and levels[0].get("group_id") is None and
                      levels[0].get("user_id") is None,
                      "stable-release must allow only the native Maintainer deploy role")
            p.require(protected.get("approval_rules", []) == [] and
                      protected.get("required_approval_count", 0) == 0,
                      "maintainer authorization requires no deployment approval rules")
            for field in ("pipeline_user_id", "job_user_id"):
                user = context.get(field)
                p.require(positive(user), f"missing {field} evidence")
                member = self.request(f"{self.root}/members/all/{user}")
                p.require(member.get("id") == user and member.get("state") == "active" and
                          member.get("membership_state", "active") == "active" and
                          type(member.get("access_level")) is int and member["access_level"] >= 40,
                          "pipeline and manual-job users must be active project Maintainers or Owners")
        else:
            groups(protected.get("deploy_access_levels"), self.gate["deploy_group_ids"])
            rules = groups(protected.get("approval_rules"), self.gate["approver_group_ids"])
            for rule in rules.values():
                p.require(positive(rule.get("required_approvals")), "deployment rule requires no approvals")
        deployments = self.pages(f"deployments?environment={p.ENVIRONMENT}&status=running&order_by=id&sort=desc")
        found = [item for item in deployments if (item.get("deployable") or {}).get("id") == context["job_id"]]
        p.require(len(found) == 1 and positive(found[0].get("id")),
                  "no unique deployment for the current promotion job")
        deployment = self.request(f"{self.root}/deployments/{found[0]['id']}")
        deployable = deployment.get("deployable") or {}
        p.require(deployment.get("id") == found[0]["id"] and
                  deployable.get("id") == context["job_id"] and
                  deployable.get("pipeline", {}).get("id") == context["pipeline_id"] and
                  deployment.get("environment", {}).get("name") == p.ENVIRONMENT and
                  deployment.get("ref") == context["ref"] and
                  deployment.get("sha") == context["commit"] and
                  deployment.get("status") == "running" and
                  deployment.get("pending_approval_count") == 0,
                  "deployment is not approved for this exact RC job")
        if maintainer:
            return {"deployment_id": deployment["id"], "job_id": context["job_id"],
                    "pipeline_id": context["pipeline_id"], "authorization": "maintainer",
                    "pipeline_user_id": context["pipeline_user_id"], "job_user_id": context["job_user_id"]}
        # An independent approver may also press Play on the approved job.
        triggerer = context.get("pipeline_user_id")
        p.require(positive(triggerer), "missing pipeline triggerer evidence")
        summary = groups(deployment.get("approval_summary", {}).get("rules"), rules)
        p.require(set(summary) == set(rules), "deployment approvals differ from live protection rules")
        approved = set()
        for group, rule in summary.items():
            expected = rules[group]
            p.require(rule.get("required_approvals") == expected["required_approvals"],
                      "deployment approval requirements changed")
            approvals = rule.get("deployment_approvals")
            p.require(isinstance(approvals, list), "deployment has no approval evidence")
            counted = set()
            for approval in approvals:
                user = approval.get("user", {}).get("id")
                p.require(approval.get("status") == "approved" and positive(user) and
                          user != triggerer and user not in approved and
                          approval.get("user", {}).get("state") == "active",
                          "deployment approval is missing, rejected, duplicated or self-approved")
                counted.add(user)
                approved.add(user)
            p.require(len(counted) >= expected["required_approvals"], "required deployment approvals missing")
        return {"deployment_id": deployment["id"], "job_id": context["job_id"],
                "pipeline_id": context["pipeline_id"], "approved_by": sorted(approved)}


def verify(config, document, gitlab, context, github, registry, authenticate=None):
    commit, run, publisher = github.source_run(config, context["rc_version"])
    p.require(commit == context["commit"], "GitHub RC tag differs from GitLab pipeline SHA")
    if authenticate:
        authenticate()
    evidence = p.capture(config, registry, {
        "repository": github.repository, "rc_version": context["rc_version"], "commit": commit,
        "run_id": run["id"], "run_attempt": run["run_attempt"],
        "source_workflow": config["source_workflow"],
    })
    p.manifest(evidence, config, github.repository, context["rc_version"], commit, run)
    return {"schema": 1, "producer": gitlab.producer(context, document, context["job_id"]),
            "source_publisher_sha": publisher, "manifest": evidence}


def evidence(config, gitlab, context, github, policy_path):
    wrapper = gitlab.verified_artifact(context, policy_path, github)
    proof = wrapper["manifest"]
    p.require(isinstance(proof, dict), "invalid verified manifest")
    commit, run, publisher = github.source_run(config, context["rc_version"], selected={
        "id": proof.get("run_id"), "run_attempt": proof.get("run_attempt"),
    })
    p.require(commit == context["rc_commit"] and publisher == wrapper["source_publisher_sha"],
              "verified GitHub source changed")
    p.manifest(proof, config, github.repository, context["rc_version"], commit, run)
    return wrapper


def promote(config, gitlab, context, project, env, github, registry, report_path,
            policy_path=POLICY_PATH, authenticate=None, write_access=None):
    approval = gitlab.approvals(context, project, env)
    with ExitStack() as credentials:
        if write_access:
            token = credentials.enter_context(write_access())
            previous = github.write_token
            github.write_token = token
            credentials.callback(setattr, github, "write_token", previous)
            github.write_environment()
        wrapper = evidence(config, gitlab, context, github, policy_path)
        if authenticate:
            authenticate()
        try:
            return p.promote(config, wrapper["manifest"], github, registry,
                             f"gitlab-{gitlab.gate['project_id']}-{context['job_id']}", report_path,
                             source_publisher_sha=wrapper["source_publisher_sha"],
                             producer=wrapper["producer"])
        finally:
            if report_path.exists():
                report = decode(report_path.read_bytes())
                report["gitlab_approval"] = approval
                report_path.write_bytes(p.canonical(report))


@contextmanager
def read_token_environment(token):
    previous = os.environ.get("GH_TOKEN")
    os.environ["GH_TOKEN"] = token
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("GH_TOKEN", None)
        else:
            os.environ["GH_TOKEN"] = previous


def app_credentials(prefix):
    app = {name: os.environ.pop(f"{prefix}_{name.upper()}", None) for name in (
        "app_id", "app_installation_id", "app_private_key", "app_private_key_file",
    )}
    if all(value is None for value in app.values()):
        return None
    p.require(all(p.matches(r"[1-9][0-9]{0,18}", app[name]) for name in (
        "app_id", "app_installation_id",
    )), f"{prefix} requires both App and installation IDs")
    keys = [app[name] for name in ("app_private_key", "app_private_key_file")
            if app[name] is not None]
    p.require(len(keys) == 1 and bool(keys[0].strip()),
              f"{prefix} requires exactly one nonempty App private key source")
    return {"app_id": app["app_id"], "installation_id": app["app_installation_id"],
            "private_key": app["app_private_key"], "private_key_file": app["app_private_key_file"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "promote"))
    parser.add_argument("--policy", default=POLICY_PATH)
    args = parser.parse_args()
    document = decode(Path(args.policy).read_bytes())
    config, gate = policy(document)
    # The privileged tokens must not be inherited by registry or parser commands.
    write_token = os.environ.pop("RC_PROMOTION_GITHUB_TOKEN", "")
    app = app_credentials("RC_PROMOTION")
    read_app = app_credentials("RC_VERIFICATION")
    p.require(not (app and write_token), "configure only one GitHub promotion credential")
    # Legacy single-App callers can still request a read-scoped token.
    read_app = read_app or (app if not os.environ.get("GH_TOKEN") else None)
    gitlab = GitLab(gate, os.environ.pop("GITLAB_RELEASE_READ_TOKEN", ""),
                    os.environ.pop("CI_JOB_TOKEN", ""))
    github = p.GitHub(gate["repository"])
    context, project = gitlab.context(args.operation, os.environ)
    with ExitStack() as credentials, tempfile.TemporaryDirectory(prefix="gitlab-promotion-") as auth:
        if read_app:
            token = credentials.enter_context(installation_token(github.repository, mode="read", **read_app))
            credentials.enter_context(read_token_environment(token))
        p.require(os.environ.get("GH_TOKEN"), "an approved GitHub read credential is required")
        gitlab.check_policy(args.policy, document, context, github)
        os.environ["PROMOTION_AUTH_ROOT"] = auth
        for key, name in (("HELM_CONFIG_HOME", "config"), ("HELM_CACHE_HOME", "cache"),
                          ("HELM_DATA_HOME", "data")):
            os.environ[key] = str(Path(auth) / "helm" / name)
        os.environ["HELM_REPOSITORY_CONFIG"] = str(Path(auth) / "helm/config/repositories.yaml")
        os.environ["HELM_REPOSITORY_CACHE"] = str(Path(auth) / "helm/cache/repository")
        if args.operation == "verify":
            result = verify(config, document, gitlab, context, github,
                            p.Registry(config["source_ngc_path"]),
                            authenticate=lambda: p.authenticate_registries(config, capture_only=True))
            Path(MANIFEST).write_bytes(p.canonical(result))
        else:
            promote(config, gitlab, context, project, os.environ, github,
                    p.Registry(config["source_ngc_path"], config["destination_ngc_path"]),
                    Path(RESULT), args.policy, authenticate=lambda: p.authenticate_registries(config),
                    write_access=(lambda: installation_token(github.repository, mode="write", **app))
                    if app else lambda: nullcontext(write_token))


if __name__ == "__main__":
    try:
        main()
    except (p.PromotionError, charts.InvalidChart, GitHubAppError) as error:
        print(f"GitLab promotion stopped: {error}")
        raise SystemExit(1) from None
    except Exception:
        print("GitLab promotion stopped: unexpected response or tool failure")
        raise SystemExit(1) from None
