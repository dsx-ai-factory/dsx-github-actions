# CDS Containers for GitHub Actions

Container images for CDS tooling, optimized for GitHub Actions workflows. These images provide consistent development and CI/CD environments across DSX projects.

> **Note**: This is the GitHub-compatible version. Internal tools requiring NVIDIA network access (nvault, cds-cli) have been removed. For GitLab with internal tools, see `registry.gitlab-master.nvidia.com/cds/cds-containers`.

---

## 🐳 Available Containers

### 1. `cds-tools` - CDS Tools Container
Comprehensive tooling for infrastructure automation, CI/CD, and Kubernetes operations.

**Image**: `ghcr.io/dsx-ai-factory/dsx-cds-tools:0.0.2`

**Includes**:
- **Bazel** (multiple versions):
  - `bazel6` - Bazel 6.5.0 (for KubeVirt compatibility)
  - `bazel8` - Bazel 8.4.0 (latest stable)
  - `bazel` - Default symlink (→ bazel8)
- **Kubernetes**: kubectl, kubelogin, helm
- **Infrastructure**: terraform, terragrunt
- **NVIDIA**: NGC CLI
- **Container tools**: docker CLI, regctl
- **Dev tools**: Node.js 24, Python 3, git, make, jq, yq, uv (Python package manager)

**Size**: ~500MB

### 2. `cds-go-dev-1.24-alpine` - Go Development (Alpine)
Lightweight Go 1.24 development environment with essential tooling.

**Image**: `ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-alpine:0.0.2`

**Includes**:
- Go 1.24.3 (Alpine-based)
- Air (hot reloading)
- Delve (debugging)
- golangci-lint
- goimports
- swag (Swagger documentation)

**Size**: ~50MB

**Use when**: You need a minimal, fast container for Go development and CI.

### 3. `cds-go-dev-1.24-debian` - Go Development (Debian)
Full-featured Go 1.24 environment with better C library compatibility.

**Image**: `ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-debian:0.0.2`

**Size**: ~300MB+

