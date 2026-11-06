# Verify and promote RCs in GitLab

Use this adapter when GitLab owns NGC publishing. GitHub runs product CI and
creates the RC tag and prerelease, then finishes. It needs no NGC credentials.
GitLab publishes and verifies the RC, then promotes it after QA approval.

## GitHub tags, GitLab promotes

1. QA validates an RC with a successful GitLab `verify-rc` job.
2. A maintainer runs GitHub's Promote RC workflow from the protected default
   branch and selects that exact RC. The `stable-release` reviewers approve it.
3. GitHub creates an annotated `vX.Y.Z` tag at the RC commit and publishes the
   release notes. It makes no registry requests and needs no NGC credentials.
4. The mirrored stable tag starts GitLab's `promote-release` job. GitLab checks
   the selected RC and its verification record, preserves image digests, updates
   chart release metadata, and verifies the resulting artifacts.

The stable tag exists before artifact promotion finishes. Wait for GitLab's
`promote-release` job to pass before consuming the stable artifacts. A failed
job can be retried for the same tag. Never move that tag to a different RC.

The product's manual GitHub caller uses `promote-rc-tag.yml` at a reviewed commit:

```yaml
jobs:
  promote:
    permissions:
      contents: read
      actions: read
      deployments: read
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/promote-rc-tag.yml@<reviewed-full-commit-sha>
    with:
      rc_version: ${{ inputs.rc_version }}
      policy_path: .gitlab/rc-promotion.json
    secrets:
      RC_PROMOTION_APP_PRIVATE_KEY: ${{ secrets.RC_PROMOTION_APP_PRIVATE_KEY }}
```

