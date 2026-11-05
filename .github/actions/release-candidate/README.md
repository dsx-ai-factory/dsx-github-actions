# Shared Release Candidate Helpers

Internal implementation of the reusable
[`release-candidate.yml` workflow](../../workflows/release-candidate.yml), not
a standalone composite action. Consumers should use the
[recommended minimal workflow](../../../docs/release-candidate-publishing.md#recommended-minimal-workflow),
not copy these scripts or add Node files or `.releaserc` changes to a product
repository. The documented `REPLACE_WITH_REVIEWED_COMMIT_SHA` is a placeholder
until a reviewed commit containing the workflow and helpers is available.

## Ownership and Isolation

The workflow checks out shared code using `job.workflow_repository` and
`job.workflow_sha` on GitHub.com. Its central checks use the same job context
and run before publishing. This avoids a circular self-pin and does not
execute shared helpers from the caller's product revision. GHES is outside
the current implementation's scope.

Source history lives in `release-source`, a full-history, non-cone sparse
checkout containing only `/.github/`. Product root release configuration and
package files are absent. Shared helpers and their pinned dependencies live in
sibling `dsx-rc-actions`. The product's stable-release configuration and the
generic `semantic-release` action remain unchanged.

## Helper Responsibilities

| File | Responsibility |
| --- | --- |
| `check-source.sh` | Require an exact `release/X.Y.Z` push, protected ref, full history and a checked-out event commit that is still the remote branch head. |
| `check-git-auth.sh` | In Deploy Key mode, require an SSH push dry run before publishing. No remote refs change; authentication failure stops publication. |
| `plan.mjs` | Select the stable baseline and target-line RC, analyze Conventional Commits, generate release notes, and atomically push the new tag and channel notes. |
| `publish.mjs` | Save the preview plan and notes, recheck the plan before publishing Git refs, and emit source outputs. Does not call the GitHub Release API. |
| `config.cjs` | Share repository URL and release-note options; retain the legacy semantic-release configuration helper. |
| `validate-version.cjs` | Require `X.Y.Z-rc.N` to match the branch, with a positive sequence and no leading zeros. The planner and publisher use these checks; the legacy `verifyRelease` hook remains available. |
| `verify-existing-tag.sh` | Verify a rerun tag's version, source commit and semantic-release `rc` channel notes before reusing it. Missing or invalid metadata requires maintainer repair. |
| `verify-prerelease.sh` | Verify or create the non-draft GitHub prerelease for a verified tag. Optional `RC_NOTES_PATH` supplies the preview's pinned Conventional Commits notes for publication and recovery. Never move a tag or overwrite an incompatible release. |
| `tests/` | Shared release contract tests run by `release-candidate-checks.yml` before the publishing job. |

The planner selects the highest repository-wide stable version below the branch
target and requires shared ancestry, not a direct ancestor. The cumulative
Conventional Commits bump from that baseline must equal the target. Only the
latest RC on that target line controls the next sequence, starting at 1.
No release-worthy changes means no new RC; a finalized stable target blocks new
RCs. See the [release contract](../../../docs/release-candidate-publishing.md#shared-release-contract).

Same-commit target-line RCs are reused through the existing metadata verifier.
Workflow-level concurrency remains per repository and ref, with
`cancel-in-progress: false`. Planning and publication independently check the
remote branch head and stable target before creating a new RC. The atomic push
protects tag-and-notes conflicts; it does not serialize manual or stable writers.
`should-publish` is exposed as `true` only after prerelease verification.

## Consumer Boundary

- Inputs are `runner` (default `ubuntu-latest`) and `default-branch` (empty by
  default, deprecated and unused, retained for compatibility). Central checks use `ubuntu-latest`.
- The optional `release-deploy-key` secret enables SSH for source Git
  operations through the checkout's origin. Without it, Git uses HTTPS; GitHub Release API calls
  use `GITHUB_TOKEN` in either mode. Central checks do not receive the key.
- The caller must configure protected source/tag rules, approved source and
  required product tests, and grant `contents: write` to `GITHUB_TOKEN`.
  Shared contract checks do not approve source changes or run product tests.
- The Linux runner needs Git supporting non-cone sparse checkout, Bash, `gh`
  and `jq`; the workflow supplies Node and release tooling.
- Outputs are `should-publish`, `version`, `tag`, `reused-existing-tag` and
  `release-url`. Product-native artifact jobs must gate on `should-publish`,
  check out `tag` and publish/verify artifacts at `version`.
- No stable releases, moving major tags or NGC artifacts are published here.
  No access to private dependency repositories or registry secrets is required.
  Product artifact jobs own NGC credentials, build layouts and duplicate/revision checks.

See the [workflow reference](../../workflows/README.md#release-candidate-release-candidateyml)
for the interface and [advanced composition](../../../docs/release-candidate-publishing.md#advanced-composition)
when a repository needs its own plugins or custom release policy.
