# Helm Unit Tests Action

Run [helm-unittest](https://github.com/helm-unittest/helm-unittest) against Helm charts in your repository.

This action installs Helm, the helm-unittest plugin from an OCI registry, and runs unit tests for specified charts or all charts found in the repository.

> **Note**: This action is based on [d3adb5/helm-unittest-action](https://github.com/d3adb5/helm-unittest-action) (MIT License), adopted for NVIDIA enterprise use where all dependencies must come from enterprise-owned infrastructure.

## Usage

### Basic Usage

```yaml
name: Helm Tests

on: [push, pull_request]

jobs:
  unittest:
    runs-on: linux-amd64-cpu4
    steps:
      - uses: actions/checkout@v4
      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-unittest@main
        with:
          plugin-oci-ref: "oci://nvcr.io/ORG/REPO/helm-unittest:1.1.2"
          registry-password: ${{ secrets.NGC_CLI_API_KEY }}
          charts: charts/my-app
```

### Test Specific Charts

```yaml
- uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-unittest@main
  with:
    plugin-oci-ref: "oci://nvcr.io/ORG/REPO/helm-unittest:1.1.2"
    registry-password: ${{ secrets.NGC_CLI_API_KEY }}
    charts: >-
      charts/my-app
      charts/my-lib
```

### Custom Flags

```yaml
- uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-unittest@main
  with:
    plugin-oci-ref: "oci://nvcr.io/ORG/REPO/helm-unittest:1.1.2"
    registry-password: ${{ secrets.NGC_CLI_API_KEY }}
    flags: "--color --output-type JUnit --output-file test-results.xml"
```

### Combined with Helm Lint

```yaml
jobs:
  helm:
    runs-on: linux-amd64-cpu4
    steps:
      - uses: actions/checkout@v4

      - uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-unittest@main
        with:
          plugin-oci-ref: "oci://nvcr.io/ORG/REPO/helm-unittest:1.1.2"
          registry-password: ${{ secrets.NGC_CLI_API_KEY }}
          helm-version: v3.14.0

      # Helm is now available for additional commands
      - run: helm lint charts/my-app
```

## Inputs

| Input               | Description                                                                                   | Required | Default          |
|:--------------------|:----------------------------------------------------------------------------------------------|:---------|:-----------------|
| `plugin-oci-ref`    | OCI reference for helm-unittest plugin (e.g., `oci://nvcr.io/ORG/REPO/helm-unittest:1.1.2`)   | **Yes**  | -                |
| `registry-password` | Password or token for OCI registry authentication                                             | **Yes**  | -                |
| `charts`            | Paths to the charts to be tested, separated by spaces or newlines. Paths must not contain spaces. | **Yes**  | -                |
| `registry-host`     | OCI registry hostname for authentication                                                      | No       | `nvcr.io`        |
| `registry-username` | Username for OCI registry authentication                                                      | No       | `$oauthtoken`    |
| `helm-version`      | Which version of Helm to install                                                              | No       | `v3.13.2`        |
| `flags`             | Which flags to pass to helm-unittest when running unit tests                                  | No       | `--color`        |
| `install-mode`      | One of `"force"`, `"if-not-present"`, or `""`. See below.                                     | No       | `if-not-present` |

### plugin-oci-ref

The plugin is installed from an OCI registry, giving you full supply chain control. The artifact lives in NVIDIA-owned infrastructure.

To mirror the plugin to NGC:

```bash
# Copy from upstream to NGC (requires skopeo or crane)
skopeo copy \
  docker://ghcr.io/helm-unittest/helm-unittest/helm-unittest:1.1.2 \
  docker://nvcr.io/ORG/REPO/helm-unittest:1.1.2
```

### install-mode

| Value            | Behavior                                                                    |
|:-----------------|:----------------------------------------------------------------------------|
| `force`          | Uninstalls any existing plugin before installing the specified version.     |
| `if-not-present` | Skips installation if a plugin called `unittest` is already installed.      |
| _empty_          | Attempts normal install. Fails if the plugin is already installed.          |

## Writing Helm Unit Tests

For examples and assertion types, see the [helm-unittest documentation](https://github.com/helm-unittest/helm-unittest).

## License

Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.

Licensed under the Apache License, Version 2.0. See [LICENSE](../../../LICENSE) for details.

This action includes code derived from [d3adb5/helm-unittest-action](https://github.com/d3adb5/helm-unittest-action), which is licensed under the MIT License. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for details.
