# Resource Push to NGC (Composite Action)

This composite action mirrors the existing GitLab component that publishes artifacts into the NVIDIA GPU Cloud (NGC) registry. It performs validation, installs the NGC CLI when needed, and uploads or updates resources and versions.

## Usage

```yaml
name: Publish model to NGC
on:
  workflow_dispatch:

jobs:
  publish:
    runs-on: linux-amd64-cpu4
    steps:
      - uses: actions/checkout@v4
      - name: Upload resource
        uses: NVIDIA/dsx-github-actions/.github/actions/resource-push-ngc@main
        with:
          name: my-model
          display-name: My Model
          description: Example resource upload
          version: "1.0.0"
          path: ./build/model.trt
          application: OBJECT_DETECTION
          framework: TensorRT
          format: engine
          precision: FP16
          ngc-path: myorg/myteam
          ngc-key: ${{ secrets.NGC_API_KEY }}
          ngc-force: overwrite
          ngccli-version: 4.9.17
```

The default NGC CLI version has NVIDIA-published checksums built into the
action for both AMD64 and ARM64. When overriding `ngccli-version`, also pass
the matching `ngccli-sha256`; installation fails closed when no trusted
checksum is available.

## Inputs

| Name             | Required | Default             | Description                                                      |
| ---------------- | -------- | ------------------- | ---------------------------------------------------------------- |
| `job-name`       | No       | `resource-push-ngc` | Compatibility job name tag                                       |
| `application`    | No       | `OTHER`             | Resource application category                                    |
| `description`    | No       | `""`                | Short description of the resource                                |
| `display-name`   | No       | `""`                | Display name, defaults to `name`                                 |
| `format`         | No       | `generic`           | Resource format                                                  |
| `framework`      | No       | `Other`             | Framework label                                                  |
| `name`           | Yes      | —                   | Resource name                                                    |
| `ngc-force`      | No       | `skip`              | Behavior when the version exists: `overwrite`, `skip`, or `fail` |
| `ngc-key`        | Yes      | —                   | NGC API key (store in secrets)                                   |
| `ngc-path`       | Yes      | —                   | Target NGC `org/team` path                                       |
| `path`           | Yes      | —                   | File or directory to upload                                      |
| `precision`      | No       | `OTHER`             | Resource precision                                               |
| `version`        | Yes      | —                   | Resource version                                                 |
| `ngccli-version` | No       | `4.9.17`            | NGC CLI version to install                                       |
| `ngccli-sha256`  | No       | empty               | Expected archive SHA256; required for non-default CLI versions   |

## Outputs

| Name            | Description                                           |
| --------------- | ----------------------------------------------------- |
| `resource-id`   | Fully qualified identifier (`org/team/name:version`). |
| `upload-status` | `created`, `updated`, `overwritten`, or `skipped`.    |

## Environment Overrides

For compatibility with the GitLab component, you may override selected inputs using environment variables before invoking the action:

- `RESOURCE_NAME`, `RESOURCE_DESCRIPTION`, `RESOURCE_VERSION`, `RESOURCE_FORMAT`, `RESOURCE_APPLICATION`, `RESOURCE_FRAMEWORK`, `RESOURCE_PRECISION`, `RESOURCE_PATH`, `RESOURCE_NGC_FORCE`

## Exit Codes

- Standard success returns exit code `0`.
- When `ngc-force=skip` and the version exists, the internal script exits with `66`, but the composite step captures it and reports success with `upload-status=skipped`.