**Use when**:
- CGO-enabled packages with complex C dependencies
- Specific Debian packages not available in Alpine
- Troubleshooting compatibility issues with musl (Alpine's libc)

---

## 📦 Using Images in GitHub Actions

### Method 1: Using `container` in Job (Recommended)

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      packages: read  # Required to pull from GHCR

    container:
      image: ghcr.io/dsx-ai-factory/dsx-cds-tools:0.0.2
      credentials:
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Build with Bazel
        run: bazel build //...

      - name: Deploy with Terraform
        run: |
          terraform init
          terraform plan
```

### Method 2: Using Docker Run in Steps

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Run build in container
        run: |
          docker run --rm \
            -v $PWD:/workspace \
            -w /workspace \
            ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-alpine:0.0.2 \
            go build ./...
```

### Method 3: Building Custom Image Based on CDS Containers

```dockerfile
FROM ghcr.io/dsx-ai-factory/dsx-cds-tools:0.0.2

# Add your custom tools
RUN apt-get update && apt-get install -y \
    your-custom-package

WORKDIR /app
```

---

## 🔒 Private Image Access

### For repos in the same org (dsx-ai-factory):
- Grant the consuming repository **Read** access under the package's **Manage Actions access** settings.
- Add `permissions: packages: read` to the consuming job.
- Use that job's `GITHUB_TOKEN` in the container credentials.

### For cross-org or external repos:
1. Create a Personal Access Token (PAT) with `read:packages` scope
2. Add it as a repository secret: `GHCR_PAT`
3. Use it in credentials:
   ```yaml
   credentials:
     username: ${{ github.actor }}
     password: ${{ secrets.GHCR_PAT }}
   ```

---

## 🏗️ Versioning and Updates

### Current Version
The current version is defined in [`VERSION`](./VERSION): **0.0.2**

This file contains only the semantic version number (e.g., `0.0.1`).

Version tags and `sha-<40-character-commit-sha>` tags are immutable. The
publishing workflow refuses to change an existing version or SHA tag's digest, so
every active image-content change requires a `VERSION` bump. This applies to
`tools`, `go-dev-1.24-alpine`, and `go-dev-1.24-debian`. The `latest` tag is mutable and
must not be used when reproducibility matters. Pinning the published digest is
the strongest reference:

```yaml
container:
  image: ghcr.io/dsx-ai-factory/dsx-cds-tools@sha256:<digest>
```

### Updating Containers

Pushes to `main` and copy-pr-bot branches (`pull-request/**`) build and smoke-test
all three images. Mirror branches and manual runs test locally loaded images
without publishing. Only a push to `main` that changes `VERSION` publishes.

For publishing runs, Buildx generates an SPDX SBOM alongside each image. The
workflow pushes unique staging tags and tests their exact digests. After all
images pass, it signs build provenance and the existing SBOM for each tested
digest, storing the attestations in GitHub and GHCR. All three images must pass
attestation before it checks that every version and SHA tag is
either unused or already points to that image's tested digest, then creates only
the missing release tags. Authentication, network, and unexpected registry errors
stop publication. Only an explicit `MANIFEST_UNKNOWN` response permits a new tag.

Runs for each branch are serialized and queued (up to 100 pending runs).
An older queued version does not update `latest` after `main` has moved to a newer
version. Publication across multiple images is not atomic. If promotion partially
fails, use **Re-run failed jobs** while the tested-digest artifacts are available
(retained for one day). The publication job reuses those digests, skips matching
version/SHA tags, and creates the missing tags. Rebuilding can produce different
digests; conflicting published tags still require a new `VERSION`.

### Verifying provenance and SBOMs

Attestations apply to new version releases after this workflow change; existing
images are not retroactively attested. CDS images currently contain one runtime
platform, so both attestations identify the same published image index digest.
Replace `<digest>` below with the digest you intend to use. Authenticate `gh` and
Docker with access to the repository and package before verifying:

```bash
image='oci://ghcr.io/dsx-ai-factory/dsx-cds-tools@sha256:<digest>'
for predicate in https://slsa.dev/provenance/v1 https://spdx.dev/Document; do
  gh attestation verify "$image" \
    --repo dsx-ai-factory/dsx-github-actions \
    --signer-workflow dsx-ai-factory/dsx-github-actions/.github/workflows/build-cds-containers.yml \
    --source-ref refs/heads/main \
    --predicate-type "$predicate" || exit 1
done
```

Use the same commands with either Go image name. Multi-platform image support
would require per-platform SBOM attestations; the current extraction rejects it.

### Making an update

When you need to update tools or fix issues:

1. **Make changes** to Dockerfiles in this folder
2. **Update VERSION**:
   - Edit the file to contain only the new version number
   - Follow [Semantic Versioning](https://semver.org/)
   - Example: `0.1.0`
3. **Commit and push**:
   ```bash
   git add cds-containers/
   git commit -m "feat: upgrade kubectl to v1.33.0"
   git push
   ```
4. **Pipeline auto-triggers** - Runs when container files, the build workflow, or its registry guard change
5. **Images are tagged** with the version from `VERSION` and the full commit SHA

**Version Bumping**:
- **PATCH** (0.0.1 → 0.0.2): Bug fixes, base image updates
- **MINOR** (0.0.1 → 0.1.0): New tools, tool version upgrades
- **MAJOR** (0.0.1 → 1.0.0): Breaking changes, removed tools

**Example Workflow**:
```bash
# Edit Dockerfile to upgrade a tool
vim cds-containers/tools/Dockerfile

# Update version (just the semver number)
echo "0.1.0" > cds-containers/VERSION

# Commit with descriptive message
git add cds-containers/
git commit -m "feat: upgrade kubectl to v1.33.0

Bump version to 0.1.0"
git push

# Pipeline runs automatically, creates:
# - ghcr.io/dsx-ai-factory/dsx-cds-tools:0.1.0
# - ghcr.io/dsx-ai-factory/dsx-cds-tools:sha-<40-character-commit-sha>
# - ghcr.io/dsx-ai-factory/dsx-cds-tools:latest (mutable)
```

---

## 🔄 Migration from GitLab

### GitLab → GitHub URL Mapping

| GitLab Registry | GitHub GHCR |
|-----------------|-------------|
| `registry.gitlab-master.nvidia.com/cds/cds-containers/tools:latest` | `ghcr.io/dsx-ai-factory/dsx-cds-tools:0.0.2` |
| `registry.gitlab-master.nvidia.com/cds/cds-containers/go-dev-1.24-alpine:1.0.0` | `ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-alpine:0.0.2` |

### Key Differences

| Feature | GitLab CI | GitHub Actions |
|---------|-----------|----------------|
| Registry Auth | `CI_REGISTRY_PASSWORD` | `GITHUB_TOKEN` |
| Image URL | `registry.gitlab-master.nvidia.com` | `ghcr.io` |
| Permissions | Inherited from repo | Explicit `permissions: packages: write/read` |
| Job syntax | `image: <url>` | `container: { image: <url>, credentials: {...} }` |
| Internal tools | ✅ nvault, cds-cli | ❌ Removed (no internal network) |
| Versioning | Git tags | VERSION file |

---

## 🛠️ Tools Container - Bazel Multi-Version Support

The `cds-tools` container includes multiple Bazel versions to support different projects:

**Usage in pipelines**:
```yaml
steps:
  # Use Bazel 6.5.0 for KubeVirt or projects requiring compatibility
  - name: Build with Bazel 6
    run: bazel6 build //...

  # Use Bazel 8.4.0 explicitly
  - name: Build with Bazel 8
    run: bazel8 build //...

  # Use default (currently Bazel 8.4.0)
  - name: Build with default Bazel
    run: bazel build //...
```

**For projects with Makefiles that call `bazel`**:

If your Makefile uses `bazel` commands directly, override the default:

```yaml
jobs:
  build:
    container:
      image: ghcr.io/dsx-ai-factory/dsx-cds-tools:0.0.2

    steps:
      - name: Override bazel to use version 6.5.0
        run: |
          ln -sf /usr/local/bin/bazel6 /usr/local/bin/bazel
          bazel --version  # Verify it's 6.5.0

      - name: Build (uses Bazel 6.5.0)
        run: make build
```

---

## 💰 GHCR Pricing (Private Images)

- **Storage**: 500 MB free, then $0.008/GB/day (~$0.25/GB/month)
- **Bandwidth**: 1 GB/month free
- **GitHub Actions**: Pulling images in GH Actions **does not count toward bandwidth** ✅

**Estimated cost for all 3 images**: ~$0.25/month (very affordable!)

---

## 📚 Contributing

### Adding a New Container

1. Create your image directory: `cds-containers/my-new-image/`
2. Add your `Dockerfile` inside
3. Update `.github/workflows/build-cds-containers.yml` to include it in the matrix:
   ```yaml
   - name: cds-my-new-image
     path: cds-containers/my-new-image
     description: "Description of your new image"
   ```
4. Update `VERSION` (at least MINOR bump)
5. Update this README to document the new image
6. Push changes - pipeline will build and test automatically

### Local Testing

```bash
# Build locally
docker build -t test-cds-tools -f cds-containers/tools/Dockerfile ./cds-containers

# Test locally
docker run --rm -it test-cds-tools bash

# Test specific tool
docker run --rm test-cds-tools bazel --version
```

---

## 🔗 Useful Links

- **GHCR Packages**: https://github.com/orgs/dsx-ai-factory/packages?repo_name=dsx-github-actions
- **GitHub Actions Workflow**: `.github/workflows/build-cds-containers.yml`
- **Version History**: See `CHANGELOG.md`
- **Original GitLab Repo**: https://gitlab-master.nvidia.com/cds/cds-containers

---

## 📝 Notes

- **No internal tools**: `nvault` and `cds-cli` are not included as GitHub runners cannot reach internal NVIDIA resources
- **For internal use**: Use GitLab version at `registry.gitlab-master.nvidia.com/cds/cds-containers/tools`
- **Immutable tagging**: Use a version, full commit SHA tag, or digest; `latest` is mutable
- **Path-filtered pipeline**: Pushes matching `cds-containers/**`, `.github/workflows/build-cds-containers.yml`, or `.github/scripts/check_cds_tags.py` trigger builds and smoke tests. Manual runs also build and test; release publication additionally requires a push to `main` that changes `VERSION`.
