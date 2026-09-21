# Workflows

This directory contains automated workflows for the dsx-github-actions repository.

## Release Candidate Publish

Use [`release-candidate-publish.yml`](release-candidate-publish.yml) to publish
source RCs, NGC images, and Helm charts through one caller job. The caller
provides product manifests and access policy. Shared jobs own manifest
validation, source publishing, artifact publishing, and immutable-artifact
checks. No product RC scripts or separate publishing jobs are required.

Start with the [Exchange caller example](../../docs/release-candidate-publishing.md#recommended-minimal-workflow).
Replace `REPLACE_WITH_REVIEWED_COMMIT_SHA` with a reviewed full commit SHA
containing the wrapper and helpers before enabling the caller.

### Inputs

All inputs are strings. The two manifests contain JSON arrays.

| Input | Default | Purpose |
| --- | --- | --- |
| `runner` | `ubuntu-latest` | Approved Linux runner for preflight and publishing. Central checks use `ubuntu-latest`. |
| `default-branch` | Empty, resolved to the caller repository's default branch | Stable history for the unchanged source publisher. |
| `images` | `[]` | Entries with `name`, `context`, and `dockerfile`. Images use fixed `linux/amd64,linux/arm64` platforms. |
| `charts` | `[]` | Entries with `name`, `path`, and optional `localDependencies: [{"name": "dependency", "path": "path/to/chart"}]`. |
| `environment` | Required | GitHub environment for artifact jobs. Exchange uses `components-dev`. |
| `ngc-path` | Required | NGC organization/team path, without a registry hostname. Exchange uses `0837451325059433/components-dev`. |

Manifest paths are relative to the product repository root. Declare local chart
dependencies explicitly; the shared implementation does not encode Exchange
component names. Refer to the [manifest contract](../../docs/release-candidate-publishing.md#manifest-contract)
for examples and preflight requirements. Inputs contain JSON, not manifest-file
paths. Empty arrays disable that artifact type, but at least one image or chart
is required. Use `release-candidate.yml` when you only need source releases.

### Secrets

The workflow declares these optional secrets.

| Secret | Purpose |
| --- | --- |
| `release-deploy-key` | Forwarded only to the source publisher for Git operations. GitHub Release API calls still use `GITHUB_TOKEN`. |
| `NGC_DSX_COMPONENTS_PUSH_KEY` | NGC credential resolved in each artifact job's selected environment. A caller can pass a repository or organization secret when environment-scoped storage is not used. |

Configure `NGC_DSX_COMPONENTS_PUSH_KEY` in the selected GitHub environment for
the recommended setup. Do not pass an environment secret through the caller's
`secrets` mapping. Restrict the environment to approved protected release
branches. The NGC secret is optional in `workflow_call`, but artifact publishing
requires an available credential. Validation does not use publishing
credentials or the publishing environment.

### Publishing Contract

- Preflight parses the product manifests and validates paths before the source
  publisher can create an RC tag. The wrapper runs central checks on non-release
  events; the source publisher runs its own shared tests on release pushes.
  These checks do not replace required product tests or approval.
- Copy-pr-bot `push` and `pull_request` events can validate manifests when the
  caller enables them. They do not publish. Exchange uses copy-pr-bot pushes.
- Source publishing calls the existing `release-candidate.yml` workflow. Its
  exact branch, protected-ref, current-head, version, and prerelease gates remain
  in effect. The caller must grant `contents: write` for source publishing.
- Artifact jobs require the verified source release. They use the resolved tag
  and version, run with `contents: read`, and use the selected environment.
- Shared image checks verify both supported platforms and their source revision.
  Shared chart checks verify version, app version, and source revision. Matching
  artifacts are reused; a mismatch fails instead of overwriting an RC artifact.
- Shared chart preparation aligns explicitly declared local dependency versions
  before packaging. Product files are changed only in the job workspace.
- Neither stable releases nor moving tags are published. GitHub.com is supported;
  GHES and arbitrary image platform selection are outside this contract.

Refer to [first-RC acceptance](../../docs/release-candidate-publishing.md#verify-your-first-rc)
before declaring onboarding complete. Local checks do not prove live GitHub
tag authorization, NGC publishing, or a real RC rerun.

### Outputs

The wrapper exposes `version`, `tag`, and `release-url` from the source publisher.
These outputs identify the source release, not completion of all artifact jobs.
The source-only outputs `should-publish` and `reused-existing-tag` are not wrapper outputs.

## Release Candidate (`release-candidate.yml`)

The existing `workflow_call` entry point remains unchanged for source-only RC releases.
Start with the [source-only caller](../../docs/release-candidate-publishing.md#source-only-workflow).
Replace its `REPLACE_WITH_REVIEWED_COMMIT_SHA` placeholder with a full
reviewed commit containing this workflow before enabling it. No product Node
files, `.releaserc` changes or custom scripts are required.

### Inputs

| Input | Default | Purpose |
| --- | --- | --- |
| `runner` | `ubuntu-latest` | Approved Linux runner for the release job. Central checks use `ubuntu-latest`. |
| `default-branch` | Empty, resolved to the caller repository's default branch | Separate stable-history branch used for version calculation. |

### Optional Secret

| Secret | Default | Purpose |
| --- | --- | --- |
| `release-deploy-key` | Not provided | Repository-scoped, write-enabled SSH deploy key authorized by the repository's tag rules. Used only in the source release job for Git operations, not central checks or the shared-code checkout. |

When provided, the source checkout uses the key and the generated release
configuration uses `git@github.com:OWNER/REPO.git`. Without it, Git continues
using HTTPS and `GITHUB_TOKEN`. In both modes, `GITHUB_TOKEN` with
`contents: write` handles GitHub Release API calls; `GH_TOKEN` is set to that
same workflow token rather than inherited from the runner. Deploy Key mode
requires a successful SSH push dry run with a one-minute timeout before
publishing, so an unusable key fails before token fallback. See the
[onboarding example](../../docs/release-candidate-publishing.md#optional-deploy-key-authentication).

### Contract

- GitHub.com only, using `job.workflow_repository` and `job.workflow_sha` to
  check out the called workflow's shared code and tests at the same revision.
  GHES is outside this implementation's scope; no circular self-pin is needed.
- [Central RC checks](release-candidate-checks.yml) must pass before the
  release job. These check the shared contract, not product tests.
- Publishing requires a push to an exact, protected `release/X.Y.Z` branch
  whose current remote head matches the event commit. Product approval/tests
  and tag rules must be configured beforehand. The caller grants
  `contents: write` to the workflow's `GITHUB_TOKEN`.
- A full-history, non-cone sparse `release-source` checkout excludes product
  root configuration/package files. RC configuration is generated there;
  shared helpers live in sibling `dsx-rc-actions`. Product stable-release
  configuration is unchanged. The Linux runner needs Git with non-cone sparse
  support, Bash, `gh` and `jq`; the workflow sets up Node itself.
- The calculated version must be `X.Y.Z-rc.N` with a positive, canonical RC
  sequence. Both the preview and actual publication are guarded, including
  semantic-release's `verifyRelease` phase before tag creation.
- Reruns verify the existing source tag and `rc` channel metadata, then verify
  or recover the non-draft GitHub prerelease. Ambiguous tags or invalid
  metadata fail closed. Runs for an old branch head are rejected.
- Only source RC tags and GitHub prereleases are published, not stable/major
  tags or NGC artifacts. No access to private dependency repositories or
  registry secrets is required.

### Outputs

| Output | Description |
| --- | --- |
| `should-publish` | `true` only after the source RC and GitHub prerelease are verified. |
| `version` | RC version without `v`. |
| `tag` | Source RC Git tag. |
| `reused-existing-tag` | Whether this run reused an existing RC tag. |
| `release-url` | Verified GitHub prerelease URL. |

For NGC publishing, use the wrapper above. It consumes these outputs and owns
the artifact jobs. `should-publish` verifies the source RC, not completion of
all artifact jobs. Custom consumers must gate on `should-publish`, check out
`tag`, and verify every artifact at `version`. Refer to the
[helper reference](../actions/release-candidate/README.md) and
[advanced composition boundary](../../docs/release-candidate-publishing.md#advanced-composition)
for source-only integration outside the wrapper.

## Promote Image (`promote-image.yml`)

A reusable workflow that copies OCI images between registries (e.g., from NGC to GHCR or between repositories) using `skopeo`.

### Features

- **Multi-Arch Support**: Copies entire manifest lists (all architectures) by default
- **Credential Isolation**: Supports different credentials for source and destination registries (resolving common auth conflicts)
- **Tag or Digest**: Can promote by tag or pin to a specific digest
- **Output Digest**: Returns the SHA256 digest of the promoted image for downstream use

### Usage

```yaml
jobs:
  promote:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/promote-image.yml@d15d46d22d09f7111177a6f5e9f7ae9933e067b2 # v1.20.0
    with:
      source: nvcr.io/myorg/source-image
      source_tag: v1.0.0
      destination: ghcr.io/myorg/dest-image
      destination_tag: v1.0.0
    secrets:
      SOURCE_USERNAME: ${{ secrets.NGC_USER }}
      SOURCE_PASSWORD: ${{ secrets.NGC_KEY }}
      DEST_USERNAME: ${{ secrets.GHCR_USER }}
      DEST_PASSWORD: ${{ secrets.GHCR_TOKEN }}
```

## Docker Build (`docker-build.yml`)

A reusable workflow wrapper for building (and optionally pushing) OCI images via Docker Buildx. It supports multi-arch builds and GitHub Actions cache (`type=gha`) through the underlying composite action.

### Usage

```yaml
jobs:
  build:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/docker-build.yml@d15d46d22d09f7111177a6f5e9f7ae9933e067b2 # v1.20.0
    with:
      runner: ubuntu-latest
      image: nvcr.io/myorg/myapp
      tags: |
        latest
        sha-${{ github.sha }}
      push: true
      registry: nvcr.io
      security_scan_enabled: true
      security_scan_fail_on_critical: true
    secrets:
      REGISTRY_USERNAME: ${{ secrets.NVCR_USERNAME }}
      REGISTRY_PASSWORD: ${{ secrets.NVCR_TOKEN }}
```

### Outputs

- `digest`: Image digest
- `tags`: Normalized fully qualified tags used for build/push

### Security scan options

- `security_scan_enabled`: If true, performs a pre-build security scan on a locally-built `linux/amd64` image before the main build/push.
- `security_scan_fail_on_critical`: If true, fails the workflow when Critical vulnerabilities are found. Scan tool failures always fail and prevent pushing.

## Release Workflow (`release.yml`)

Automatically creates semantic version tags and releases when commits are pushed to the `main` branch using [semantic-release](https://github.com/semantic-release/semantic-release).

### How It Works

1. **Triggers**: On every push to `main` branch
2. **Uses Semantic Release**: Leverages industry-standard semantic-release tool
3. **Analyzes Commits**: Uses [Conventional Commits](https://www.conventionalcommits.org/) to determine version bump
4. **Creates Tags**: Generates semantic version tags (e.g., `v1.2.3`)
5. **Updates Major Tags**: Maintains major version tags (e.g., `v1`) for easy pinning
6. **Creates Release**: Generates GitHub release with auto-generated release notes

This workflow uses the `semantic-release` action from this repository (`./.github/actions/semantic-release`).

### Conventional Commits

The workflow follows conventional commit format to determine version bumps:

#### Major Version (Breaking Changes)

```
feat!: remove deprecated parameter
BREAKING CHANGE: old parameter no longer works
```

**Result**: `v1.0.0` → `v2.0.0`

#### Minor Version (New Features)

```
feat: add new post-pr-comment parameter
feature(codeql): support multiple languages
```

**Result**: `v1.0.0` → `v1.1.0`

#### Patch Version (Bug Fixes, etc.)

```
fix: resolve SARIF file not found issue
docs: update README
refactor: improve error handling
chore: update dependencies
```

**Result**: `v1.0.0` → `v1.0.1`

### Version Tags

The workflow creates two types of tags:

1. **Full Version Tags** (e.g., `v1.2.3`)

   - Intended to remain fixed
   - Prefer the corresponding full commit SHA for production

2. **Major Version Tags** (e.g., `v1`)
   - Points to latest minor/patch within major version
   - Force-updated automatically with every release
   - Not a production pin

### Usage Examples

**Pin to a full commit SHA** (supported for production):

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@d15d46d22d09f7111177a6f5e9f7ae9933e067b2 # v1.20.0
```

**Pin to major version** (development and evaluation only):

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@v1
```

**Use latest** (for development/testing):

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@main
```

### Manual Release

If you need to create a release manually:

1. **Tag locally**:

   ```bash
   git tag -a v1.0.0 -m "Release v1.0.0"
   git push origin v1.0.0
   ```

2. **The workflow will skip** if the tag already exists

### Changelog Generation

The workflow automatically generates changelogs organized by:

- ✨ **Features**: New capabilities
- 🐛 **Bug Fixes**: Fixes and corrections
- 🔧 **Other Changes**: Docs, refactoring, etc.

### Troubleshooting

#### No tag created

**Cause**: No commits since last tag
**Solution**: This is expected behavior

#### Wrong version bump

**Cause**: Commit messages don't follow conventional commits
**Solution**: Use proper commit format:

- `feat:` for features
- `fix:` for bug fixes
- Add `!` or `BREAKING CHANGE:` for major bumps

#### Tag already exists

**Cause**: Tag was manually created
**Solution**: Delete tag and re-push, or let workflow handle versioning

## Best Practices

1. **Use Conventional Commits**: Always follow the format for automatic versioning
2. **Review Before Merge**: Check commit messages before merging to main
3. **Breaking Changes**: Clearly mark with `!` or `BREAKING CHANGE:` footer
4. **Descriptive Messages**: Write clear commit messages for better changelogs

## Examples

### Good Commit Messages

```
feat(codeql-scan): add support for C++ language
fix(trivy-scan): resolve SARIF file parsing error
docs: update README with new examples
refactor(codeql-scan): improve PR comment formatting
```

### Breaking Change Example

```
feat(codeql-scan)!: change default build-mode to none

BREAKING CHANGE: The default build-mode is now 'none' instead of 'autobuild'.
Users must explicitly set build-mode: 'autobuild' if they want the previous behavior.
```

## References

- [Conventional Commits Specification](https://www.conventionalcommits.org/)
- [Semantic Versioning](https://semver.org/)
- [GitHub Actions: Creating releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
