# Helm Unit Tests Action

Run [helm-unittest](https://github.com/helm-unittest/helm-unittest) against Helm charts in your repository.

This action installs Helm 4, downloads the helm-unittest plugin from an OCI registry, and runs unit tests for the specified charts.

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
          plugin-oci-ref: "oci://nvcr.io/ORG/TEAM/unittest:1.1.2"
          registry-password: ${{ secrets.NGC_CLI_API_KEY }}
          charts: charts/my-app
```

### Test Multiple Charts

```yaml
- uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-unittest@main
  with:
    plugin-oci-ref: "oci://nvcr.io/ORG/TEAM/unittest:1.1.2"
    registry-password: ${{ secrets.NGC_CLI_API_KEY }}
    charts: >-
      charts/my-app
      charts/my-lib
```

### Custom Flags

```yaml
- uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-unittest@main
  with:
    plugin-oci-ref: "oci://nvcr.io/ORG/TEAM/unittest:1.1.2"
    registry-password: ${{ secrets.NGC_CLI_API_KEY }}
    charts: charts/my-app
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
          plugin-oci-ref: "oci://nvcr.io/ORG/TEAM/unittest:1.1.2"
          registry-password: ${{ secrets.NGC_CLI_API_KEY }}
          charts: charts/my-app

      # Helm is now available for additional commands
      - run: helm lint charts/my-app
```

## Inputs

| Input               | Description                                                                                   | Required | Default          |
| ------------------- | --------------------------------------------------------------------------------------------- | -------- | ---------------- |
| `plugin-oci-ref`    | OCI reference for helm-unittest plugin (e.g., `oci://nvcr.io/ORG/TEAM/unittest:1.1.2`)        | **Yes**  | -                |
| `registry-password` | Password or token for OCI registry authentication                                             | **Yes**  | -                |
| `charts`            | Charts to run tests for, separated by spaces or newlines. Paths must not contain spaces.      | **Yes**  | -                |
| `registry-username` | Username for OCI registry authentication                                                      | No       | `$oauthtoken`    |
| `helm-version`      | Which version of Helm to install (4.2.1-4.2.4 have [oras-go bug](https://github.com/helm/helm/issues/32247)) | No | `v4.2.0` |
| `flags`             | Which flags to pass to helm-unittest when running unit tests                                  | No       | `--color`        |
| `install-mode`      | One of `"force"`, `"if-not-present"`, or `""`. See below.                                     | No       | `if-not-present` |

### plugin-oci-ref

The plugin is downloaded from an OCI registry using Helm 4's native OCI support, giving you full supply chain control.

**Setup**: Mirror the plugin to your NGC registry using ORAS:

```bash
# Login to NGC
oras login nvcr.io --username '$oauthtoken' --password "$NGC_CLI_API_KEY"

# Copy from upstream to NGC
oras copy \
  ghcr.io/helm-unittest/helm-unittest/unittest:1.1.2 \
  nvcr.io/ORG/TEAM/unittest:1.1.2
```

Repeat this when upgrading to a new plugin version.

### install-mode

| Value             | Behavior                                                 |
| ----------------- | -------------------------------------------------------- |
| `if-not-present`  | Skip install if plugin already exists (default)          |
| `force`           | Uninstall existing plugin first, then install            |
| `""`              | Fail if plugin already exists                            |

## Writing Helm Unit Tests

See the [helm-unittest documentation](https://github.com/helm-unittest/helm-unittest#usage) for how to write tests.

Example test file (`charts/my-app/tests/deployment_test.yaml`):

```yaml
suite: deployment tests
templates:
  - deployment.yaml
tests:
  - it: should create a deployment
    asserts:
      - isKind:
          of: Deployment
      - equal:
          path: metadata.name
          value: RELEASE-NAME-my-app
```
