# Promote a release candidate

`promote-rc.yml` publishes a stable release from one verified RC. It copies images
by digest and derives stable Helm archives from the published RC archives.
It does not build images or resolve chart dependencies.

## Product configuration

Add this caller to the product's protected default branch. Pin each shared
workflow to a reviewed full commit SHA. A compatible promoter update can use
a newer revision without rebuilding the selected RC.

```yaml
name: Promote RC
on:
  workflow_dispatch:
    inputs:
      rc_version:
        description: Exact QA-approved RC, without v
        required: true
        type: string
permissions:
  contents: read
jobs:
  promote:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/promote-rc.yml@<reviewed-sha>
    permissions:
      contents: read
      actions: read
      deployments: read
    with:
      rc_version: ${{ inputs.rc_version }}
    secrets:
      RC_PROMOTION_APP_PRIVATE_KEY: ${{ secrets.RC_PROMOTION_APP_PRIVATE_KEY }}
      RC_PROMOTION_SOURCE_NGC_KEY: ${{ secrets.RC_PROMOTION_SOURCE_NGC_KEY }}
      RC_PROMOTION_DESTINATION_NGC_KEY: ${{ secrets.RC_PROMOTION_DESTINATION_NGC_KEY }}
```

Keep these secrets only in `stable-release`. GitHub requires the caller to
[forward environment secrets explicitly](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-inputs-and-secrets-in-a-reusable-workflow),
even when they exist only in that environment. The shared job receives them after
approval and checks for missing values before installing tools.
Do not use `secrets: inherit` or add caller concurrency. The shared workflow owns
the publishing environment and stable-release lock.

Add `.github/rc-promotion.json` on the protected default branch. Keep `enabled`
false until the rollout review.
Declare the complete inventory, including every chart whose stable version is
required. Registry paths and artifact declarations are reviewed configuration,
not dispatch inputs.

```json
{
  "schema": 1,
  "enabled": false,
  "source_workflow": ".github/workflows/release-rc.yml",
  "source_publisher_sha": "<reviewed-RC-publisher-commit-SHA>",
  "source_ngc_path": "example/components-dev",
  "destination_ngc_path": "example/components-dev",
  "images": ["service"],
  "charts": [
    {"name": "service", "version_value_paths": [["image", "tag"]]}
  ],
  "competing_workflows": [".github/workflows/release.yml"]
}
```

Use the same chart version-value paths as the RC publisher's `versionValuePaths`.
An empty array means no values-file tag rewrites. References derived from
`Chart.AppVersion` remain part of the product QA contract described below.
Each declared path must end in `tag`, with a sibling string `repository` and an
optional string `registry`. The resulting `registry/repository:stable` (or
`repository:stable` when registry is empty) must exactly match a verified image
destination, even for disabled templates or values embedded in strings.
Missing repositories and other version-value mapping shapes are unsupported and
rejected before any publishing.

Set `source_publisher_sha` to the approved full commit SHA used to publish the
selected RC. Keep this value when updating only the promoter. The selected Actions attempt must reference
`dsx-ai-factory/dsx-github-actions/.github/workflows/release-candidate-publish.yml`
at exactly that immutable SHA. Missing provenance, mutable refs, different
repositories, and different publisher revisions are rejected. The helper fetches
the exact attempt response rather than trusting run-list provenance fields.
The shared repository does not embed its own commit SHA.
GitHub's caller workflow `path` may be bare or have an `@ref` suffix. Selection,
exact-attempt verification, and retries accept only the configured caller path
with no suffix, the verified `release/X.Y.Z` branch (short or `refs/heads/`
form), or the exact verified commit SHA. Other refs are rejected; this does not
relax the separate immutable shared-publisher check.

When charts are included, source and destination NGC paths **must be identical**.
Cross-namespace chart promotion is rejected before registry access, including
charts without declared version-value paths. This implementation does not
rewrite image repositories or relocate vendored dependency images. The example
therefore publishes stable tags alongside RC tags in the same namespace. Only
image-only policies may use different source and destination paths.

