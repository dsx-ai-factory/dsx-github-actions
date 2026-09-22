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

### Prepare signed publication, with or without vulnerability scanning

SBOM generation and vulnerability scanning are independent. Set
`prepare-attestation: "true"` on the composite action (or `prepare_attestation: true`
on the build workflow) to produce per-platform SPDX SBOMs. With `push: true`, this
publishes **only a run-unique candidate**, not the requested release tags. Then call
`attest-image.yml` to sign and verify all subjects before promoting those tags.

The separate workflow keeps OIDC permissions out of existing build-only callers.
Call it only on trusted publication events, and pin both workflows to the same
reviewed commit. For example, the following jobs belong in a trusted release workflow:

```yaml
jobs:
  build:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/docker-build.yml@<commit-sha>
    permissions:
      contents: read
      packages: write
    with:
      image: ghcr.io/myorg/myapp
      tags: sha-${{ github.sha }}
      platforms: linux/amd64,linux/arm64 # linux/amd64 alone uses the same flow
      registry: ghcr.io
      push: true
      prepare_attestation: true
      security_scan_enabled: false # true adds a scan gate before candidate push
    secrets:
      REGISTRY_USERNAME: ${{ github.actor }}
      REGISTRY_PASSWORD: ${{ secrets.GITHUB_TOKEN }}

  attest-and-publish:
    needs: build # include your smoke-test jobs here when applicable
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/attest-image.yml@<commit-sha>
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    with:
      image: ghcr.io/myorg/myapp
      digest: ${{ needs.build.outputs.digest }}
      platforms: linux/amd64,linux/arm64
      sbom_artifact_id: ${{ needs.build.outputs.sbom_artifact_id }}
      tags: ${{ needs.build.outputs.tags }}
      registry: ghcr.io
    secrets:
      REGISTRY_USERNAME: ${{ github.actor }}
      REGISTRY_PASSWORD: ${{ secrets.GITHUB_TOKEN }}
```

For a composite-action caller, expose `digest`, `tags`, and `sbom-artifact-id` as
job outputs, then call the same attestation workflow from a dependent job.
`push: false` still generates local evidence but does not upload a candidate to
the registry; the signing workflow requires the candidate to be present.

| Scan | Prepare attestation | Build action behavior |
| --- | --- | --- |
| Off | Off | Existing Buildx build/push behavior |
| On | Off | SBOM + scan all requested platforms, then publish requested tags |
| Off | On | SBOM all requested platforms, then push candidate only |
| On | On | SBOM + scan all requested platforms, then push candidate only |

The attestation workflow validates the registry's runtime platform set against the
build evidence. It signs an SPDX SBOM for each platform manifest, and provenance
for the root plus each distinct platform digest. This also handles a plain
single-platform manifest and a single-platform index containing BuildKit metadata.
It fetches the signed evidence from the registry and checks the signer workflow,
source ref/commit, subject digest, and expected SBOM contents. Every matrix job must
succeed before release tags are promoted, without rebuilding or changing the root
digest. A failure can leave a candidate and partial attestations, but will not
promote release tags. Registry tag updates are not an atomic transaction.

Leave the attestation workflow's `tags` empty to sign/verify only, keeping a
project-specific promotion policy (for example, CDS smoke tests and version gates).
Callers remain responsible for release-event restrictions and concurrency ordering
of mutable tags such as `latest`. Signing does not imply vulnerability scanning or
application/runtime testing; those gates must be explicitly enabled or supplied.

The signed path requires GitHub artifact-attestation access for the caller repo,
registry support for the OCI attestations written by `actions/attest`, and a Linux
runner with Docker, Bash, jq, and a `gh` CLI supporting `--bundle-from-oci`.
GHCR is the initial integration target; other registries require their own E2E
verification. Existing non-attested registry publication is unchanged.

Consumers can verify each platform by its manifest digest:

```bash
# Provenance: run for the root digest and each platform digest.
gh attestation verify oci://ghcr.io/myorg/myapp@sha256:<digest> \
  --repo myorg/myrepo --source-ref refs/heads/main --source-digest <source-commit> \
  --signer-workflow dsx-ai-factory/dsx-github-actions/.github/workflows/attest-image.yml \
  --signer-digest <shared-workflow-commit> --bundle-from-oci \
  --predicate-type https://slsa.dev/provenance/v1

# SBOM: run for each platform manifest digest, not the multi-platform index.
gh attestation verify oci://ghcr.io/myorg/myapp@sha256:<platform-digest> \
  --repo myorg/myrepo --source-ref refs/heads/main --source-digest <source-commit> \
  --signer-workflow dsx-ai-factory/dsx-github-actions/.github/workflows/attest-image.yml \
  --signer-digest <shared-workflow-commit> --bundle-from-oci \
  --predicate-type https://spdx.dev/Document/v2.3
```

Use the actual release source ref, commit, and reviewed workflow commit rather than
copying the example's `main` policy for a tag-based release.

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
| `prepare-attestation` | Generate SBOMs independently of scanning; when pushing, publish only a candidate for `attest-image.yml` | No | `false` |

## Outputs

| Output | Description |
| --- | --- |
| `digest` | Image digest reported by `docker/build-push-action` |
| `tags` | Normalized fully qualified image refs used for the build |
| `sbom-artifact-id` | Same-run artifact ID containing per-platform SBOMs and digest mapping |
| `candidate` | Run-unique candidate reference when preparing attestations (pushed only with `push: true`) |

## Notes

- If `push: "true"` but `username/password` are not provided, this action assumes you have already logged in earlier in the job.
- Scanned publication resolves Docker `credHelpers` / `credsStore` on the host (or reads inline credentials). Only the destination registry's credentials are passed to Skopeo through a temporary `0600` authfile, which is removed on exit; host credential helpers must be available in `PATH`.
- When `security-scan-enabled: "true"`, Buildx exports all requested platforms once to `$RUNNER_TEMP/docker-build-scan.XXXXXX/candidate` (an OCI layout, not the Docker daemon or a registry). Allow enough runner disk space for all platforms and scanner extraction.
- `scripts/sbom-oci.sh` catalogs each platform with Syft; the separate `scripts/scan-oci.sh` scans those SBOMs with Grype only when scanning is enabled. These tools inspect files; they do not execute the target image. Reports and SPDX SBOMs for each platform are uploaded as a workflow artifact, including on scan failure.
- The separate **Publish OCI artifact** step runs only after any enabled scanning succeeds and `push: "true"`. Skopeo copies the original index, images, and attestations with `--all --preserve-digests`; there is no second build. It targets only a candidate when `prepare-attestation` is enabled, otherwise the requested tags. Disabling both scanning and attestation preparation retains the existing direct Buildx push path.
- Setting `security-scan-fail-on-critical: "false"` permits Critical findings, but scanner errors, missing platforms, and digest mismatches still fail the action and prevent publication.
- The scan path requires a Linux runner with Bash, jq, and Docker. Syft, Grype, and Skopeo run in digest-pinned containers configured in `action.yml`; no Python or host installation of these tools is needed. The bundled script is resolved through `$GITHUB_ACTION_PATH`, so callers do not need it in their own checkout.
