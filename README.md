# DSX GitHub Actions

A collection of reusable GitHub Actions for standardizing CI/CD workflows across NVIDIA projects.

## 🚀 Available Actions

| Action                                                  | Description                       | Use Case                          |
| ------------------------------------------------------- | --------------------------------- | --------------------------------- |
| [codeql-scan](.github/actions/codeql-scan/)             | Static code analysis with CodeQL  | Security vulnerability detection  |
| [trufflehog-scan](.github/actions/trufflehog-scan/)     | Secret scanning with TruffleHog   | Leaked credentials detection      |
| [security-container-scan](.github/actions/security-container-scan/) | Container vuln scan (SBOM + Grype) | Container image CVE detection     |
| [security-container-scan-aggregate](.github/actions/security-container-scan-aggregate/) | Aggregate multi-image Grype reports into one summary | Per-PR consolidated scan summary + sticky comment |
| [semantic-release](.github/actions/semantic-release/)   | Automated versioning and releases | Semantic versioning and changelog |
| [resolve-release-candidate](.github/actions/resolve-release-candidate/) | Select RC releases and make artifact publishing rerunnable | Prerelease artifact publishing |
| [resource-push-ngc](.github/actions/resource-push-ngc/) | Push resources to NGC             | Artifact publishing               |
| [docker-build](.github/actions/docker-build/)           | Docker Buildx build/push wrapper  | Build/push multi-arch OCI images  |
| [git-tag](.github/actions/git-tag/)                     | Create and push git tag           | Tagging releases                  |
| [slack-notify](.github/actions/slack-notify/)           | Send notifications to Slack       | CI/CD status notifications        |
| [go-lint](.github/actions/go-lint/)                     | Go linting (golangci-lint, fmt, vet) | Go code quality checks         |
| [go-test](.github/actions/go-test/)                     | Go tests with coverage and JUnit  | Go test execution and reporting   |
| [license-headers](.github/actions/license-headers/)     | SPDX license header checks        | License compliance                |
| [commitlint](.github/actions/commitlint/)               | Conventional commit validation    | Commit message enforcement        |
| [helm-unittest](.github/actions/helm-unittest/)         | Run Helm chart unit tests         | Helm chart testing                |

## ♻️ Available Workflows

| Workflow                                                                 | Description                                           | Use Case                                |
| ------------------------------------------------------------------------ | ----------------------------------------------------- | --------------------------------------- |
| [promote-image](.github/workflows/promote-image.yml) | Re-tag and re-publish multi-arch images via `skopeo` | Promote OCI images across registries |
| [docker-build](.github/workflows/docker-build.yml) | Reusable workflow wrapper for Docker build/push | Share Docker build logic across repos |

## ⚠️ Important: GitHub Advanced Security Required

The security scanning actions (`codeql-scan`, `security-container-scan` with `upload-sarif: true`) upload results to GitHub's Code Scanning feature, which **requires GitHub Advanced Security (GHAS)** to be enabled:

- ✅ **Public repositories**: Free and automatically available
- ⚠️ **Private repositories**: Requires GHAS license

Without GHAS enabled, scans will run successfully but uploads will fail. See individual action documentation for workarounds and details:

