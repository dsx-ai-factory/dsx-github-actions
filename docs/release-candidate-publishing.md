# Enable RC Releases with DSX GitHub Actions

Use this guide to enable release-candidate (RC) tags and publish matching
images and Helm charts from a GitHub-hosted component. **dsx-exchange is the
worked example**, not a requirement to use its component names or version.

Start with the recommended shared workflow for source RCs. Your repository
still owns branch protection, approved source/tests, workflow triggers,
publishing permissions and artifact paths. A Git tag alone is not a deployable
release: the matching charts and images must also be published and verified
by product-native jobs. The Exchange step-by-step guide remains available for
advanced composition with repository-owned plugins or custom release policy.

- [Recommended Minimal Workflow](#recommended-minimal-workflow)
- [What You Will Get](#what-you-will-get)
- [Advanced Composition](#advanced-composition)
- [Onboard Your Repository](#onboard-your-repository)
- [Contract](#contract)
- [Release Job](#release-job)
- [Image Job](#image-job)
- [Helm Job](#helm-job)
- [Verify Your First RC](#verify-your-first-rc)
- [Reruns](#reruns)
- [Hand Off to the SBOM](#hand-off-to-the-sbom)
- [Next Release Cycle](#next-release-cycle)
- [Troubleshooting](#troubleshooting)

## Recommended Minimal Workflow

Add `.github/workflows/release-rc.yml` in the component repository:

```yaml
name: Release Candidate

on:
  push:
    branches:
      - release/1.2.3

permissions:
  contents: write

jobs:
  release:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/release-candidate.yml@REPLACE_WITH_REVIEWED_COMMIT_SHA
```

Replace `release/1.2.3` with your explicit release target. The literal
`REPLACE_WITH_REVIEWED_COMMIT_SHA` is a placeholder, not a usable ref: replace
it with the full reviewed commit SHA containing the new workflow, helpers and
checks once that commit is available. Older pins in the advanced examples do
not provide this workflow. The shared implementation uses the called job's
`job.workflow_repository` and `job.workflow_sha`, so it needs no circular
self-pin to its own future commit.

No Node/package files, `.releaserc` edits or custom scripts are needed in the
product repository for this source-only path. No access to private dependency
repositories or NGC credentials is required. The workflow installs its own tooling and does
not build or publish images/charts, create stable releases or update major
tags such as `v1`.

### Prerequisites and Scope

- Use GitHub.com, where the called job exposes `job.workflow_repository` and
  `job.workflow_sha`. This implementation is not a GHES workflow.
- Protect the exact `release/X.Y.Z` branch with PR review, Code Owner approval
  and required product tests. The source commit must be approved and tested;
  central RC contract checks do not run the product's test suite.
- Grant `contents: write` to `GITHUB_TOKEN` for GitHub prereleases. For Git
  tags and channel notes, authorize either this token or the optional
  repository Deploy Key described below. Configure tag rules to prevent
  unauthorized creation or movement; do not weaken existing protections.
- The pushed commit must still be the current remote release-branch head.
  Old runs are rejected after the branch advances. Choose a target consistent
  with stable history and release-worthy Conventional Commits; naming a
  branch does not force that version or a release.
- Use an approved Linux runner with Git supporting non-cone sparse checkout,
  Bash, `gh`, `jq` and access to GitHub and the shared tool dependencies.
  `runner` defaults to `ubuntu-latest`. Shared checks run on `ubuntu-latest`.
- Keep stable and RC publishing triggers disjoint. The optional
  `default-branch` input defaults to the caller repository's default branch
  and supplies stable history, not permission to publish stable releases.

### Optional Deploy Key Authentication

Some repositories, including Exchange, permit release-tag writes only through
an approved Deploy Key. `contents: write` on `GITHUB_TOKEN` does not override
those tag rules. Pass the existing repository-scoped, write-enabled deploy key
to the reusable release job:

```yaml
jobs:
  release:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/release-candidate.yml@REPLACE_WITH_REVIEWED_COMMIT_SHA
    permissions:
      contents: write
    secrets:
      release-deploy-key: ${{ secrets.RELEASE_DEPLOY_KEY }}
```

Pin a reviewed commit containing this optional-secret support. Register the
public key as a write-enabled deploy key for the product repository, store the
private key in the caller's repository or scoped organization secret, and
authorize that Deploy Key in the applicable tag rules. Do not relax tag rules
to make the workflow pass. Environment secrets cannot be passed to a reusable
workflow through this caller-level `secrets` mapping.

The source checkout uses SSH and semantic-release receives the canonical
`git@github.com:OWNER/REPO.git` URL for Git operations. `GITHUB_TOKEN` remains
the credential for GitHub Release API calls. Only a boolean indicating that
the key exists reaches the configuration helper; the key is not printed or
written to release configuration. The shared-code checkout and central tests
do not receive the key.

Before publishing, a one-minute SSH `git push --dry-run` checks the key's
push access without changing remote refs. An invalid or read-only key stops
the workflow before semantic-release can fall back to HTTPS. `GH_TOKEN` is
set to the workflow token, so a runner's inherited token cannot override the
GitHub Release API credential. This preflight verifies authentication, not
whether every tag-specific rule will accept the eventual release.

Omit this secret in repositories whose rules already permit the workflow token.
Their existing HTTPS behavior is unchanged. This option does not alter stable
publishing or grant additional permissions. Verify the first real RC and its
rerun in the product repository: local contract tests cannot prove live key
validity or ruleset authorization.

### Shared Release Contract

The [workflow](../.github/workflows/release-candidate.yml) runs
[central RC checks](../.github/workflows/release-candidate-checks.yml) before
the publishing job. Checks and shared helpers are checked out from the
called workflow's repository and SHA, not the caller's product SHA or a
moving shared branch.

The disposable `release-source` checkout has full Git history but only
`/.github/` in a non-cone sparse working tree. Product root configuration and
package files are absent. Shared code lives in the sibling `dsx-rc-actions`
checkout, and generates RC-only configuration in `release-source`; the
product's stable-release configuration remains unchanged.

Publishing accepts only a push to the protected, current `release/X.Y.Z`
head. The version must be `X.Y.Z-rc.N`, with a positive sequence and no leading
zeros. The guard checks the preview and runs again as semantic-release's
`verifyRelease` plugin during actual publication, before creating a tag.
Reruns validate the existing tag's target and `rc` channel metadata before
reusing it. A missing GitHub prerelease can be recovered for a verified tag;
an existing draft or non-prerelease is rejected, not overwritten.

### Outputs for Product Publishing Jobs

| Output | Meaning |
| --- | --- |
| `should-publish` | `true` only after the source RC and GitHub prerelease are verified; otherwise do not publish artifacts. |
| `version` | Version without `v`, for example `1.2.3-rc.1`. |
| `tag` | Source Git tag, for example `v1.2.3-rc.1`. |
| `reused-existing-tag` | `true` when this run reused an RC tag on the source commit. |
| `release-url` | Verified GitHub prerelease URL. |

Keep NGC publishing in existing product-native jobs in the same caller
workflow. Add `needs: release`, gate on
`needs.release.outputs.should-publish == 'true'`, check out
`needs.release.outputs.tag` and use `needs.release.outputs.version` for all
artifacts. Registry credentials, environments, artifact layouts and
revision/duplicate verification stay with those jobs. The advanced examples
below alias the gate to `publish-rc`; use `should-publish` with the reusable
workflow's outputs instead.

For source-only acceptance, confirm central checks succeeded, the tag points
to the approved commit, the GitHub release is a non-draft prerelease, and a
rerun of the still-current commit reuses the tag. Product artifact acceptance
is separate. See the [workflow reference](../.github/workflows/README.md#release-candidate-release-candidateyml)
and [helper reference](../.github/actions/release-candidate/README.md).

## What You Will Get

Exchange [PR #109](https://github.com/dsx-ai-factory/dsx-exchange/pull/109)
implemented this pattern on `release/2.9.3`. The
[successful run](https://github.com/dsx-ai-factory/dsx-exchange/actions/runs/34188354559)
created [v2.9.3-rc.1](https://github.com/dsx-ai-factory/dsx-exchange/releases/tag/v2.9.3-rc.1)
and published these artifacts at version `2.9.3-rc.1` to NGC `components-dev`:

| Artifact | Names |
| --- | --- |
| Helm charts | `auth-callout`, `nats-event-bus`, `dsx-agent-gateway` |
| Images, both `linux/amd64` and `linux/arm64` | `auth-callout`, `dsx-agentgateway-bridge` |

The following table describes that Exchange implementation, not a universal
release policy for every DSX repository:

| Event | Git tag | Artifact behavior |
| --- | --- | --- |
| PR before merge | No release tag | Exchange CI builds/tests without publishing its validation images. |
| Release-worthy changes merged to configured `release/2.9.3` | `v2.9.3-rc.1`, `v2.9.3-rc.2`, etc. | Publish matching `2.9.3-rc.N` charts and images. |
| Release-worthy changes merged to `main` | Stable `vX.Y.Z` | Existing stable publishing path, not the RC jobs. |
| Rerun the same RC commit | Reuse its existing RC tag | Verify and reuse matching artifacts; finish any missing artifacts. |

Not every merge creates a tag. semantic-release calculates the version from
release history and Conventional Commits. It does **not** pick the
highest-numbered release branch. The branch-target check below rejects a
calculated version that does not match the intended release line.

This guide adds RC publishing without changing stable-release policy. If your
repository requires development-only builds on `main` and final releases from
a release branch, align that policy separately; do not assume the Exchange
example already implements it.

## Advanced Composition

The existing Exchange guide below is for repositories that need to own their
semantic-release plugins or custom release policy. Its `.releaserc` changes,
Node setup and scripts are **not** prerequisites for the recommended shared
workflow. These composable examples do not inherit the shared workflow's
central checks, isolated configuration or actual-publication version guard;
repositories choosing this path own those controls as well as artifact jobs.

## Onboard Your Repository

These steps describe the advanced composition path.

### 1. Check Access and Choose a Release Line

- Have a maintainer configure the release branch and tag rules. Require PRs,
  Code Owner approval and the relevant CI checks. Allow only the approved
  release identity to create release tags; do not disable protections to make
  a workflow pass.
- Confirm runner availability, registry connectivity and the tools your build
  needs. Exchange uses `linux-amd64-cpu4`; use a runner approved for your repo.
- For NGC publishing, configure a `components-dev` GitHub environment restricted
  to the protected release branch and a scoped environment secret named
  `NGC_DSX_COMPONENTS_PUSH_KEY`. Configure this through your approved credential
  process. Never commit the value or expose it to PR validation jobs.
- Choose an explicit release target. For example, Exchange's next patch after
  `2.9.2` was `2.9.3`, so its active branch was `release/2.9.3`. Use your own
  release history and intended changes, not Exchange's number.
- Cut the protected release branch from the approved source commit. Put the
  onboarding changes in a PR targeting that branch. A branch cut at an already
  released commit needs a new **release-worthy** change before semantic-release
  creates an RC; a documentation-only commit may produce no release.

Do not enable a second workflow that also publishes the same release. If the
repository already has a release workflow, integrate the steps into it or make
the old and new triggers disjoint.

### 2. Configure semantic-release

In the component repository, add the **one active release branch** to
`.releaserc.json`. Keep `main` and preserve the repository's existing plugins
and release rules. This minimal configuration illustrates the Exchange
`2.9.3` release line:

```json
{
  "branches": [
    "main",
    {"name": "release/2.9.3", "channel": "rc", "prerelease": "rc"}
  ],
  "tagFormat": "v${version}",
  "plugins": [
    ["@semantic-release/commit-analyzer", {"preset": "conventionalcommits"}],
    ["@semantic-release/release-notes-generator", {"preset": "conventionalcommits"}],
    ["@semantic-release/github", {"successComment": false, "failComment": false}]
  ]
}
```

Set `prerelease: "rc"` explicitly. Do not derive it from a branch name containing
`/`, and do not configure multiple release branches with the same static `rc`
identifier. The `branches` and `tag-format` action inputs can override this
file: leave them unset when the file is your source of truth.

### 3. Add the Workflow and Publishing Jobs

Use the [Release Job](#release-job) below as a separate RC-only workflow in
`.github/workflows/release-rc.yml`. Keep your stable-release workflow separate
and exclude this release branch from its triggers. Then add the
[Image Job](#image-job) and [Helm Job](#helm-job) under the RC workflow's `jobs`
mapping. Those publishing blocks
are templates: replace the component paths, registry destination and verifier
with your repository's values before enabling them.

For a complete, previously executed example, use these immutable Exchange
references rather than copying a moving branch:

| File | What to adapt |
| --- | --- |
| [.releaserc.json](https://github.com/dsx-ai-factory/dsx-exchange/blob/71ec461908b90bc11dc0ce7c95b9cb40da7bf7ce/.releaserc.json) | Your active release branch and existing plugins. |
| [.github/workflows/release.yml](https://github.com/dsx-ai-factory/dsx-exchange/blob/71ec461908b90bc11dc0ce7c95b9cb40da7bf7ce/.github/workflows/release.yml) | Runner, trigger, approved release identity, image/chart matrices, NGC destination and tool setup. |
| [scripts/verify-rc-image.sh](https://github.com/dsx-ai-factory/dsx-exchange/blob/71ec461908b90bc11dc0ce7c95b9cb40da7bf7ce/scripts/verify-rc-image.sh) | Required platforms, revision checks and registry behavior. |

Exchange's `RELEASE_DEPLOY_KEY` is its repository-specific Git push identity,
not a universal requirement. The example below uses `GITHUB_TOKEN` with
`contents: write`; ensure your tag rules permit that identity, or use your
organization's approved release identity. Do not copy someone else's key.

The snippets below pin DSX actions to `3dda6f9`, which includes the shared RC
resolver and subsequent release-action hardening. The linked Exchange workflow
records its original pins. Review action updates as normal dependencies, and
validate the resulting workflow in your own repository before enabling it.

## Contract

For advanced composition, the component repository owns this contract:

- Configure semantic-release in the component repository to create versions in
  the form `MAJOR.MINOR.PATCH-rc.NUMBER` from its release branch.
- Run semantic-release once in publishing mode, then pass its outputs to
  `resolve-release-candidate`. A preceding dry run may validate the version
  without creating a second release.
- Publish only when `should-publish` is `true`.
- Use the normalized `version` output for every image and Helm chart produced by
  the same repository.
- Do not publish `latest` or another moving tag from the RC workflow.
- Keep registry credentials in a GitHub environment secret, restrict that
  environment to the protected release-branch pattern, and grant publishing
  jobs only `contents: read`.
- Enforce PR-only updates and Code Owner approval on every branch that can use
  the publishing environment.
- List the active `release/X.Y.Z` branch explicitly in semantic-release rather
  than combining a branch glob with the static `rc` prerelease identifier.
  Replace that entry each cycle and delete the old release branch after stable
  promotion.
- Run semantic-release in dry-run mode first and verify that its calculated
  version matches `X.Y.Z-rc.N` from the branch name before creating the tag.
- Pin the semantic-release version and every extra plugin, and pin every shared
  DSX action to an immutable commit SHA.
- Stamp every image and chart with the source commit, and fail closed if an
  existing RC artifact does not match that commit.

## Release Job

This is a complete RC-only workflow for `.github/workflows/release-rc.yml`.
Replace the branch and runner for your component. Do not add stable-only steps
such as `Update Major Version Tag`: `new-release-published == 'true'` also
applies to RCs, so that condition alone must not update a stable moving tag such
as `v2`. Leave those steps in the separate stable-release workflow. Keep the
checkout's full tag history so version calculation and reruns work.

```yaml
name: Release Candidate

on:
  push:
    branches: [release/2.9.3]

permissions:
  contents: read

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false

jobs:
  release:
    runs-on: linux-amd64-cpu4
    permissions:
      contents: write
    outputs:
      publish-rc: ${{ steps.rc.outputs.should-publish }}
      reused-tag: ${{ steps.rc.outputs.reused-existing-tag }}
      version: ${{ steps.rc.outputs.version }}
      tag: ${{ steps.rc.outputs.tag }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
        with:
          node-version: "22"

      - name: Preview release candidate
        id: preview
        if: startsWith(github.ref, 'refs/heads/release/')
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/semantic-release@3dda6f975377bc945fbc12c61fb0aedc4ac4f3ca
        with:
          semantic-version: 25.0.9
          extra-plugins: conventional-changelog-conventionalcommits@9.3.1
          dry-run: "true"

      - name: Validate release branch target
        if: startsWith(github.ref, 'refs/heads/release/') && steps.preview.outputs.new-release-published == 'true'
        env:
          PREVIEW_VERSION: ${{ steps.preview.outputs.new-release-version }}
        shell: bash
        run: |
          set -euo pipefail
          target_version="${GITHUB_REF_NAME#release/}"
          [[ "$target_version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
          rc_prefix="${target_version}-rc."
          rc_number="${PREVIEW_VERSION#"$rc_prefix"}"
          [[ "$PREVIEW_VERSION" == "$rc_prefix"* ]]
          [[ "$rc_number" =~ ^[1-9][0-9]*$ ]]

      - name: Create release
        id: semantic
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/semantic-release@3dda6f975377bc945fbc12c61fb0aedc4ac4f3ca
        with:
          semantic-version: 25.0.9
          extra-plugins: conventional-changelog-conventionalcommits@9.3.1

      - name: Resolve RC publishing
        id: rc
        if: startsWith(github.ref, 'refs/heads/release/')
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/resolve-release-candidate@3dda6f975377bc945fbc12c61fb0aedc4ac4f3ca
        with:
          new-release-published: ${{ steps.semantic.outputs.new-release-published }}
          new-release-version: ${{ steps.semantic.outputs.new-release-version }}
          new-release-git-tag: ${{ steps.semantic.outputs.new-release-git-tag }}

      - name: Validate resolved RC target including reruns
        if: steps.rc.outputs.should-publish == 'true'
        env:
          RC_VERSION: ${{ steps.rc.outputs.version }}
        shell: bash
        run: |
          set -euo pipefail
          target_version="${GITHUB_REF_NAME#release/}"
          [[ "$target_version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
          rc_prefix="${target_version}-rc."
          rc_number="${RC_VERSION#"$rc_prefix"}"
          [[ "$RC_VERSION" == "$rc_prefix"* ]]
          [[ "$rc_number" =~ ^[1-9][0-9]*$ ]]
```

The dry run checks the version before any tag is created. Feed the resolver
the publishing step's outputs, not the preview outputs. The resolver selects
an RC but does not create tags, build artifacts or enforce a branch target.
The final guard also checks a reused tag, which the preview may not cover.
Publishing jobs consume this one release decision instead of recalculating it.

## Image Job

Use `docker-build` with the resolved version. A matrix can publish multiple
images without encoding component-specific paths in the shared action. The
component repository must provide `scripts/verify-rc-image.sh`: return `0` only
when both required platforms exist and every
`org.opencontainers.image.revision` label matches the expected commit, return
`3` when the tag does not exist, and fail for every other condition.
Install any verifier dependencies on the runner first. The Exchange verifier
requires Docker with Buildx and `jq`. Adapt `ORG/TEAM/component`, the
Dockerfile/context and required platforms to your repository.

```yaml
  publish-images:
    needs: release
    if: startsWith(github.ref, 'refs/heads/release/') && needs.release.outputs.publish-rc == 'true'
    runs-on: linux-amd64-cpu4
    environment: components-dev
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          ref: ${{ needs.release.outputs.tag }}
          persist-credentials: false

      - name: Record checked-out source revision
        id: source
        run: echo "revision=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3

      - name: Log in to the registry
        uses: docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9 # v3
        with:
          registry: nvcr.io
          username: $oauthtoken
          password: ${{ secrets.NGC_DSX_COMPONENTS_PUSH_KEY }}

      - name: Check existing image
        id: existing
        env:
          EXPECTED_REVISION: ${{ steps.source.outputs.revision }}
          IMAGE_REF: nvcr.io/ORG/TEAM/component:${{ needs.release.outputs.version }}
        run: |
          set +e
          scripts/verify-rc-image.sh "$IMAGE_REF" "$EXPECTED_REVISION"
          status=$?
          set -e
          case "$status" in
            0) echo "exists=true" >> "$GITHUB_OUTPUT" ;;
            3) echo "exists=false" >> "$GITHUB_OUTPUT" ;;
            *) exit "$status" ;;
          esac

      - name: Build and publish image
        if: steps.existing.outputs.exists != 'true'
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/docker-build@3dda6f975377bc945fbc12c61fb0aedc4ac4f3ca
        with:
          image: nvcr.io/ORG/TEAM/component
          tags: ${{ needs.release.outputs.version }}
          context: .
          dockerfile: Dockerfile
          platforms: linux/amd64,linux/arm64
          registry: nvcr.io
          username: $oauthtoken
          password: ${{ secrets.NGC_DSX_COMPONENTS_PUSH_KEY }}
          push: "true"
          labels: |
            org.opencontainers.image.revision=${{ steps.source.outputs.revision }}
            org.opencontainers.image.version=${{ needs.release.outputs.version }}

      - name: Verify published image
        env:
          EXPECTED_REVISION: ${{ steps.source.outputs.revision }}
          IMAGE_REF: nvcr.io/ORG/TEAM/component:${{ needs.release.outputs.version }}
        run: scripts/verify-rc-image.sh "$IMAGE_REF" "$EXPECTED_REVISION"
```

## Helm Job

Use `helm-package-push` with the same version. When a chart contains a local
chart dependency, update both the dependency constraint and the local chart's
version in the job workspace before packaging.
Install Helm and Mike Farah `yq` v4 before this block's metadata/dependency
steps. Exchange uses its checked-in `setup-mise` action; use your own pinned
tool setup rather than assuming that action exists in your repository.
Replace `deploy/component`, `component` and `ORG/TEAM` below.

```yaml
  publish-chart:
    needs: release
    if: startsWith(github.ref, 'refs/heads/release/') && needs.release.outputs.publish-rc == 'true'
    runs-on: linux-amd64-cpu4
    environment: components-dev
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          ref: ${{ needs.release.outputs.tag }}
          persist-credentials: false

      - name: Record checked-out source revision
        id: source
        run: echo "revision=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"

      - name: Stamp source revision
        env:
          SOURCE_REVISION: ${{ steps.source.outputs.revision }}
        run: |
          yq -i \
            '.annotations = (.annotations // {}) | .annotations."dsx.nvidia.com/source-revision" = strenv(SOURCE_REVISION)' \
            deploy/component/Chart.yaml

      - name: Publish chart
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-package-push@3dda6f975377bc945fbc12c61fb0aedc4ac4f3ca
        with:
          chart-path: deploy/component
          chart-version: ${{ needs.release.outputs.version }}
          app-version: ${{ needs.release.outputs.version }}
          ngc-key: ${{ secrets.NGC_DSX_COMPONENTS_PUSH_KEY }}
          ngc-path: ORG/TEAM
          ngc-duplicate: skip

      - name: Verify published chart
        env:
          EXPECTED_REVISION: ${{ steps.source.outputs.revision }}
          RELEASE_VERSION: ${{ needs.release.outputs.version }}
        shell: bash
        run: |
          set -euo pipefail
          verify_dir="$(mktemp -d "$RUNNER_TEMP/rc-chart.XXXXXX")"
          trap 'rm -rf "$verify_dir"' EXIT
          helm repo update helm-repo-ngc
          helm pull helm-repo-ngc/component \
            --version "$RELEASE_VERSION" --destination "$verify_dir"
          helm show chart "$verify_dir/component-$RELEASE_VERSION.tgz" \
            | yq -e '.version == strenv(RELEASE_VERSION) and
                .appVersion == strenv(RELEASE_VERSION) and
                .annotations."dsx.nvidia.com/source-revision" == strenv(EXPECTED_REVISION)'
```

For registries with delayed visibility, add bounded retries when downloading
the just-published chart, as the Exchange workflow does. A version match alone
is not enough: verify the recorded source revision too.

## Verify Your First RC

After the onboarding PR and a release-worthy change are approved and merged
to the configured release branch:

1. Confirm the dry run calculated the intended `X.Y.Z-rc.N` and passed the
   branch-target guard. A mismatch must fail before creating a tag.
2. Confirm the Git tag and GitHub prerelease point to the expected commit.
3. Confirm all expected images and charts exist at the resolver's version,
   without the Git tag's leading `v`.
4. Check both required image platforms and source revision labels. Download
   each chart and check its `version`, `appVersion` and source revision
   annotation. Verify local chart dependencies use the matching version.
5. Rerun the **same workflow run/commit**. Confirm the existing RC tag is reused
   and matching artifacts are verified, not rebuilt or overwritten. Do not
   create a new commit just to retry a failed artifact upload.

These read-only commands inspect the historical Exchange example:

```sh
gh release view v2.9.3-rc.1 --repo dsx-ai-factory/dsx-exchange
gh run view 34188354559 --repo dsx-ai-factory/dsx-exchange
gh run view 34188354559 --attempt 2 --repo dsx-ai-factory/dsx-exchange
```

Use your own repository, tag and run ID for acceptance. A green tag-creation
job does not prove downstream publishing succeeded. Do not mark onboarding
complete until every expected artifact is downloadable and the rerun checks
pass. No cluster deployment is required just to verify publishing.

## Reruns

The shared workflow detects an existing RC before running semantic-release,
requires valid `rc` channel metadata, and verifies or recovers the GitHub
prerelease before returning `should-publish: true`. Missing or invalid channel
metadata requires maintainer repair, not a new or moved tag. A rerun must
still be the current remote branch head. The artifact guidance below applies
when product publishing jobs are enabled.

semantic-release creates the Git tag before downstream artifact jobs run. If a
later job fails, a workflow rerun normally reports that no new release was
created. `resolve-release-candidate` handles this by selecting one matching RC
tag already pointing at `HEAD`. Multiple matching RC tags fail closed because
the intended artifact version would be ambiguous.

Expose `reused-existing-tag` for logging and auditing, but do not use it as the
only rerun condition. A failed first run can leave only some artifacts behind,
so every artifact job must inspect its own immutable version independently.

Do not rebuild or overwrite an image that already has the RC tag. Stamp images
with `org.opencontainers.image.revision`; on rerun, verify that every required
platform has the expected revision and skip a matching image. Stamp Helm charts
with the same source revision. `ngc-duplicate: skip` is safe only when the
downloaded existing chart is verified against that revision. End every run by
checking that all expected images and charts exist at the one resolved version.

## Hand Off to the SBOM

Give the SBOM/QA owner the exact chart version, image references, source commit
and successful workflow URL. For the Exchange example, consumers pin the
published chart version `2.9.3-rc.1`, not the Git tag `v2.9.3-rc.1`, a branch
name or `latest`. Confirm the chart resolves to the intended images and pinned
local dependencies before accepting the SBOM update.

Update the component's version in the owning SBOM configuration using that
repository's current schema and normal review/validation flow. Publication
does not automatically update the SBOM or approve a deployment. The SBOM bundle
has its own version: an SBOM `1.0` release does not require every component to
use version `1.0`.

## Next Release Cycle

Agree the next target with the component owner, then update the explicit
release branch in the workflow trigger. Only the advanced composition path
also updates `.releaserc.json`; the shared workflow derives RC configuration
from the event and leaves product configuration unchanged. Recheck the
protected-branch/tag rules and environment branch policy. Keep only one active
branch using the `rc` prerelease identifier and validate the new version with
the dry run. Do not assume creating or renaming a branch alone enables it.

Stable promotion is a separate release decision. Follow the component's
approved stable-release process; do not strip `-rc.N`, retag images or merge
into `main` merely to make a final release appear. Retire the old RC branch
after stable promotion without deleting or moving its immutable release tags.

## Troubleshooting

| Symptom | Check / next step |
| --- | --- |
| No workflow after merge | The workflow must exist on the pushed branch, and its `on.push.branches` must match. PR validation alone does not run the release job. |
| Workflow runs, but no new RC | Check the explicit branch target, stable history and release-worthy commits since the last release. For advanced composition, also check `.releaserc.json` and action input overrides. A docs/chore-only change may legitimately publish nothing. |
| Shared checks fail before publishing | Inspect the central checks at the pinned shared workflow revision. Do not bypass them or substitute product code for the shared checkout. |
| Source check rejects the run | Verify branch protection, exact `release/X.Y.Z` naming and that the run still targets the current remote branch head. |
| Version-target check fails | Reconcile the intended branch target with release history and commit semantics. Do not bypass the check or move an existing tag. |
| Tag/release creation denied | Check the release identity's permissions and tag rules. An NGC credential cannot grant GitHub tag permissions. |
| Artifact job cannot use the environment/NGC | Check protected branch admission, environment approvals, scoped registry permissions and runner connectivity. Never print credentials. |
| Tag exists but some artifacts are missing | Repair the failed publishing prerequisite and rerun the same workflow run. Inspect each artifact independently; do not skip all uploads merely because the tag exists. |
| Existing artifact has a different revision or a required platform is missing | Fail closed and investigate the publishing history. Do not overwrite the RC tag; have the release owner select the corrected release path. |
| Multiple RC tags point to the same commit | The resolver deliberately rejects ambiguity. Have the release owner reconcile the release history instead of guessing which version to publish. |

The same onboarding steps apply to another GitHub-hosted component once its
repository, runner and publishing access are ready. GitLab-native release
templates are outside this guide's scope.
