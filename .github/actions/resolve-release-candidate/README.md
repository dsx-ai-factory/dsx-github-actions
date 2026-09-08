# Resolve Release Candidate

Selects a semantic-release result for release-candidate artifact publishing.
Only versions matching `MAJOR.MINOR.PATCH-rc.NUMBER` are selected by default, so
stable releases can continue through a separate production publishing path.

The action also makes publishing idempotent. If semantic-release created the RC
tag but a later artifact job failed, rerunning the workflow selects an RC tag
that already points at `HEAD` instead of requiring another release commit.

## Usage

Checkout the complete tag history, run semantic-release, then pass its outputs
to this action:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0

- name: Create release
  id: semantic
  uses: dsx-ai-factory/dsx-github-actions/.github/actions/semantic-release@<commit-sha>

- name: Resolve RC publishing
  id: rc
  uses: dsx-ai-factory/dsx-github-actions/.github/actions/resolve-release-candidate@<commit-sha>
  with:
    new-release-published: ${{ steps.semantic.outputs.new-release-published }}
    new-release-version: ${{ steps.semantic.outputs.new-release-version }}
    new-release-git-tag: ${{ steps.semantic.outputs.new-release-git-tag }}

- name: Publish RC artifacts
  if: steps.rc.outputs.should-publish == 'true'
  run: ./publish "${{ steps.rc.outputs.version }}"
```

Pin the action to a full commit SHA. The checkout must contain tags for the
rerun fallback to work.

## Outputs

| Output | Description |
| --- | --- |
| `should-publish` | `true` only when the current commit resolves to an accepted RC |
| `version` | RC version without the Git tag prefix |
| `tag` | Full Git tag |
| `reused-existing-tag` | `true` when a rerun reused an RC tag already on `HEAD` |
