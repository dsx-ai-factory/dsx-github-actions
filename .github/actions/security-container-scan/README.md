# Security Container Scan Action

A composite GitHub Action that generates an SBOM (via Syft) and scans a locally-built container image for known vulnerabilities (via Anchore Grype). It produces JSON and SARIF reports, writes a human-friendly summary, and can optionally upload the SARIF to the GitHub code scanning Security tab.

## Features

- ✅ SBOM generation with `anchore/sbom-action` (Syft, SPDX-JSON by default; SHA-pinned for GHE allowlist)
- ✅ Vulnerability scan with Anchore Grype (JSON + SARIF outputs)
- ✅ Top-N CVE summary written to `$GITHUB_STEP_SUMMARY`
- ✅ Reports uploaded as a workflow artifact
- ✅ Optional SARIF upload to GitHub code scanning with per-image categories
- ✅ Non-fatal by default — findings surface without blocking the build unless opted in

## Prerequisites

### Docker daemon on the runner

The action scans a local image by shelling out to `docker image inspect` and running Grype in a container with the host Docker socket mounted. The image you want to scan must already exist in the runner's Docker daemon (build with `load: true` or `docker pull` before calling this action).

### GitHub Advanced Security (only when `upload-sarif: true`)

Uploading SARIF to the Security tab uses the standard GitHub code scanning pipeline, which requires **GitHub Advanced Security (GHAS)** on private repositories:

- ✅ **Public repositories**: GHAS is free and available
- ⚠️ **Private repositories**: GHAS requires a paid license

Upload failures (including `422 Advanced Security must be enabled for this repository`) are wrapped in `continue-on-error: true`, so they do not fail the job. If GHAS is not available, leave `upload-sarif` at its default of `false` and rely on the artifact + job summary for findings.

The caller job must also grant `security-events: write` permission when `upload-sarif: true`.

## Usage

### Basic — scan a locally built image

```yaml
jobs:
  build-and-scan:
    runs-on: linux-amd64-cpu4
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
        with:
          buildkitd-config: /etc/buildkit/buildkitd.toml

      - name: Build image locally
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64
          push: false
          load: true
          tags: localbuild/myapp:${{ github.run_id }}

      - name: Grype scan
        uses: NVIDIA/dsx-github-actions/.github/actions/security-container-scan@main
        with:
          image: localbuild/myapp:${{ github.run_id }}
          fail-on: high
          fail-build: "false"
```

### With SARIF upload to GitHub Security tab

```yaml
jobs:
  scan:
    runs-on: linux-amd64-cpu4
    permissions:
      contents: read
      security-events: write   # required for SARIF upload
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
        with:
          buildkitd-config: /etc/buildkit/buildkitd.toml

      - name: Build image locally
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64
          push: false
          load: true
          tags: localbuild/myapp:${{ github.run_id }}

      - name: Grype scan + SARIF upload
        uses: NVIDIA/dsx-github-actions/.github/actions/security-container-scan@main
        with:
          image: localbuild/myapp:${{ github.run_id }}
          fail-on: critical
          fail-build: "false"
          upload-sarif: "true"
          sarif-category: grype-myapp
```

### Scanning multiple images in the same run (matrix)

When scanning several images in one workflow run, always pass a unique `sarif-category` per image — otherwise later uploads replace earlier ones in the Security tab.

```yaml
strategy:
  fail-fast: false
  matrix:
    service: [api, worker, gateway]
steps:
  - uses: NVIDIA/dsx-github-actions/.github/actions/security-container-scan@main
    with:
      image: localbuild/${{ matrix.service }}:${{ github.run_id }}
      upload-sarif: "true"
      sarif-category: grype-${{ matrix.service }}
```

## Inputs

