# Workflows

This directory contains automated workflows for the dsx-github-actions repository.

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