- [CodeQL Prerequisites](.github/actions/codeql-scan/README.md#️-prerequisites)
- [Security Container Scan Prerequisites](.github/actions/security-container-scan/README.md#prerequisites)

> **Note**: `trivy-scan` has been removed due to a supply chain compromise (March 2026).
> See: https://github.com/aquasecurity/trivy/discussions/10425 — use `security-container-scan` (Anchore Grype) as the replacement.

## 📖 Quick Start

### Enable Release Candidates for Your Component

Follow the [RC onboarding guide](docs/release-candidate-publishing.md) to add
protected release branches, `vX.Y.Z-rc.N` tags and matching NGC images/charts.
It uses **dsx-exchange** as a worked example and includes setup, tag behavior,
first-release checks, safe reruns and the handoff to an SBOM.
Start with [Onboard Your Repository](docs/release-candidate-publishing.md#onboard-your-repository).

### Security Scanning (Rust)

```yaml
name: Security Checks

on: [push, pull_request]

permissions:
  contents: read
  security-events: write

jobs:
  security:
    runs-on: linux-amd64-cpu4
    steps:
      - uses: actions/checkout@v4

      - name: CodeQL Analysis
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@main
        with:
          languages: "rust"
          build-command: "cargo build --workspace"
```

### Security Scanning (Go)

```yaml
- name: CodeQL Analysis
  uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@main
  with:
    languages: "go"
    build-command: "go build ./..."
```

### Image Promotion
```yaml
name: Promote OCI Image

on:
  workflow_dispatch:
    inputs:
      new-tag:
        type: string
        required: true

jobs:
  promote:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/promote-image.yml@main
    with:
      source: nvcr.io/acme/dev/service
      source_tag: faf3d1
      destination: nvcr.io/acme/stg/service
      destination_tag: ${{ github.event.inputs.new-tag }}
    secrets:
      SOURCE_USERNAME: ${{ secrets.NVCR_DEV_USER }}
      SOURCE_PASSWORD: ${{ secrets.NVCR_DEV_TOKEN }}
      DEST_USERNAME: ${{ secrets.NVCR_STG_USER }}
      DEST_PASSWORD: ${{ secrets.NVCR_STG_TOKEN }}
```

This reusable workflow wraps `skopeo copy`, so it copies the entire manifest list (multi-arch) by default, supports tag-to-tag retagging, and also allows pinning a specific digest by supplying the optional `digest` input. Pass GitHub Container Registry (GHCR) or NVIDIA Container Registry (NGC) credentials through the required secrets block to authenticate against different registries, and consume the resulting `${{ needs.promote.outputs.destination_digest }}` output if downstream jobs need the promoted digest.

## 📚 Documentation

- [CodeQL Scan Action](.github/actions/codeql-scan/README.md)
- [TruffleHog Secret Scan Action](.github/actions/trufflehog-scan/README.md)
- [Security Container Scan Action](.github/actions/security-container-scan/README.md)
- [Security Container Scan Aggregate Action](.github/actions/security-container-scan-aggregate/README.md)
- [Semantic Release Action](.github/actions/semantic-release/README.md)
- [Resolve Release Candidate Action](.github/actions/resolve-release-candidate/README.md)
- [Resource Push NGC Action](.github/actions/resource-push-ngc/README.md)
- [Docker Build Action](.github/actions/docker-build/README.md)
- [Slack Notify Action](.github/actions/slack-notify/README.md)
- [Go Lint Action](.github/actions/go-lint/README.md)
- [Go Test Action](.github/actions/go-test/README.md)
- [License Headers Action](.github/actions/license-headers/README.md)
- [Commitlint Action](.github/actions/commitlint/README.md)
- [Helm Unit Tests Action](.github/actions/helm-unittest/README.md)
- [Workflows Guide](.github/workflows/README.md)
- [Release Candidate Artifact Publishing](docs/release-candidate-publishing.md)

## 🎯 Features

- ✅ **Composite Actions**: Lightweight, reusable, and flexible
- ✅ **Multi-language Support**: Go, Rust, Python, JavaScript, C++, Java, C#
- ✅ **Comprehensive Security**: CodeQL and TruffleHog scanning
- ✅ **Secret Detection**: 700+ credential types with verification
- ✅ **Security Integration**: Automatic SARIF upload to GitHub Security tab
- ✅ **PR Comments**: Automated security findings on pull requests
- ✅ **Configurable**: Extensive input parameters for customization
- ✅ **Well-documented**: Comprehensive README for each action
- ✅ **Automatic Versioning**: Semantic releases for release-worthy changes

## 📦 Version Pinning

This repository uses **automatic semantic versioning**. Pushes to `main` are evaluated using [Conventional Commits](https://www.conventionalcommits.org/); a tag is created only when the changes warrant a release.

### Recommended Approaches

#### 1. Pin to Specific Commit SHA (Recommended by NVIDIA Security Guidence)

Maximum stability and security - the target action never changes:

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@55d1e0af17fb4431edaca19fbd5c78fecd29d18a
```

✅ **Best for**: Production, CI/CD pipelines
⚠️ **Note**: Won't receive bug fixes or new features automatically

#### 2. Pin to Specific Version

Maximum stability - version never changes:

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@v1.2.3
```

✅ **Best for**: Production, CI/CD pipelines
⚠️ **Note**: Won't receive bug fixes or new features automatically

#### 3. Pin to Major Version

Get patches and features, avoid breaking changes:

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@v1
```

✅ **Best for**: Most use cases
📦 **Updates**: Automatically gets `v1.x.x` updates
🛡️ **Safety**: Won't update to `v2.0.0` (breaking changes)

#### 4. Use Latest Main

Always use latest code:

```yaml
uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@main
```

⚠️ **Best for**: Development and testing only
⚠️ **Risk**: May include breaking changes

### Semantic Versioning

Version format: `vMAJOR.MINOR.PATCH`

- **MAJOR** (`v2.0.0`): Breaking changes - update your workflows
- **MINOR** (`v1.1.0`): New features - backward compatible
- **PATCH** (`v1.0.1`): Bug fixes - backward compatible

### Finding Available Versions

View all releases: [GitHub Releases](../../releases)

```bash
# List all tags
git ls-remote --tags https://github.com/dsx-ai-factory/dsx-github-actions.git
```

### Automatic Versioning

This repository uses automatic semantic versioning:

- 🤖 **Automated**: Release-worthy changes on `main` create tags automatically
- 📝 **Conventional Commits**: Version bumps based on commit messages
- 📦 **Dual Tags**: Both specific (`v1.2.3`) and major (`v1`) tags are created

**See**: [Release Workflow Documentation](.github/workflows/README.md) for details.

## 🛠️ Usage Examples

### Example 1: Complete Security Pipeline

```yaml
name: Security

on: [push, pull_request]

permissions:
  contents: read
  security-events: write
  pull-requests: write

jobs:
  scan:
    runs-on: linux-amd64-cpu4
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # Required for TruffleHog

      # Secret scanning
      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/trufflehog-scan@55d1e0af17fb4431edaca19fbd5c78fecd29d18a
        with:
          post-pr-comment: "true"

      # Code analysis
      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@55d1e0af17fb4431edaca19fbd5c78fecd29d18a
        with:
          languages: "go"
          post-pr-comment: "true"

```

### Example 2: Separate Jobs for Long Scans

```yaml
jobs:
  codeql:
    runs-on: linux-amd64-cpu4
    timeout-minutes: 360
    permissions:
      security-events: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/codeql-scan@main
        with:
          languages: "rust"
          build-command: "cargo build --workspace"

```

### Example 3: Container Build + Secret Scan

```yaml
jobs:
  build-and-scan:
    runs-on: linux-amd64-cpu4
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      # Scan for secrets in source code
      - name: Secret Scan
        uses: dsx-ai-factory/dsx-github-actions/.github/actions/trufflehog-scan@main
        with:
          post-pr-comment: "true"

      - name: Build Container
        run: docker build -t myapp:${{ github.sha }} .
```

## 🧹 Developer Workflow

This repository ships with a [`pre-commit`](https://pre-commit.com/) configuration to lint YAML, trim whitespace, run ShellCheck on shell scripts, and execute `actionlint` against GitHub workflows before every commit.

1. Install `pre-commit` (pick one)
   - `pipx install pre-commit`
   - `pip install pre-commit`
   - `brew install pre-commit`
2. Run `pre-commit install` at the repository root to enable the git hook.
3. Run `pre-commit run --all-files` once to ensure every workflow and shell script passes ShellCheck/actionlint.

If CI still fails, execute `pre-commit run actionlint --all-files` or `pre-commit run shellcheck --all-files` locally to focus on the failing hook.

## Contributing

1. Create action in `.github/actions/my-action/`
2. Add `action.yml` and `README.md`
3. Test with multiple projects
4. Update this README
5. Create version tag

## 📋 Repository Structure

```text
.github/
├── actions/
│   ├── codeql-scan/              # Static code analysis (CodeQL)
│   ├── trufflehog-scan/          # Secret scanning (TruffleHog)
│   ├── security-container-scan/  # Container vuln scan (SBOM + Grype)
│   ├── security-container-scan-aggregate/  # Multi-image Grype summary + PR comment
│   ├── docker-build/             # Docker build/push wrapper
│   ├── semantic-release/         # Automated versioning and releases
│   ├── resource-push-ngc/        # NGC resources publishing
│   ├── git-tag/                  # Create and push git tag
│   ├── slack-notify/             # Send Slack notifications
│   ├── go-lint/                  # Go linting (golangci-lint, fmt, vet)
│   ├── go-test/                  # Go tests with coverage and JUnit
│   ├── license-headers/          # SPDX license header checks
│   ├── helm-unittest/            # Helm chart unit testing
│   └── commitlint/               # Conventional commit validation
└── workflows/
    ├── release.yml         # Automatic semantic versioning
    ├── promote-image.yml   # Promote image across registries
    ├── docker-build.yml    # Reusable Docker build/push wrapper
    └── README.md           # Workflows documentation

CONTRIBUTING.md             # Contribution guidelines
LICENSE                     # Apache 2.0
SECURITY.md                 # Security policy
README.md                   # This file
```

## 📄 License

Copyright (c) 2025, NVIDIA CORPORATION. All rights reserved.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
