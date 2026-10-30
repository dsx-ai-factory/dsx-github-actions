# Helm Push NGC

Pushes a Helm Chart to NGC.

## Usage

```yaml
steps:
  - uses: actions/checkout@v4

  - name: Push Chart
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/helm-push-ngc@main
    with:
      package-dir: package
      ngc-key: ${{ secrets.NGC_API_KEY }}
      ngc-path: myorg/myteam
      retries: 1
```

## Inputs

| Input | Description | Required | Default |
| --- | --- | --- | --- |
| `package-dir` | Directory of the .tgz artifact | No | package |
| `ngc-key` | NGC API Key | Yes | |
| `ngc-path` | NGC Org/Team path | Yes | |
| `ngc-duplicate` | Action for duplicate versions: `skip`, `overwrite`, `fail`, or `verify` | No | skip |
| `retries` | Additional publish attempts after failure | No | 0 |
| `ngc-registry` | NGC Registry URL | No | https://helm.ngc.nvidia.com/ |

`retries` is the number of retries after a failed publish (default: `0`).
Retries wait 5 seconds, then add 5 seconds to the delay for each retry. Setup runs once.
With `ngc-duplicate: verify`, each failed upload also runs up to six readback checks,
10 seconds apart, before the publish retry delay starts. These checks can add
50 seconds per failed upload.

## Outputs

| Output | Description |
| --- | --- |
| `chart-name` | The name of the chart |
| `chart-version` | The version of the chart |
| `push-skipped` | Whether the push was skipped |

## Duplicate versions

`ngc-duplicate` controls publication when the same chart name and version exist:

| Policy | Behavior |
| --- | --- |
| `skip` (default) | Skip a version reported by the NGC CLI, without comparing its contents. |
| `fail` | Fail if the NGC CLI reports that the version exists. |
| `overwrite` | Remove a version reported by the NGC CLI, then upload the local package. |
| `verify` | Download an existing version and reuse it only when its chart contents match the local package. Never remove an existing version. |

`verify` requires `python3` on the runner, in addition to the tools installed by
this action. It uses a private copy of the configured NGC repository and a fresh
index cache, then searches for the exact
name and version, including prereleases. An index refresh or search error stops
publication; it does not count as an absent version. An indexed version that
cannot be downloaded or compared also fails.

Comparison includes every packaged file's path and contents. It ignores archive
ordering and tar/gzip metadata, and compares each chart's `Chart.lock` as parsed
YAML with only its `generated` timestamp omitted. Dependency declarations, lock
digests, templates, values, and all other file contents must match. Helm must load
both archives successfully. Invalid
archives, duplicate paths, links, and invalid lock documents fail verification.

When the version is absent, `verify` uploads it. If that upload fails, the action
checks for an identical published chart up to six times, waiting ten seconds
between checks. Only a successful download and content comparison recover the
failed upload; an “already exists” error alone is insufficient. Different or
invalid downloaded contents fail immediately. This accommodates another publisher
or an upload whose result became visible after the CLI reported failure.

`push-skipped` is `true` when `skip` finds an existing version or `verify` reuses
one, including reuse after a failed upload. It is `false` after a successful new
upload. Outputs are not set when publication fails.
