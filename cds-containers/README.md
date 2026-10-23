# CDS Containers for GitHub Actions

Container images for CDS tooling, optimized for GitHub Actions workflows. These images provide consistent development and CI/CD environments across DSX projects.

> **Note**: This is the GitHub-compatible version. Internal tools requiring NVIDIA network access (nvault, cds-cli) have been removed. For GitLab with internal tools, see `registry.gitlab-master.nvidia.com/cds/cds-containers`.

---

## 🐳 Available Containers

### 1. `cds-tools` - CDS Tools Container
Comprehensive tooling for infrastructure automation, CI/CD, and Kubernetes operations.

**Image**: `ghcr.io/dsx-ai-factory/dsx-cds-tools:0.1.0`

**Includes**:
- **Bazel** (multiple versions):
  - `bazel6` - Bazel 6.5.0 (for KubeVirt compatibility)
  - `bazel9` - Bazel 9.3.1
  - `bazel` - Default symlink (→ bazel9)
- **Kubernetes**: kubectl, kubelogin, helm
- **Infrastructure**: terraform, terragrunt
- **NVIDIA**: NGC CLI
- **Container tools**: docker CLI, regctl
- **Dev tools**: Node.js 24.22.1, Python 3, git, make, jq, yq, uv 0.13.5 (Python package manager)

**Size**: ~500MB

### 2. `cds-go-dev-1.24-alpine` - Go Development (Alpine)
Lightweight Go 1.26.10 development environment with essential tooling. The `1.24`
image name is retained for compatibility; it no longer identifies the toolchain version.

**Image**: `ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-alpine:0.1.0`

**Includes**:
- Go 1.26.10 (Alpine-based)
- Air (hot reloading)
- Delve (debugging)
- golangci-lint
- goimports
- swag (Swagger documentation)

**Size**: ~50MB

**Use when**: You need a minimal, fast container for Go development and CI.

### 3. `cds-go-dev-1.24-debian` - Go Development (Debian)
Full-featured Go 1.26.10 environment with better C library compatibility. The public
image name is unchanged.

**Image**: `ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-debian:0.1.0`

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
      image: ghcr.io/dsx-ai-factory/dsx-cds-tools:0.1.0
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
            ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-alpine:0.1.0 \
            go build ./...
```

### Method 3: Building Custom Image Based on CDS Containers

```dockerfile
FROM ghcr.io/dsx-ai-factory/dsx-cds-tools:0.1.0

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
The current version is defined in [`VERSION`](./VERSION): **0.1.0**

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

### CA Incident Remediation (0.1.0)