| Input                | Description                                                                                                                   | Required | Default                      |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------- | ---------------------------- |
| `image`              | Local container image reference to scan (must exist on the runner).                                                           | Yes      | —                            |
| `fail-on`            | Minimum Grype severity that counts as a failure. One of `negligible`, `low`, `medium`, `high`, `critical`.                    | No       | `high`                       |
| `fail-build`         | If `true`, fail the step when Grype finds vulnerabilities at/above `fail-on` or when SBOM/scan prerequisites fail.            | No       | `false`                      |
| `grype-image`        | Grype container image to use for scanning. Update the version and multi-architecture digest together.                         | No       | `anchore/grype:v0.117.0@sha256:ddf9e9f204049f3a4a0955ef70873cabab6a31432125ad4f20a490b54950a253` |
| `report-json`        | Filename for the JSON report.                                                                                                 | No       | `grype-results.json`         |
| `report-sarif`       | Filename for the SARIF report.                                                                                                | No       | `grype-results.sarif`        |
| `report-table`       | Filename for the human-readable table report (always generated).                                                              | No       | `grype-results.txt`          |
| `upload-artifact`    | Upload reports as a workflow artifact.                                                                                        | No       | `true`                       |
| `artifact-name`      | Artifact name for uploaded reports.                                                                                           | No       | `grype-container-scan`       |
| `generate-sbom`      | Generate and upload an SBOM via `anchore/sbom-action`.                                                                        | No       | `true`                       |
| `sbom-format`        | SBOM format for `sbom-action` (e.g. `spdx-json`, `cyclonedx-json`).                                                           | No       | `spdx-json`                  |
| `sbom-artifact-name` | Artifact name for the SBOM uploaded by `sbom-action`.                                                                         | No       | `container.spdx.json`        |
| `write-summary`      | Write a human-friendly summary into `$GITHUB_STEP_SUMMARY`.                                                                   | No       | `true`                       |
| `upload-sarif`       | Upload SARIF to GitHub code scanning. Requires `security-events: write` and, for private repos, GHAS. Failures are non-fatal. | No       | `false`                      |
| `sarif-category`     | Category for SARIF upload. Must be unique per image in multi-image runs. Defaults to `grype-<sanitized-image-ref>`.           | No       | `""` (auto-derived)          |

## Outputs

| Output         | Description                                                                                                 |
| -------------- | ----------------------------------------------------------------------------------------------------------- |
| `status`       | One of `ok`, `high_or_error`, `image_not_found`, `pull_failed`, `sbom_failed`, `grype_unknown`.             |
| `detail`       | Free-form detail string corresponding to `status`.                                                          |
| `report_json`  | Path to the JSON report (if generated).                                                                     |
| `report_sarif` | Path to the SARIF report (if generated).                                                                    |
| `report_table` | Path to the table report (always generated).                                                                |

## Notes

- **Step summary is count-only**: the `$GITHUB_STEP_SUMMARY` output shows total matches and Critical/High/Medium/Low counts, but does **not** list individual CVE IDs or affected packages. On public repositories, run summaries are world-readable, and publishing a list of unresolved CVEs + package versions amounts to handing attackers a roadmap. Per-CVE detail is available in the JSON/SARIF/table artifact (collaborators only) or, when `upload-sarif: true`, in the Security tab.
- **Three artifact formats**: each scan produces JSON (Grype-native, used by tooling and `jq` drill-down), SARIF (GitHub code scanning / IDE viewers), and a plain-text table (drop-in readable for reviewers who don't want to touch `jq`). All three are bundled into the same workflow artifact.
- **Supply chain**: `grype-image` defaults to a versioned, multi-architecture digest. Refresh the version and digest periodically; Grype's vulnerability database still updates at runtime.
- **SBOM generation**: enabled by default; set `generate-sbom: "false"` to skip when you only need the vulnerability scan.
- **Multi-arch**: Grype scans the image variant that is loaded into the local Docker daemon. When the runner is `linux/amd64` and you need to scan `linux/arm64`, pull with `--platform linux/arm64` first.
- **GHAS-free fallback**: leave `upload-sarif` at `false`. Findings still appear in the workflow artifact and job summary; the job does not fail.
