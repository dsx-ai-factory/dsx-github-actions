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
| `security-scan-enabled` | If `true`, build once to OCI, scan every requested Linux platform, then optionally publish the same digest | No | `false` |
| `security-scan-fail-on-critical` | If `true`, fail when Critical vulnerabilities are found | No | `true` |

## Outputs

| Output | Description |
| --- | --- |
| `digest` | Image digest reported by `docker/build-push-action` |
| `tags` | Normalized fully qualified image refs used for the build |

## Notes

- If `push: "true"` but `username/password` are not provided, this action assumes you have already logged in earlier in the job.
- Scanned publication resolves Docker `credHelpers` / `credsStore` on the host (or reads inline credentials). Only the destination registry's credentials are passed to Skopeo through a temporary `0600` authfile, which is removed on exit; host credential helpers must be available in `PATH`.
- When `security-scan-enabled: "true"`, Buildx exports all requested platforms once to `$RUNNER_TEMP/docker-build-scan.XXXXXX/candidate` (an OCI layout, not the Docker daemon or a registry). Allow enough runner disk space for all platforms and scanner extraction.
- The **Scan all platforms** step runs `scripts/scan-oci.sh`: Syft reads each platform from that layout and Grype scans its SBOM. These tools inspect files; they do not execute the target image. Reports and SPDX SBOMs for each platform are uploaded as a workflow artifact, including on scan failure.
- The separate **Publish scanned artifact** step runs only when scanning succeeds and `push: "true"`. Skopeo copies the original index, images, and attestations to each requested tag with `--all --preserve-digests`; there is no second build. `push: "false"` performs the same scans without publication. Disabling scanning retains the existing direct Buildx push path.
- Setting `security-scan-fail-on-critical: "false"` permits Critical findings, but scanner errors, missing platforms, and digest mismatches still fail the action and prevent publication.
- The scan path requires a Linux runner with Bash, jq, and Docker. Syft, Grype, and Skopeo run in digest-pinned containers configured in `action.yml`; no Python or host installation of these tools is needed. The bundled script is resolved through `$GITHUB_ACTION_PATH`, so callers do not need it in their own checkout.
