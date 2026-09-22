# Docker Build

Build (and optionally push) OCI images with Docker Buildx. Supports multi-arch builds and GitHub Actions cache (`type=gha`).

## Usage

### Build only (no push)

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Build image (no push)
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/docker-build@main
    with:
      image: ghcr.io/myorg/myapp
      tags: |
        sha-${{ github.sha }}
      push: "false"
```

### Build + push (with registry login)

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Build and push
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/docker-build@main
    with:
      image: nvcr.io/myorg/myapp
      tags: |
        latest
        sha-${{ github.sha }}
      push: "true"
      registry: nvcr.io
      username: ${{ secrets.NVCR_USERNAME }}
      password: ${{ secrets.NVCR_TOKEN }}
```

### Build + security scan gate + push

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Build, scan, and push
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/docker-build@main
    with:
      image: nvcr.io/myorg/myapp
      tags: |
        latest
        sha-${{ github.sha }}
      security-scan-enabled: "true"
      security-scan-fail-on-critical: "true" # set to "false" to allow criticals without failing
      push: "true"
      registry: nvcr.io
      username: ${{ secrets.NVCR_USERNAME }}
      password: ${{ secrets.NVCR_TOKEN }}
```

## Inputs

| Input | Description | Required | Default |
| --- | --- | --- | --- |
| `image` | Image repository without tag (e.g. `nvcr.io/org/app` or `ghcr.io/org/app`) | Yes | |
| `tags` | Tags (comma or newline separated). Each item may be a tag (`latest`) or a full ref (`ghcr.io/org/app:latest`). If empty, defaults to `sha-<shortsha>`. | No | `""` |
| `context` | Build context | No | `.` |
| `dockerfile` | Path to Dockerfile | No | `Dockerfile` |
| `platforms` | Target platforms | No | `linux/amd64,linux/arm64` |
| `push` | Whether to push | No | `false` |
| `registry` | Registry host for login (`nvcr.io`, `ghcr.io`). Empty means Docker Hub. | No | `""` |
| `username` | Registry username (used when `push: "true"`) | No | `""` |
| `password` | Registry password/token (used when `push: "true"`) | No | `""` |
| `cache` | Enable GitHub Actions cache (`type=gha`) | No | `true` |
| `cache-scope` | Cache scope (defaults to sanitized `image`) | No | `""` |
| `build-args` | Build args (one per line, `KEY=VALUE`) | No | `""` |
| `labels` | OCI labels (one per line, `key=value`) | No | `""` |
| `target` | Target stage | No | `""` |
| `buildkit-config` | BuildKit config path, or empty for Docker defaults | No | `/etc/buildkit/buildkitd.toml` |
| `provenance` | Provenance setting (empty uses docker default) | No | `""` |
| `sbom` | SBOM setting (empty uses docker default) | No | `""` |
| `security-scan-enabled` | If `true`, run SBOM+Grype scan on a locally-built `linux/amd64` image before main build/push | No | `false` |
| `security-scan-fail-on-critical` | If `true`, fail when Critical vulnerabilities are found | No | `true` |

## Outputs

| Output | Description |
| --- | --- |
| `digest` | Image digest reported by `docker/build-push-action` |
| `tags` | Normalized fully qualified image refs used for the build |

## Notes

- If `push: "true"` but `username/password` are not provided, this action assumes you have already logged in earlier in the job.
- When `security-scan-enabled: "true"`, this action builds a temporary local `linux/amd64` image to scan, then proceeds to the main (possibly multi-arch) build/push if the scan policy allows.
