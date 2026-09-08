# Release Candidate Artifact Publishing

Use this pattern when a GitHub-hosted DSX component must publish prerelease
artifacts to NGC while stable releases continue through another release path.

## Contract

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

The checkout must include tags. Expose the resolver outputs so independent
artifact jobs use one release decision and one version.

```yaml
jobs:
  release:
    runs-on: linux-amd64-cpu4
    permissions:
      contents: write
    outputs:
      publish-rc: ${{ steps.rc.outputs.should-publish }}
      version: ${{ steps.rc.outputs.version }}
      tag: ${{ steps.rc.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Preview release candidate
        id: preview
        if: startsWith(github.ref, 'refs/heads/release/')
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/semantic-release@<commit-sha>
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
          target_version="${GITHUB_REF_NAME#release/}"
          [[ "$PREVIEW_VERSION" == "$target_version"-rc.* ]]

      - name: Create release
        id: semantic
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/semantic-release@<commit-sha>
        with:
          semantic-version: 25.0.9
          extra-plugins: conventional-changelog-conventionalcommits@9.3.1

      - name: Resolve RC publishing
        id: rc
        if: startsWith(github.ref, 'refs/heads/release/')
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/resolve-release-candidate@<commit-sha>
        with:
          new-release-published: ${{ steps.semantic.outputs.new-release-published }}
          new-release-version: ${{ steps.semantic.outputs.new-release-version }}
          new-release-git-tag: ${{ steps.semantic.outputs.new-release-git-tag }}
```

## Image Job

Use `docker-build` with the resolved version. A matrix can publish multiple
images without encoding component-specific paths in the shared action.

```yaml
  publish-images:
    needs: release
    if: startsWith(github.ref, 'refs/heads/release/') && needs.release.outputs.publish-rc == 'true'
    runs-on: linux-amd64-cpu4
    environment: components-dev
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ needs.release.outputs.tag }}
      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/docker-build@<commit-sha>
        with:
          image: nvcr.io/ORG/TEAM/component
          tags: ${{ needs.release.outputs.version }}
          registry: nvcr.io
          username: $oauthtoken
          password: ${{ secrets.NGC_DSX_COMPONENTS_PUSH_KEY }}
          push: "true"
```

## Helm Job

Use `helm-package-push` with the same version. When a chart contains a local
chart dependency, update both the dependency constraint and the local chart's
version in the job workspace before packaging.

```yaml
  publish-chart:
    needs: release
    if: startsWith(github.ref, 'refs/heads/release/') && needs.release.outputs.publish-rc == 'true'
    runs-on: linux-amd64-cpu4
    environment: components-dev
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ needs.release.outputs.tag }}
      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-package-push@<commit-sha>
        with:
          chart-path: deploy/component
          chart-version: ${{ needs.release.outputs.version }}
          app-version: ${{ needs.release.outputs.version }}
          ngc-key: ${{ secrets.NGC_DSX_COMPONENTS_PUSH_KEY }}
          ngc-path: ORG/TEAM
          ngc-duplicate: skip
```

## Reruns

semantic-release creates the Git tag before downstream artifact jobs run. If a
later job fails, a workflow rerun normally reports that no new release was
created. `resolve-release-candidate` handles this by selecting one matching RC
tag already pointing at `HEAD`. Multiple matching RC tags fail closed because
the intended artifact version would be ambiguous.

Do not rebuild or overwrite an image that already has the RC tag. Stamp images
with `org.opencontainers.image.revision`; on rerun, verify that every required
platform has the expected revision and skip a matching image. Stamp Helm charts
with the same source revision. `ngc-duplicate: skip` is safe only when the
downloaded existing chart is verified against that revision. End every run by
checking that all expected images and charts exist at the one resolved version.
