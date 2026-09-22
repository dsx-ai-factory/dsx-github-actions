# Enable RC Releases with DSX GitHub Actions

Use this guide to publish release-candidate (RC) source tags, GitHub prereleases, images, and Helm charts.
DSX Exchange is the worked example, not a requirement to use its component names or release version.

Use `release-candidate-publish.yml` for manifest-driven source and NGC publishing.
Your repository owns branch protection, approved source, required product tests, workflow triggers, artifact declarations, and credential policy.
Shared jobs own preflight, source publishing, image and chart publishing, and immutable-artifact verification.
No product RC scripts, publishing jobs, Node files, or `.releaserc` changes are required.

The existing `release-candidate.yml` remains a separate, unchanged source-only entry point.
Neither entry point changes stable publishing on `main` or migrates historical release branches.

This guide covers the following topics.

- [Recommended Minimal Workflow](#recommended-minimal-workflow)
- [Manifest Contract](#manifest-contract)
- [Prerequisites and Scope](#prerequisites-and-scope)
- [Optional Deploy Key Authentication](#optional-deploy-key-authentication)
- [Source-Only Workflow](#source-only-workflow)
- [What You Will Get](#what-you-will-get)
- [Advanced Composition](#advanced-composition)
- [Verify Your First RC](#verify-your-first-rc)
- [Reruns](#reruns)
- [Hand Off to the SBOM](#hand-off-to-the-sbom)
- [Next Release Cycle](#next-release-cycle)
- [Troubleshooting](#troubleshooting)

## Recommended Minimal Workflow

Add `.github/workflows/release-rc.yml` to the component repository.
This Exchange example uses one caller job with inline manifests.

```yaml
name: Release Candidate

on:
  push:
    branches: ["release/*", "pull-request/[0-9]+"]

permissions:
  contents: read

jobs:
  release:
    if: github.repository == 'dsx-ai-factory/dsx-exchange'
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/release-candidate-publish.yml@REPLACE_WITH_REVIEWED_COMMIT_SHA
    permissions:
      contents: write
    with:
      runner: linux-amd64-cpu4
      environment: components-dev
      ngc-path: 0837451325059433/components-dev
      images: >-
        [{"name":"auth-callout","context":"auth-callout","dockerfile":"auth-callout/build/package/Dockerfile"},
         {"name":"dsx-agentgateway-bridge","context":"dsx-agentgateway-bridge","dockerfile":"dsx-agentgateway-bridge/Dockerfile"}]
      charts: >-
        [{"name":"auth-callout","path":"auth-callout/deploy"},
         {"name":"nats-event-bus","path":"deploy/nats-event-bus",
          "localDependencies":[{"name":"auth-callout","path":"auth-callout/deploy"}]},
         {"name":"dsx-agent-gateway","path":"deploy/dsx-agent-gateway"}]
    secrets:
      release-deploy-key: ${{ secrets.RELEASE_DEPLOY_KEY }}
```

Replace `REPLACE_WITH_REVIEWED_COMMIT_SHA` with the full reviewed commit SHA containing the wrapper and its helpers before use.
The placeholder is not a usable ref.
Replace the repository guard, runner, environment, NGC path, and manifests for another product.
Do not restrict the caller job to release pushes only, because that would skip manifest validation on copy-pr-bot pushes.

The wrapper also supports validation on `pull_request` events when the caller enables that trigger.
Exchange uses copy-pr-bot pushes to `pull-request/[0-9]+` instead.
Validation does not publish tags or artifacts and does not use the publishing environment.
Only a push to a protected, current `release/X.Y.Z` head can pass the source publisher's gates.

### Inputs

The wrapper accepts these inputs. All are strings except the two booleans noted below.

| Input | Default | Meaning |
| --- | --- | --- |
| `runner` | Required, no default | Approved Linux runner for preflight and publishing. |
| `default-branch` | Caller repository's default branch | Separate stable-history branch used for version calculation. |
| `images` | `[]` | JSON array of image declarations. |
| `charts` | `[]` | JSON array of chart declarations. |
| `submodules` | `false` | Boolean. Recursively check out product submodules in preflight and artifact jobs. |
| `validate-artifacts` | `false` | Boolean. Build images and lint/package charts on nonrelease events, without publishing. |
| `environment` | Required | GitHub environment used by artifact jobs. |
| `ngc-path` | Required | NGC organization/team path, without `nvcr.io/`. |

Each artifact array defaults to empty, but the wrapper requires at least one image or chart.
An empty array disables that artifact type.
Use the [source-only workflow](#source-only-workflow) when neither artifact type is needed.
Image platforms are fixed to `linux/amd64,linux/arm64`; the wrapper has no platform input.

With `validate-artifacts: true`, nonrelease events run the declared image and chart matrices after shared checks and preflight pass.
Images use the same Docker action, contexts, targets, arguments, and platforms as publishing, with `push: false` and remote caching disabled.
Charts use the same preparation and packaging actions, with NGC push disabled.
Both use the synthetic version `0.0.0-rc.1` and the event commit SHA.
These jobs have no publishing environment, registry login, or publishing secrets, and do not create Git tags.
Dependencies and base images must be readable without publishing credentials.
Enable this only on approved runners and trusted PR or copy-pr-bot events, since Dockerfiles execute product code.
Successful validation proves builds and packages work on that runner, not NGC access, artifact publication, or product runtime behavior.

## Manifest Contract

Declare product paths relative to the repository root, including Dockerfile and local dependency paths.
Preflight parses the JSON and checks the declared paths before calling the source publisher.
Invalid manifests therefore fail before any new RC tag is created.
The inputs contain JSON directly; they are not paths to manifest files.

The image manifest has this shape.

```json
[
  {
    "name": "auth-callout",
    "context": "auth-callout",
    "dockerfile": "auth-callout/build/package/Dockerfile"
  }
]
```

The chart manifest has this shape.

```json
[
  {
    "name": "nats-event-bus",
    "path": "deploy/nats-event-bus",
    "localDependencies": [
      {"name": "auth-callout", "path": "auth-callout/deploy"}
    ]
  }
]
```

The declarations must satisfy these conditions.

- Each manifest contains at most 32 entries, with valid, unique names within that artifact type.
- Image objects contain `name`, `context`, and `dockerfile`, with optional `target` and `buildArgs`.
- Chart objects contain `name`, `path`, and optional `localDependencies`, `versionValuePaths`, and `lintValues`.
- Context and chart paths resolve to directories; Dockerfile paths resolve to files.
- Manifest paths cannot be absolute, contain `..` components, or resolve outside the source checkout.
- Each chart name matches its `Chart.yaml`.
- Each declared local dependency names exactly one parent dependency whose `file://` target resolves to the declared path.

Omitting `localDependencies` is equivalent to an empty array.
List each local dependency that must use the RC version.
The shared helper updates that local chart's `version` and `appVersion`, plus its parent's dependency version constraint, before packaging.
It does not infer Exchange-specific dependencies or change external dependency version declarations.
These edits affect only the publishing workspace, not committed product files.

### Docker Targets and Arguments

Use a Docker stage name and a mapping of literal build arguments when the Dockerfile requires them.

```json
{"name":"widget","context":".","dockerfile":"Dockerfile","target":"widget-runtime","buildArgs":{"BINARY":"widget"}}
```

Omitting `target` uses the Dockerfile's final stage. Omitting `buildArgs` passes no additional arguments.
Argument names must be identifiers and values must be single-line strings without surrounding whitespace.
The mapping supports at most 32 arguments, with values up to 4096 characters each.
Arguments are passed as data, without shell evaluation or release-version substitution.
Do not put credentials in build arguments or manifests. Build arguments can appear in build logs and image provenance.
The Dockerfile must produce its own generated sources and build prerequisites; the wrapper does not run product Make targets or arbitrary setup scripts.

### Chart Values

Declare existing string values in the chart's `values.yaml` that must track the RC version.
Paths are arrays of literal mapping keys, not yq expressions.

```json
{"name":"widget","path":"charts/widget","versionValuePaths":[["widget","widget-server","image","tag"]],"lintValues":{"endpoint":"https://example.invalid"}}
```

`versionValuePaths` defaults to `[]`. Each of at most 32 paths contains one to 16 keys using letters, digits, underscores, or hyphens.
Preflight rejects missing paths, non-string leaves, duplicate paths, and malformed declarations before source tagging.
Preparation stamps only the declared values, in addition to the existing chart metadata updates.

`lintValues` is an optional JSON mapping, limited to 65536 characters, for non-secret validation configuration.
Helm receives it through a temporary values file only during lint.
It does not change packaged chart defaults, and it is not a deployment configuration.
Product tests remain responsible for checking that packaged charts select the intended images.

## Prerequisites and Scope

Configure these prerequisites before enabling RC publication.

- Use GitHub.com. Shared code and tests are checked out using the called workflow's `job.workflow_repository` and `job.workflow_sha`. GHES is outside this implementation's scope.
- Protect each active `release/X.Y.Z` branch with PR review, Code Owner approval, and required product tests. Shared contract checks do not run the product's test suite.
- Grant `contents: write` for source release publication. Authorize the workflow token or approved Deploy Key for tag and channel-note writes.
- Restrict the selected GitHub environment to approved protected release branches. Configure any required environment approvals.
- Store `NGC_DSX_COMPONENTS_PUSH_KEY` in that environment, scoped to the selected NGC destination.
- Set `runner` explicitly to an approved Linux runner with Git, Bash, `gh`, and `jq`. The source publisher requires non-cone sparse checkout support. Shared jobs install Node, Helm, and `yq` as needed.
- When images are declared, the runner must have Docker and a readable `/etc/buildkit/buildkitd.toml`, as required by the shared `docker-build` action. Preflight fails before source tag creation if either prerequisite is missing.
- Set `submodules: true` when build contexts depend on submodules. Checkout uses the pinned gitlinks recursively, not submodule branch tips. Submodules must be accessible using checkout read access; the release Deploy Key and NGC credentials are not passed to submodule checkout.
- Keep stable and RC publishing triggers disjoint. A release-worthy change and compatible stable history are required; a branch name alone does not force a release.

### NGC Credentials

`NGC_DSX_COMPONENTS_PUSH_KEY` is optional in the reusable workflow's secret declaration.
Artifact publishing still requires an available credential.
In the recommended setup, each artifact job resolves the secret from the GitHub environment named by `environment`.
The caller does not pass that environment secret through its `secrets` mapping.

A caller can explicitly pass a repository or organization secret with the same name when it does not use environment-scoped storage.
That optional mapping does not bypass environment policy.
The source publisher and validation jobs do not use the NGC credential.
Never commit a credential value or print it during troubleshooting.

### Optional Deploy Key Authentication

Some repositories, including Exchange, permit release-tag writes only through an approved Deploy Key.
`contents: write` on `GITHUB_TOKEN` does not override those tag rules.
Pass the existing repository-scoped, write-enabled key as shown in the caller's `release-deploy-key` mapping.

The wrapper forwards this optional secret only to `release-candidate.yml`.
The source checkout uses SSH and semantic-release receives `git@github.com:OWNER/REPO.git` for Git operations.
`GITHUB_TOKEN` remains the credential for GitHub Release API calls.
Only the source publisher uses the Deploy Key.
The shared-code checkout, central checks, and manifest validation do not use publishing credentials.

Register the public key as a write-enabled deploy key for the product repository.
Store the private key in the caller's repository or scoped organization secret.
Authorize that Deploy Key in the applicable tag rules.
Do not relax protections to make the workflow pass.

Before publishing, a one-minute SSH `git push --dry-run` checks push authentication without changing remote refs.
An unusable key stops the workflow before semantic-release can fall back to HTTPS.
This check does not prove that every tag-specific rule will accept the eventual release.
`GH_TOKEN` is set to the workflow token, not inherited from the runner.

Omit `release-deploy-key` when the repository permits the workflow token to push release refs.
The source publisher then retains its existing HTTPS behavior.
Verify live key validity and ruleset authorization during [first-RC acceptance](#verify-your-first-rc), not through local tests alone.

### Shared Release Contract

The wrapper runs product manifest preflight before calling [the source publisher](../.github/workflows/release-candidate.yml).
On non-release events, the wrapper also runs [central RC checks](../.github/workflows/release-candidate-checks.yml).
On release pushes, the source publisher runs those shared tests before publishing.
Shared helpers come from the called workflow revision, not from product source or a moving shared branch.

The source publisher uses a full-history, non-cone sparse `release-source` checkout containing only `/.github/`.
Product root release configuration and package files are absent.
Shared code generates RC-only configuration in that disposable checkout.
The product's stable-release configuration remains unchanged.

Source publication requires a push to the protected, current `release/X.Y.Z` head.
The version must be `X.Y.Z-rc.N`, with a positive sequence and no leading zeros.
The guard checks both the preview and semantic-release's actual `verifyRelease` phase before tag creation.

Reruns validate the existing tag's source commit and `rc` channel metadata before reusing it.
A missing GitHub prerelease can be recovered for a verified tag.
An existing draft or non-prerelease is rejected, not overwritten.

Shared artifact jobs run only after the source RC and GitHub prerelease are verified.
They check out the resolved tag, confirm its source revision, and use the same version without `v`.
They run with `contents: read` in the selected environment.

### Wrapper Outputs

The wrapper exposes these source-release outputs.

| Output | Meaning |
| --- | --- |
| `version` | RC version without `v`, for example `1.2.3-rc.1`. |
| `tag` | Immutable source Git tag, for example `v1.2.3-rc.1`. |
| `release-url` | Verified GitHub prerelease URL. |

These outputs identify the source release, not successful publication of every artifact.
Check all artifact jobs and downloadable results before accepting the RC.

## Source-Only Workflow

Use the existing entry point when you need only source tags and GitHub prereleases.

```yaml
name: Release Candidate

on:
  push:
    branches: [release/1.2.3]

permissions:
  contents: write

jobs:
  release:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/release-candidate.yml@REPLACE_WITH_REVIEWED_COMMIT_SHA
```

Replace the branch and full commit SHA for your repository.
This workflow requires no NGC manifest, environment, or registry credential.
Its inputs remain `runner` and `default-branch`; its optional secret remains `release-deploy-key`.
The source-only `runner` remains optional and defaults to `ubuntu-latest`.
The Deploy Key and `GITHUB_TOKEN` roles are identical to those described above.

The source-only outputs remain `should-publish`, `version`, `tag`, `reused-existing-tag`, and `release-url`.
`should-publish` becomes `true` only after the source RC and GitHub prerelease are verified.
The workflow does not publish NGC artifacts, stable releases, or moving major tags.
Refer to the [source-only workflow reference](../.github/workflows/README.md#release-candidate-release-candidateyml) for the unchanged interface.

## What You Will Get

For Exchange, the wrapper publishes these artifacts to NGC `0837451325059433/components-dev` at one `X.Y.Z-rc.N` version.

| Artifact | Names |
| --- | --- |
| Helm charts | `auth-callout`, `nats-event-bus`, `dsx-agent-gateway` |
| Images, both `linux/amd64` and `linux/arm64` | `auth-callout`, `dsx-agentgateway-bridge` |

The event boundary is explicit.

| Event | Source behavior | Artifact behavior |
| --- | --- | --- |
| Copy-pr-bot push or configured `pull_request` event | No release tag; validate shared contract and product manifests. | No publishing or publishing-environment access. |
| Release-worthy change pushed to a protected, current `release/X.Y.Z` head | Create and verify `vX.Y.Z-rc.N`. | Publish and verify matching immutable artifacts. |
| Release-worthy change merged to Exchange `main` | Existing stable `release.yml` behavior. | The RC wrapper does not run stable publishing. |
| Rerun while the commit remains the release branch head | Verify and reuse its existing RC tag. | Verify matching artifacts and publish missing artifacts. |

Not every merge creates a tag.
semantic-release uses stable history and Conventional Commits, not the highest-numbered release branch.
The branch-target guard rejects a calculated version outside the intended release line.

Exchange's historical `release/2.9.3` implementation is recorded in [PR #109](https://github.com/dsx-ai-factory/dsx-exchange/pull/109), [run 34188354559](https://github.com/dsx-ai-factory/dsx-exchange/actions/runs/34188354559), and [v2.9.3-rc.1](https://github.com/dsx-ai-factory/dsx-exchange/releases/tag/v2.9.3-rc.1).
Those records describe the earlier product-owned publisher, not execution of this wrapper.
They do not establish that a real RC or rerun has passed with the new shared workflow.

## Advanced Composition

Use the manifest-driven wrapper for new Exchange release branches.
A repository with a custom release policy can still consume the source-only workflow or individual actions.
Such a repository owns any controls and artifact jobs outside the shared wrapper.
Custom composition is not required for Exchange.

The earlier Exchange implementation remains available as historical reference.

| File | Historical responsibility |
| --- | --- |
| [.releaserc.json](https://github.com/dsx-ai-factory/dsx-exchange/blob/71ec461908b90bc11dc0ce7c95b9cb40da7bf7ce/.releaserc.json) | Product-owned RC configuration for `release/2.9.3`. |
| [release.yml](https://github.com/dsx-ai-factory/dsx-exchange/blob/71ec461908b90bc11dc0ce7c95b9cb40da7bf7ce/.github/workflows/release.yml) | Product-owned source, image, and chart jobs. |
| [verify-rc-image.sh](https://github.com/dsx-ai-factory/dsx-exchange/blob/71ec461908b90bc11dc0ce7c95b9cb40da7bf7ce/scripts/verify-rc-image.sh) | Historical product-owned image verification. |

Do not copy those scripts or RC configuration into a new wrapper consumer.
Existing release branches retain their checked-in workflows.
Before migrating an older branch, disable its previous RC publisher and review the branch and environment policies.
Never enable two publishers for the same release.

## Image Job

The wrapper creates shared matrix jobs from `images`.
Each job checks the immutable NGC image before building it.
A matching image is reused only when both supported platforms exist and their `org.opencontainers.image.revision` labels match the source commit.
A missing image is built for `linux/amd64,linux/arm64`, published at the RC version, and verified again.
An unexpected registry failure or revision mismatch fails the job instead of overwriting the tag.

## Helm Job

The wrapper creates shared matrix jobs from `charts`.
Each job checks a freshly fetched, authenticated NGC index before chart preparation or packaging.
If the version exists, the job downloads the chart and verifies its name, `version`, `appVersion`, and `dsx.nvidia.com/source-revision` annotation.
A matching chart is reused without dependency resolution, metadata preparation, or packaging.
Authentication, index, download, and metadata errors fail the job; they do not mean that the chart is missing.

Only a version confirmed missing from that index proceeds to preparation and packaging.
The job validates chart metadata and declared local dependencies, then stamps the RC version and source revision.
It aligns the explicitly listed local dependencies before running Helm dependency resolution and packaging.
No Exchange-specific dependency name is encoded in shared code.

Publication of a missing chart uses duplicate-skip behavior, followed by verification of the downloadable chart.
The downloaded chart must have the expected name, `version`, `appVersion`, and `dsx.nvidia.com/source-revision` annotation.
Duplicate-skip alone is not acceptance.
The shared verifier uses bounded retries for chart visibility in NGC and fails on a metadata mismatch.

## Verify Your First RC

Before merging, confirm copy-pr-bot validation passed shared tests and product manifest preflight without publishing.
For build and packaging evidence, enable `validate-artifacts` and require its image and chart jobs on the intended Linux runner.
Local validation is not evidence of a real RC publication or rerun.

After an approved release-worthy change reaches the protected release branch, complete these checks.

1. Confirm manifest preflight and the source publisher's shared tests passed before source publication.
2. Confirm the dry run calculated `X.Y.Z-rc.N` for the branch target. A mismatch must fail before creating a tag.
3. Confirm the Git tag points to the expected commit and its GitHub release is a non-draft prerelease.
4. Confirm all declared images and charts are downloadable at the resolved version without the Git tag's leading `v`.
5. Check both image platforms and their source revision labels. Check each chart's `version`, `appVersion`, and source revision annotation. Verify declared local dependencies use the matching version.
6. Rerun the same workflow run while its commit remains the release branch head. Confirm that the source tag and matching artifacts are reused without overwriting them.

These read-only commands inspect the historical Exchange example:

```sh
gh release view v2.9.3-rc.1 --repo dsx-ai-factory/dsx-exchange
gh run view 34188354559 --repo dsx-ai-factory/dsx-exchange
gh run view 34188354559 --attempt 2 --repo dsx-ai-factory/dsx-exchange
```

Use your own repository, tag, and run ID for acceptance.
A green source job does not prove downstream publishing succeeded.
Do not mark onboarding complete until every expected artifact is downloadable and the rerun checks pass.
No cluster deployment is required to verify publishing.

## Reruns

The source publisher detects an existing RC before running semantic-release.
It requires valid `rc` channel metadata and verifies or recovers the GitHub prerelease before returning `should-publish: true` to the wrapper.
Missing or invalid channel metadata requires maintainer repair, not a new or moved tag.
The rerun commit must still be the current remote branch head.

semantic-release creates the Git tag before downstream artifact jobs run. If a
later job fails, a workflow rerun normally reports that no new release was
created. `resolve-release-candidate` handles this by selecting one matching RC
tag already pointing at `HEAD`. Multiple matching RC tags fail closed because
the intended artifact version would be ambiguous.

A failed first run can leave only some artifacts behind.
Each shared artifact job handles its immutable version independently; source-tag reuse does not skip all artifact jobs.
The wrapper reuses verified images and checks existing charts before preparation or packaging.
Matching charts skip dependency resolution and packaging; only confirmed missing chart versions are built and then verified after publication.
An existing artifact with the wrong revision fails verification and is not overwritten.
Check that all declared images and charts exist at the resolved version before accepting the rerun.

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

Agree the next target with the component owner.
Create its protected `release/X.Y.Z` branch from approved `main` source containing the wrapper caller.
Confirm that the branch matches the caller trigger and the publishing environment's branch policy.
If your caller names one explicit branch instead of `release/*`, update that trigger through review.
Recheck branch protection, tag rules, and release-worthy history before publishing.

The shared workflow derives RC configuration from the event; do not update the product's `.releaserc.json` for this path.
Exchange's `.releaserc.json` and `release.yml` remain for stable releases from `main`.
Historical release branches keep their checked-in workflows until an explicit migration disables the previous RC publisher.

Stable promotion is a separate release decision. Follow the component's
approved stable-release process; do not strip `-rc.N`, retag images or merge
into `main` merely to make a final release appear. Retire the old RC branch
after stable promotion without deleting or moving its immutable release tags.

## Troubleshooting

| Symptom | Check / next step |
| --- | --- |
| No workflow after merge | The workflow must exist on the pushed branch, and its `on.push.branches` must match. PR validation alone does not run the release job. |
| Manifest preflight fails | Check inline JSON, accepted fields, unique names, existing repository-relative paths, chart names, and declared local dependencies. Declare at least one image or chart. No RC tag is created by a failed preflight. |
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
