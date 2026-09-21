# Resolve Release Candidate

Selects a semantic-release result for release-candidate artifact publishing.
Only versions matching `MAJOR.MINOR.PATCH-rc.NUMBER` are selected by default, so
stable releases can continue through a separate production publishing path.

The action also makes publishing idempotent. If semantic-release created the RC
tag but a later artifact job failed, rerunning the workflow selects an RC tag
that already points at `HEAD` instead of requiring another release commit.

For standard source RC publishing, use the
[recommended shared workflow](../../../docs/release-candidate-publishing.md#recommended-minimal-workflow).
It owns protected-branch checks, isolated configuration, preview and
actual-publication version guards, and GitHub prerelease verification.
Product repositories need no Node files, `.releaserc` changes or custom
scripts for that path. The action below is a lower-level selector for
[advanced composition](../../../docs/release-candidate-publishing.md#advanced-composition)
with repository-owned plugins or custom release policy; it does not itself
validate branch protection, branch targets, source provenance or GitHub releases.

## Usage

Checkout the complete tag history, run semantic-release, then pass its outputs
to this action:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0

- name: Create release
  id: semantic
  uses: dsx-ai-factory/dsx-github-actions/.github/actions/semantic-release@REPLACE_WITH_REVIEWED_COMMIT_SHA

- name: Resolve RC publishing
  id: rc
  uses: dsx-ai-factory/dsx-github-actions/.github/actions/resolve-release-candidate@REPLACE_WITH_REVIEWED_COMMIT_SHA
  with:
    new-release-published: ${{ steps.semantic.outputs.new-release-published }}
    new-release-version: ${{ steps.semantic.outputs.new-release-version }}
    new-release-git-tag: ${{ steps.semantic.outputs.new-release-git-tag }}

- name: Publish RC artifacts
  if: steps.rc.outputs.should-publish == 'true'
  run: ./publish "${{ steps.rc.outputs.version }}"
```

Replace `REPLACE_WITH_REVIEWED_COMMIT_SHA` with a full reviewed commit SHA
before use; it is a placeholder, not a usable ref. The checkout must contain
tags for the rerun fallback to work.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `working-directory` | `.` | Git checkout in which to resolve tags; use `release-source` for a separate source checkout. |
| `new-release-published` | `false` | Whether semantic-release published a release in this run. |
| `new-release-version` | Empty | Version from the publishing step, not its preview. |
| `new-release-git-tag` | Empty | Git tag from the publishing step. |
| `prerelease-identifier` | `rc` | Prerelease identifier to accept. |
| `tag-prefix` | `v` | Expected Git tag prefix. |

The `working-directory` input is optional and preserves the existing `.`
behavior. Pin to a reviewed commit containing this input when using a
separate checkout. The shared workflow manages this input internally.

## Outputs

| Output | Description |
| --- | --- |
| `should-publish` | `true` only when the current commit resolves to an accepted RC |
| `version` | RC version without the Git tag prefix |
| `tag` | Full Git tag |
| `reused-existing-tag` | `true` when a rerun reused an RC tag already on `HEAD` |

These action outputs select an RC; they are not proof that it has a verified
GitHub prerelease. The [shared workflow](../../workflows/README.md#release-candidate-release-candidateyml)
adds that verification and a `release-url` output before product-native NGC
jobs consume the release decision. Neither this selector nor that workflow
publishes NGC artifacts.
