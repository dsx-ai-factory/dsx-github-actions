# Helm Package and Push

Packages a Helm Chart and pushes it to NGC.

## Usage

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Package and Push
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-package-push@main
    with:
      chart-path: ./charts/my-chart
      ngc-key: ${{ secrets.NGC_API_KEY }}
      ngc-path: myorg/myteam
      retries: 1
```

## Inputs

| Input | Description | Required | Default |
| --- | --- | --- | --- |
| `chart-version` | Set the version of the Helm Chart | No | (from Chart.yaml) |
| `chart-version-suffix` | Set the version suffix | No | "" |
| `app-version` | Set the appVersion of the Helm Chart | No | (from Chart.yaml) |
| `chart-path` | Root directory for the Helm Chart | No | . |
| `lint` | Whether to lint | No | false |
| `lint-values` | JSON mapping of values used only by lint, never merged into the packaged chart | No | {} |
| `extra-repos` | Extra repositories | No | [] |
| `ngc-push` | Enable pushing to NGC | No | true |
| `ngc-key` | NGC API Key | No | |
| `ngc-path` | NGC Org/Team path | No | |
| `ngc-duplicate` | Action for duplicate versions: `skip`, `overwrite`, `fail`, or `verify` | No | skip |
| `retries` | Additional publish attempts after failure | No | 0 |
| `ngc-registry` | NGC Registry URL | No | https://helm.ngc.nvidia.com/ |

Use `lint: true` with `lint-values: '{"endpoint":"https://example.invalid"}'` when chart validation requires configuration.
Use non-secret values. The temporary values file is removed after lint succeeds or fails.
Set `ngc-push: false` to lint and package without registry authentication or publication.

See [duplicate version policies](../helm-push-ngc/README.md#duplicate-versions)
for `skip`, `fail`, `overwrite`, and `verify`. The opt-in `verify` policy requires
`python3` and reuses an existing version only after comparing its downloaded
contents with the packaged chart. Verified reuse sets `push-skipped` to `true`.

`retries` is the number of retries after a failed publish (default: `0`).
Retries wait 5 seconds, then add 5 seconds to the delay for each retry. Setup and packaging run once.
With `ngc-duplicate: verify`, each failed upload also runs up to six readback checks,
10 seconds apart, before the publish retry delay starts. These checks can add
50 seconds per failed upload.

## Outputs

| Output | Description |
| --- | --- |
| `chart-name` | The name of the chart |
| `chart-version` | The version of the chart |
| `push-skipped` | Whether the push was skipped |
