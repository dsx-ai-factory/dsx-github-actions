# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Select an RC with an annotated stable Git tag, without registry access."""

import argparse
import base64
import json
import os
import urllib.parse

import promotion as p

KIND = "dsx-rc-tag-promotion"
FIELDS = {
    "schema", "kind", "repository", "rc_version", "stable_version", "commit",
    "source_workflow", "source_run_id", "source_run_attempt", "source_publisher_sha",
}


def policy(document, repository):
    # Validate the shared policy rather than silently discarding unknown fields.
    from gitlab import policy as gitlab_policy
    config, gate = gitlab_policy(document)
    p.require(gate["repository"] == repository, "policy belongs to another GitHub repository")
    p.require(gate.get("stable_tag_pipeline") is True,
              "this commit does not enable the GitLab stable tag pipeline")
    return config


def load_policy(github, path, commit, *, full=False):
    from gitlab import decode
    p.require(p.matches(r"\.(?:github|gitlab)/[A-Za-z0-9_-]+\.json", path),
              "policy must be a reviewed repository-relative JSON path")
    p.require(p.matches(p.SHA, commit), "invalid policy commit")
    encoded = urllib.parse.quote(path, safe="/")
    contents = github.request(f"{github.root}/contents/{encoded}?ref={commit}")
    p.require(contents.get("encoding") == "base64" and
              type(contents.get("size")) is int and 0 < contents["size"] <= 1024 * 1024,
              "invalid promotion policy contents")
    document = decode(base64.b64decode(contents["content"]))
    config = policy(document, github.repository)
    return document if full else config


def read_tag(github, stable):
    """Return the immutable RC selection, independent of GitHub release creation."""
    p.require(p.matches(p.STABLE, stable), "invalid stable version")
    ref = github.request(f"{github.root}/git/ref/tags/v{stable}", absent=True)
    if ref is None:
        return None
    obj = ref.get("object", {})
    p.require(obj.get("type") == "tag" and p.matches(p.SHA, obj.get("sha")),
              "stable tag must be an annotated RC selection")
    tag = github.request(f"{github.root}/git/tags/{obj['sha']}")
    message = tag.get("message")
    p.require(isinstance(message, str) and len(message) <= 8192,
              "invalid stable tag annotation")
    try:
        intent = json.loads(message)
    except ValueError:
        raise p.PromotionError("invalid stable tag annotation") from None
    p.require(isinstance(intent, dict) and set(intent) == FIELDS and
              message == p.canonical(intent).decode(), "invalid stable tag metadata fields")
    p.require(type(intent["schema"]) is int and intent["schema"] == 1 and
              intent["kind"] == KIND and intent["repository"] == github.repository and
              p.version(intent["rc_version"]) == stable == intent["stable_version"] and
              p.matches(p.SHA, intent["commit"]) and
              p.matches(p.SHA, intent["source_publisher_sha"]) and
              p.matches(r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", intent["source_workflow"]) and
              all(type(intent[key]) is int and intent[key] > 0
                  for key in ("source_run_id", "source_run_attempt")),
              "invalid stable tag metadata identity")
    target = tag.get("object", {})
    p.require(tag.get("sha") == obj["sha"] and tag.get("tag") == f"v{stable}" and
              target.get("type") == "commit" and target.get("sha") == intent["commit"],
              "stable tag does not point directly to the selected RC commit")
    return intent


def promote(document, github, rc, policy_path=p.POLICY_PATH):
    config = policy(document, github.repository)
    stable = p.version(rc)
    existing = read_tag(github, stable)
    p.require(existing is None or existing["rc_version"] == rc,
              "stable version already selects another RC")
    selected = ({"id": existing["source_run_id"], "run_attempt": existing["source_run_attempt"]}
                if existing else None)
    commit, run, publisher = github.source_run(config, rc, selected=selected)
    p.require(existing is not None or run["run_attempt"] == 1,
              "RC publishing was rerun; create a new RC before stable tagging")
    p.require(load_policy(github, policy_path, commit, full=True) == document,
              "RC and protected default-branch promotion policies differ; "
              "create a new RC with the reviewed policy")
    intent = {
        "schema": 1, "kind": KIND, "repository": github.repository,
        "rc_version": rc, "stable_version": stable, "commit": commit,
        "source_workflow": config["source_workflow"],
        "source_run_id": run["id"], "source_run_attempt": run["run_attempt"],
        "source_publisher_sha": publisher,
    }
    p.require(existing is None or existing == intent, "stable tag selection has changed")
    release = github.release(stable)
    if release:
        p.require(existing is not None and release.get("prerelease") is False and
                  release.get("draft") is False,
                  "stable release exists without a completed tag-only selection")
        return intent
    # Notes must succeed before the tag becomes visible to the mirror.
    notes = github.release_notes(stable, commit)
    p.require(github.tag(f"v{rc}") == commit, "RC tag changed before stable tagging")
    if existing is None:
        tag = github.request(f"{github.root}/git/tags", "POST", {
            "tag": f"v{stable}", "message": p.canonical(intent).decode(),
            "object": commit, "type": "commit",
        })
        p.require(p.matches(p.SHA, tag.get("sha")), "invalid created tag object")
        github.request(f"{github.root}/git/refs", "POST", {
            "ref": f"refs/tags/v{stable}", "sha": tag["sha"],
        })
    p.require(read_tag(github, stable) == intent, "stable tag changed before release creation")
    github.request(f"{github.root}/releases", "POST", {
        "tag_name": f"v{stable}", "target_commitish": commit, "name": f"v{stable}",
        "draft": False, "prerelease": False, "make_latest": "false", "body": notes,
    })
    return intent


def main():
    write_token = os.environ.pop("RC_PROMOTION_GITHUB_TOKEN", "")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=p.POLICY_PATH)
    args = parser.parse_args()
    p.require(os.environ.get("DSX_RELEASE_MODE") == "rc-promotion", "promotion activation gate is off")
    github = p.GitHub(os.environ["GITHUB_REPOSITORY"], write_token=write_token)
    github.write_environment()
    context = {
        "event": os.environ["GITHUB_EVENT_NAME"], "ref": os.environ["GITHUB_REF"],
        "sha": os.environ["GITHUB_SHA"], "protected": os.environ["GITHUB_REF_PROTECTED"],
    }
    document = load_policy(github, args.policy, context["sha"], full=True)
    config = policy(document, github.repository)
    github.gates(config, context)
    intent = promote(document, github, os.environ["RC_VERSION"], args.policy)
    print(f"Created v{intent['stable_version']} from v{intent['rc_version']} at {intent['commit']}")


if __name__ == "__main__":
    try:
        main()
    except p.PromotionError as error:
        print(f"Stable tagging stopped: {error}")
        raise SystemExit(1) from None
    except Exception:
        print("Stable tagging stopped: unexpected response or tool failure")
        raise SystemExit(1) from None