Keep the existing [GitHub approval controls](promote-rc.md#authorization-and-cutover)
and repository-scoped release App. Pass only the App secret to this workflow,
not NGC keys. Both platforms use the same `.gitlab/rc-promotion.json` policy.
Set `gitlab.stable_tag_pipeline` to `true` when adding the stable-tag job.
GitHub checks this opt-in at the selected RC commit before creating a stable tag.
The complete policy must match the protected default branch. Use a new RC if
its GitHub CI workflow was rerun, so the selected attempt cannot differ from
GitLab's verification record.

GitLab runs `gitlab.py promote-tag --policy .gitlab/rc-promotion.json` in an
automatic `promote-release` job for protected stable-tag push pipelines only.
Use the `stable-release` environment, the `dsx-stable-release` resource group,
and retain `promotion-result.json` on success and failure. Keep the existing RC
`verify-rc` job. Do not run image builds or the old manual `promote-rc` job for
stable tags.

Before switching:

- Protect stable tags in both repositories. In GitHub, restrict creation,
  updates, and deletion for `refs/tags/v*`, exclude `refs/tags/v*-rc.*`, and allow
  the release App to create stable tags. Keep RC tag permissions unchanged.
  The GitLab mirror account must be allowed to create protected stable tags and
  deploy to `stable-release` as a Maintainer or Owner.
- Keep GitLab's read credentials for GitHub and RC verification records. Artifact
  promotion needs NGC credentials, but no GitHub write credential.
- Merge the GitLab tag pipeline configuration into the active release branch,
  then publish and validate a new RC. Tag pipelines use the selected RC commit's
  configuration, so an older RC cannot pick up this change from the default branch.
- Remove the competing manual GitLab tag-creation job when enabling the GitHub
  caller. The existing GitLab-owned tagging mode below remains available for
  products that have not switched.

The annotated stable tag records the exact source RC, commit, and GitHub CI
attempt. It is selection metadata, not proof that artifacts were published.
GitLab obtains artifact digests from its successful RC verification job and
rejects missing evidence, changed tags, or conflicting stable artifacts.

## Pipeline setup

The following setup keeps both tagging and artifact promotion in GitLab.
Use it only when that is the product's chosen release model.

Pin this repository to a reviewed full commit SHA. Run
`.github/actions/promote-rc/gitlab.py` with Python 3 and these tools available:
GitHub CLI, Docker Buildx, Helm 3, NGC CLI, skopeo, OpenSSL, Node.js 20.8.1 or
newer, and mikefarah/yq v4. Install the pinned release-note dependencies with
`npm ci --ignore-scripts --legacy-peer-deps --prefix .github/actions/promote-rc`.

- GitHub: use `release-candidate-publish.yml` with `artifact-publisher: external`
  and `promotion-manifest: false`. Remove the product's GitHub promotion workflow.
- GitLab RC tags: run `gitlab.py verify --policy .gitlab/rc-promotion.json` in
  `verify-rc` after every image and chart publish job. Retain
  `rc-promotion-manifest.json` as a job artifact.
- GitLab default branch: run `gitlab.py promote --policy .gitlab/rc-promotion.json`
  in a manual `promote-rc` job for `web` or `api` pipelines with `RC_VERSION` set.
  Use the protected `stable-release` environment and a non-cancelling
  `dsx-stable-release` resource group. Set `timeout: 45m` so the job finishes
  within the App token's one-hour lifetime. Retain `promotion-result.json`,
  including on failure. Do not run this job from the RC tag's code.

Move the existing [promotion policy](promote-rc.md#product-configuration) to
`.gitlab/rc-promotion.json` and add:

```json
"gitlab": {
  "api_url": "https://gitlab.example.com/api/v4",
  "project_id": 123,
  "repository": "owner/product",
  "deploy_group_ids": [],
  "approver_group_ids": []
}
```

Replace both empty lists with the approved GitLab group IDs. Configure matching
group rules on `stable-release`, require independent approval, and disable
pipeline-triggerer approval. Empty lists allow RC verification but stop promotion.
Use exact `artifact_download_hosts` only if GitLab redirects artifact downloads
to approved HTTPS storage. Redirects never receive GitLab credentials.

### Maintainer-only promotion

To let this project's Maintainers or Owners promote without a second approval,
set `gitlab.authorization` to `"maintainer"`. Leave `deploy_group_ids` and
`approver_group_ids` empty (or omit them). The default, `"groups"`, keeps the
independent group-approval flow above.

Protect `stable-release` with exactly one deploy rule: `access_level: 40`.
Remove group/user exceptions and deployment approval rules, including the legacy
approval count. Keep `promote-rc` manual. The adapter checks both the pipeline
creator and the person running the job through this project's `members/all`
API: both must be active with access level 40 or higher. They can be the same
person. The pipeline-triggerer self-approval setting does not apply in this mode.

Set the project's minimum role for pipeline variable overrides to Maintainer
to restrict who can start a pipeline with `RC_VERSION`. RC evidence, protected
default-branch checks, digests and credential isolation are unchanged.

## Credentials

Keep release credentials masked and protected. Use a dedicated project-scoped
`GITLAB_RELEASE_READ_TOKEN` with `read_api` for metadata. `CI_JOB_TOKEN` proves
the running job's identity. For GitHub reads, configure a separate App with
Contents and Actions read access using `RC_VERIFICATION_APP_ID`,
`RC_VERIFICATION_APP_INSTALLATION_ID`, and `RC_VERIFICATION_APP_PRIVATE_KEY_FILE`.
These protected variables must be available to both verification and promotion.
`RC_VERIFICATION_APP_PRIVATE_KEY` accepts PEM text instead of a file, not both.

For promotion, scope `RC_PROMOTION_APP_ID`, `RC_PROMOTION_APP_INSTALLATION_ID`,
and the file variable `RC_PROMOTION_APP_PRIVATE_KEY_FILE` to `stable-release`.
The writer App needs Contents and Workflows write access, not Actions access.
It is used only after deployment authorization when separate read credentials are
configured. Tokens are limited to the product repo and revoked on exit.

Reads prefer the verification App, then `GH_TOKEN` (Contents and Actions read).
Without either, the existing single-App setup still works if the promotion App
also has Actions read access. Partial App configuration fails instead of falling
back. A dedicated `RC_PROMOTION_GITHUB_TOKEN` remains supported instead of the
writer App, not alongside it. Never reuse a personal token or another service's
credential.

Set `RC_PROMOTION_SOURCE_NGC_KEY` for verification. Promotion also needs
`RC_PROMOTION_DESTINATION_NGC_KEY`, scoped to `stable-release`. Keep write-capable
GitHub credentials out of RC verification jobs.

## Release steps

1. Have QA validate an RC with a successful GitLab `verify-rc` job.
2. Run a GitLab pipeline on the current protected default branch with
   `RC_VERSION=X.Y.Z-rc.N`.
3. Run `promote-rc` as a project Maintainer or Owner, or approve through the
   configured groups first if using group mode.
4. GitLab verifies the recorded RC artifacts, copies images without changing
   their digests, updates chart release metadata, and creates `vX.Y.Z` on GitHub.

Conflicting stable artifacts, missing approvals, changed tags, or missing
verification records stop promotion. Retry the same RC after a partial failure.
RCs created before this cutover need a new RC with GitLab verification records.
Complete credentials and environment setup before enabling the product changes.
