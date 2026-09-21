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
package files are absent. Shared helpers live in sibling `dsx-rc-actions`;
only the disposable source checkout receives generated RC configuration.
The product's stable-release configuration remains unchanged.

## Helper Responsibilities

| File | Responsibility |
| --- | --- |
| `check-source.sh` | Require an exact `release/X.Y.Z` push, protected ref, full history and a checked-out event commit that is still the remote branch head. |
| `config.cjs` | Generate isolated Conventional Commits RC configuration with a separate stable-history branch, `v${version}` tags, the version guard and GitHub prerelease publishing. |
| `validate-version.cjs` | Require `X.Y.Z-rc.N` to match the branch, with a positive sequence and no leading zeros. Exposes both a CLI preview guard and semantic-release's actual-publication `verifyRelease` guard. |
| `verify-existing-tag.sh` | Verify a rerun tag's version, source commit and semantic-release `rc` channel notes before reusing it. Missing or invalid metadata requires maintainer repair. |
| `verify-prerelease.sh` | Verify the source tag and a non-draft GitHub prerelease. Recover only a missing release for an existing verified tag, never move a tag or overwrite an incompatible release. |
| `tests/` | Shared release contract tests run by `release-candidate-checks.yml` before the publishing job. |

The workflow previews new releases, validates the result, publishes with the
same guard active, and resolves the final RC with
[`resolve-release-candidate`](../resolve-release-candidate/README.md).
Existing tags are detected before semantic-release to support reruns.
`should-publish` is exposed as `true` only after prerelease verification.

## Consumer Boundary

- Inputs are `runner` (default `ubuntu-latest`) and `default-branch` (default
  the caller repository's default branch). Central checks use `ubuntu-latest`.
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