On `release/X.Y.Z`, update the existing RC caller to the approved shared
publisher SHA and opt in to evidence capture:

```yaml
with:
  promotion-manifest: true
```

The release branch needs the producer update, not the promotion dispatcher or
policy. Capture reads the RC caller's artifact declarations without reading
`.github/rc-promotion.json`. Dispatch promotion from the protected default
branch, selecting the QA-approved RC version as input. Merging the default-branch
caller does not update a release branch or retrofit an older RC's evidence.

The RC publisher records all image digests, chart archive digests, source commit,
workflow path, run ID, and run attempt after every artifact job succeeds. It
uploads `rc-promotion-manifest-<version>-<attempt>` with 90-day retention.
Promotion checks the artifact's GitHub digest and the successful source run.
Older or expired RCs without this evidence are rejected. A GitHub prerelease
alone is insufficient. Do not fabricate a manifest for a historical RC.

## Authorization and cutover

Repository write access permits requesting a manual run. Publishing additionally
requires approval through the fixed `stable-release` environment.

Before activation, repository administrators must configure:

- A protected default branch. Runs from other refs, unprotected refs, and stale
  default-branch commits are rejected.
- `stable-release` with required **team** reviewers, self-review prevention, and
  administrator bypass disabled. Its sole deployment branch policy must be the
  exact default branch name. Native required reviewers authorize any one listed
  team reviewer, not one approval from each team. A both-teams policy is not
  supported by this single gate and must not be activated.
- Environment-only secrets `RC_PROMOTION_SOURCE_NGC_KEY` and
  `RC_PROMOTION_DESTINATION_NGC_KEY`. Use source read and destination publish
  permissions. Do not create repository or organization secrets with these names.
  Reads select credentials by source or destination role, even when the NGC paths
  are identical. The destination key must also read destination images and charts
  for conflict detection and post-publication verification.
- A GitHub App installed only on the product repository with Contents and
  Workflows write permissions. In `stable-release`, set variable
  `RC_PROMOTION_APP_ID` and secret `RC_PROMOTION_APP_PRIVATE_KEY`; do not place
  that private key in repository or organization secrets. The pinned official
  token action requests only those permissions for the current repository and
  revokes its short-lived installation token when the job ends.
- Existing destination chart repositories, immutable stable-version policy, and
  registry access from the runner. Promotion does not create chart repositories
  or modify registry permissions.
- Read access for the normal workflow token to run evidence and environment
  protection metadata. A denied metadata read stops promotion. Do not introduce
  an administrator token to bypass this check.

