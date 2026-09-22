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
| `ngc-duplicate` | Action for duplicate versions | No | skip |
| `ngc-registry` | NGC Registry URL | No | https://helm.ngc.nvidia.com/ |

Use `lint: true` with `lint-values: '{"endpoint":"https://example.invalid"}'` when chart validation requires configuration.
Use non-secret values. The temporary values file is removed after lint succeeds or fails.
Set `ngc-push: false` to lint and package without registry authentication or publication.

## Outputs

| Output | Description |
| --- | --- |
| `chart-name` | The name of the chart |
| `chart-version` | The version of the chart |
| `push-skipped` | Whether the push was skipped |