The legacy `go-dev-1.24-*` image names now contain **Go 1.26.10**, not Go 1.24.
The toolchain upgrade uses a minor release, **0.1.0**, with unchanged public
paths. Go 1.26.9 lacks the [upstream CA fix](https://groups.google.com/g/golang-announce/c/66pojueaQek).
The tools image pins [Node 24.22.1](https://nodejs.org/en/blog/release/v24.22.1).
Delve v1.27.0 supports this toolchain. golangci-lint stays on v1.64.8, built with
pinned x/tools v0.45.0 and the existing Staticcheck tooling v0.6.1. Linux
compatibility tests selected this patched Go version to preserve v1 CLI/config.
All images refresh distro-managed CA stores with patched package minimums:
[Alpine](https://pkgs.alpinelinux.org/package/v3.24/main/x86_64/ca-certificates)
`20261020-r0`, [Debian](https://tracker.debian.org/news/1813397/accepted-ca-certificates-20250419deb13u1-source-into-stable-security/)
`20250419+deb13u1`, and [Ubuntu](https://ubuntu.com/security/notices/USN-8944-1)
`20260816ubuntu0.22.04.1`. No custom root blocklist or TLS bypass is used.

### Tools CA trust follow-up (unreleased)

The tools image updates uv to 0.13.5, system Python pip to 26.2.2, and certifi to
2026.10.23. Upgrading uv alone does not update Ubuntu's Python packages.
Bazel 9.3.1 includes the upstream Java CA fix. Bazel 6.5.0 remains available for
compatibility; `ca-certificates-java` and the system bazelrc select the updated
system Java trust store for both Bazel versions. The default is `bazel9`. Consumers
calling `bazel8` must update their pipelines and validate their builds with Bazel 9.
NGC CLI's independently bundled trust stores remain a separate follow-up.

### Updating Containers

Pushes to `main` and copy-pr-bot branches (`pull-request/**`) build and smoke-test
all three images. Mirror branches and manual runs test locally loaded images
without publishing. Only a push to `main` that changes `VERSION` publishes.

Publishing runs use the shared `docker-build` action to build once, generate
per-platform SPDX SBOMs with Syft, and push only unique candidate tags. CDS stays
on `linux/amd64` with vulnerability scanning disabled; SBOM generation does not
require scanning. Non-publishing runs use the same action with `load: "true"`
for local smoke tests, without registry publication or signing.

After all three images pass their existing smoke tests, the shared
`attest-image.yml` workflow signs provenance for each root and platform digest,
signs each platform's existing SBOM, and fetches the evidence from GHCR to verify
it. CDS leaves that workflow's release tags empty so its separate publication job
still waits for all three images. Only then does it check that every version and SHA tag is
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

For releases built with the shared workflow, provenance identifies the root and
each platform digest; the SPDX attestation identifies the platform manifest,
not the enclosing index. CDS currently builds only `linux/amd64`. Authenticate
`gh` and Docker with access to the repository and package before verifying:

```bash
image='ghcr.io/dsx-ai-factory/dsx-cds-tools'
root_digest='sha256:<published-root-digest>'
source_commit='<full-release-commit-sha>'
policy=(--repo dsx-ai-factory/dsx-github-actions --source-ref refs/heads/main
  --source-digest "$source_commit" --signer-digest "$source_commit"
  --signer-workflow dsx-ai-factory/dsx-github-actions/.github/workflows/attest-image.yml
  --bundle-from-oci)

manifest="$(docker buildx imagetools inspect "$image@$root_digest" --raw)"
platform_digest="$(jq -er --arg root "$root_digest" '
  if has("manifests") then
    .manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64") | .digest
  else $root end' <<< "$manifest")"
for digest in "$root_digest" "$platform_digest"; do
  gh attestation verify "oci://$image@$digest" "${policy[@]}" \
    --predicate-type https://slsa.dev/provenance/v1 || exit 1
done
gh attestation verify "oci://$image@$platform_digest" "${policy[@]}" \
  --predicate-type https://spdx.dev/Document/v2.3
```

Use the same commands with either Go image name. Older releases are not
retroactively re-attested; releases from the previous CDS-only workflow use
`build-cds-containers.yml` as their signer and attach the SBOM to the root digest.
The shared workflow supports multiple platforms, but this change does not make
the CDS Dockerfiles multi-platform.

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
| `registry.gitlab-master.nvidia.com/cds/cds-containers/tools:latest` | `ghcr.io/dsx-ai-factory/dsx-cds-tools:0.1.0` |
| `registry.gitlab-master.nvidia.com/cds/cds-containers/go-dev-1.24-alpine:1.0.0` | `ghcr.io/dsx-ai-factory/dsx-cds-go-dev-1.24-alpine:0.1.0` |

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

  # Use Bazel 9.3.1 explicitly
  - name: Build with Bazel 9
    run: bazel9 build //...

  # Use default (currently Bazel 9.3.1)
  - name: Build with default Bazel
    run: bazel build //...
```

**For projects with Makefiles that call `bazel`**:

If your Makefile uses `bazel` commands directly, override the default:

```yaml
jobs:
  build:
    container:
      image: ghcr.io/dsx-ai-factory/dsx-cds-tools:0.1.0

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

Stream the runtime checks into the exact built `linux/amd64` image. They assert
Go 1.26.10 (including tool build metadata), Node 24 >= 24.22.1, distro CA versions,
existing tool commands, v1 lint CLI/config, debugging, compilation, and CGO:

```bash
docker run --rm -i "$GO_IMAGE" sh -s -- go < cds-containers/tests/security-runtime.sh
docker run --rm -i "$TOOLS_IMAGE" sh -s -- tools < cds-containers/tests/security-runtime.sh
python3 -B -m unittest discover -s cds-containers/tests -v
```

The tests make no external TLS requests and disable Go toolchain auto-download
only during testing. Delve requires the runner to permit tracing its child
process. Third-party bundled runtimes or CA stores need separate
upstream maintenance. Host checks do not replace target-container validation.

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