GitHub requires Workflows write permission when a release's target commit changes
workflow files relative to the default branch; `GITHUB_TOKEN` cannot receive that
permission ([GitHub release API](https://docs.github.com/en/rest/releases/releases#create-a-release)).
The release token is used only for release/ref writes, release-asset uploads, and
destination draft-release/intent reads needed for retries. GitHub limits draft
release visibility to push-capable identities. Source evidence, protection checks,
and checkout keep the normal read-only workflow token. The App token is not passed
to registry tools or checked-out product code; no RC code is executed by promotion.
Missing selected credentials stop before writes.

For a temporary PAT setup, set environment variable `RC_PROMOTION_AUTH_MODE` to
`pat` and environment secret `RC_PROMOTION_PAT`. Replace the App private-key
mapping in the caller with `RC_PROMOTION_PAT: ${{ secrets.RC_PROMOTION_PAT }}`.
Use an expiring, repository-scoped
PAT with Contents and Workflows write permissions. The approval gate stays the
same. This mode must be selected explicitly; failed App authentication never
falls back to a PAT. Only the selected credential is used for release writes.
Do not place the PAT in repository or organization secrets. To return to App
authentication, configure the App credentials and caller mapping, set the mode to
`app`, and remove the PAT secret. Revoke a dedicated temporary PAT when it is no
longer needed.
Unset mode defaults to `app`; any other mode is rejected.

One repository variable, `DSX_RELEASE_MODE`, coordinates the cutover. Unset or
any value other than `rc-promotion` preserves legacy publishing. Every job in an
existing stable publisher must use:

```yaml
if: vars.DSX_RELEASE_MODE != 'rc-promotion'
```

That workflow must also share the promotion lock:

```yaml
concurrency:
  group: dsx-stable-release-${{ github.repository }}
  cancel-in-progress: false
```

The helper checks these guards on every declared competing workflow and on
`.github/workflows/release.yml` when present. A manually disabled workflow also
satisfies the collision check. The helper never changes workflow state.

Merge and review the guarded callers before setting `DSX_RELEASE_MODE` to
`rc-promotion` and enabling the policy. Do not set that variable on the
environment, where it could disagree with the legacy publisher. The shared
workflow reads it before entering the environment and carries that decision into
the publishing job.

Keep activation off until the authorization policy is agreed and a supervised
canary is authorized. Local tests do not validate live GitHub approvals, NGC
permissions, publication, or downstream deployment.

## Artifact identity

Only canonical `X.Y.Z-rc.N` versions are accepted, with a positive RC number and
no leading zeros or build metadata. The stable version is `X.Y.Z`.

Images are copied from the verified digest with `skopeo --all --preserve-digests`.
The destination tag must resolve to that exact digest. Embedded RC version
strings and OCI image labels remain unchanged. A stable registry tag is a new
name for the QA-tested bytes, not a rebuilt binary with rewritten version text.

Stable chart archives have **different digests**. Only root `Chart.yaml`
version/appVersion, promotion provenance annotations, and declared RC-valued
paths in root `values.yaml` change. Templates, other values, lock files, and
vendored dependencies retain their original bytes. A vendored RC dependency
keeps its RC version and archive even when a separate stable chart is published
for it. No dependency update or image rebuild occurs.
After verifying the original source archive digest, identical duplicate file
paths with exactly matching content and mode are canonicalized to one entry.
Conflicting duplicate content or modes are rejected. This does not modify the
contents of nested dependency archives.
Vendored image references also retain their RC tags unless an explicitly
declared root value overrides them. A stable root chart is **not** a promise that
every runtime image uses a stable tag. Retain the referenced RC images and their
access permissions; this workflow performs no RC cleanup. Products requiring
all-stable dependency references or namespace relocation are unsupported and
must remain disabled until those transformations are implemented and reviewed.

The shared promoter validates source artifact identity, the planned inventory,
and every declared tag rewrite. It does **not** render charts or require them to
deploy with default values. Product QA owns deployment values, placeholder
registry overrides, `Chart.AppVersion`-derived image references, conditional
templates, embedded expressions, and custom resources. Product fixture/render
checks must establish that the intended deployment uses the approved artifacts
before activation. No runtime-wide image validation or deployment readiness is
claimed. Repositories, templates, dependency archives, and undeclared values are
preserved; the promoter never inserts deployment values or rewrites repositories
to make a chart pass validation.

The archive records the RC version, source commit, original chart digest, and RC
manifest digest. This is traceability, not a new security approval or signing
attestation. External OCI referrer signatures are not copied by this workflow.
Products requiring new signatures or changed vendored dependency versions need
those capabilities before activation.

## Retries and partial publication

Every source artifact and existing destination is checked before any write.
Missing artifacts, changed digests, unexpected inventory, or a different stable
version stop the run.

A draft GitHub release stores the exact promotion intent before registry writes.
Attempt reports remain release assets. Matching outputs are verified and reused
on a rerun; different outputs are never deliberately overwritten. The original
source run and attempt remain bound to that intent.
The intent also binds the approved shared publisher SHA; changing it during a
partial retry is rejected.

The stable tag points to the RC commit. It is created only after all destination
artifacts verify, then the draft is published without changing the latest-release
marker. A failure can leave some registry versions or a draft release. Do not
delete them or choose a different RC for the same stable version. Inspect the
intent and attempt reports, correct access or transport failures, and rerun the
same RC. A failed or ambiguous write is not automatically retried.

Registry writes do not provide a cross-service transaction or compare-and-set
tag operation. The common GitHub concurrency group serializes these workflows;
registry policy must exclude independent writers or tag replacement. Publication
to another system is outside this workflow.
