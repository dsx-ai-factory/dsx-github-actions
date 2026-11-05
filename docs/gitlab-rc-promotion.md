# Verify and promote RCs in GitLab

Use this adapter when GitLab owns NGC publishing. GitHub runs product CI and
creates the RC tag and prerelease, then finishes. It needs no NGC credentials.
GitLab publishes and verifies the RC, then promotes it after QA approval.

## Pipeline setup

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
