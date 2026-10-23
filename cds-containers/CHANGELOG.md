# Changelog

All notable changes to CDS Containers will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Update tools to uv 0.13.5, pip 26.2.2, certifi 2026.10.23, and Bazel 9.3.1 for the CA incident fixes.
- Retain Bazel 6.5.0 compatibility using the distribution-managed Java trust store; replace `bazel8` with `bazel9` as the default.

## [0.1.0] - 2026-10-23

### Changed
- Upgrade both Go development images to Go 1.26.10 with verified official image digests for the upstream CA incident fix.
- Upgrade Delve to v1.27.0; retain golangci-lint's v1 CLI and configuration.
- Build golangci-lint v1.64.8 with pinned x/tools v0.45.0 and existing Staticcheck tooling v0.6.1, verified together on Linux.
- Pin the tools image to Node.js 24.22.1 from the existing signed NodeSource repository.
- Require patched distribution CA packages and regenerate system trust stores in all three images.
- Keep the existing public image names, Go development tools, CGO support, and tools-container feature set.

### Added
- Runtime security smoke tests for installed Go, Node.js, CA packages, and existing tools, plus offline regression tests.

## [0.0.1] - 2026-01-19

### Added
- Initial GitHub version of CDS containers
- `cds-tools` container with Bazel 6.5.0 & 8.4.0, kubectl, helm, terraform, terragrunt, NGC CLI
- `cds-go-dev-1.24-alpine` container for Go development (Alpine-based, minimal size)
- `cds-go-dev-1.24-debian` container for Go development (Debian-based, better compatibility)
- `cds-grafana-backup-tool` container for Grafana backups
- GitHub Actions workflow for building and pushing to GHCR
- Version management via VERSION.md file
- Path-filtered pipeline (only triggers on cds-containers/ changes)
- Comprehensive documentation and usage examples

### Removed
- `nvault` (requires internal URM access, not available on GitHub runners)
- `cds-cli` (requires internal GitLab access, not available on GitHub runners)

### Changed
- Container registry from GitLab to GitHub Container Registry (GHCR)
- Image naming from `cds/cds-containers/*` to `nvidia/dsx-github-actions/cds-*`
- Version tagging from Git tags to VERSION.md file-based versioning
