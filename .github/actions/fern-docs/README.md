# Fern documentation workflows

The shared workflows validate docs, collect PR pages, publish previews, and
publish releases. Callers own event triggers and path filters. Pin every
caller to the same reviewed full commit SHA.

| Workflow | Purpose | Required permissions |
| --- | --- | --- |
| `fern-docs-ci.yml` | MDX, path, Fern configuration and link checks | `contents: read` |
| `fern-docs-preview-build.yml` | Collect pages from a vetted `pull-request/N` push | `contents: read` |
| `fern-docs-preview-comment.yml` | Publish a preview from the collector artifact | `contents: read`, `actions: read`, `pull-requests: write` |
| `publish-fern-docs.yml` | Validate a release, optionally publish and register it | `contents: write`, `pull-requests: write` |

All four use `workflow_call` only. The composite action in this directory
prepares and validates docs without publishing. Runners need Bash, Python 3.12+
and Git. The action installs Node.js and Fern CLI. The workflows and composite
action default to Fern `5.186.0`. Callers can override it with `fern_cli_version`
(or `fern-cli-version` when using the composite directly). The selected version
must be exact; `latest` is rejected. With the default layout, it must match
`fern/fern.config.json`.
`docs_directory`, `docs_paths`, and Lychee arguments remain
relative to the caller root even when `fern_working_directory` is set.

## Site configuration path

Set these inputs on the CI, preview comment, and publish workflows to keep
site configuration in `.github/fern-docs.yml`:

```yaml
with:
  fern_docs_path: .github/fern-docs.yml
  fern_organization: nvidia
  fern_cli_version: '5.65.2'
```

`fern_docs_path` is relative to the caller checkout. The action copies that
file to `fern/docs.yml` and generates `fern/fern.config.json` from the organization
and CLI version inputs. The organization defaults to `nvidia`. The composite
action uses `fern-docs-path` and `fern-organization` for these inputs.
An empty path preserves the existing `fern/` layout.
Custom configuration and output paths accept ASCII letters, numbers, `.`, `_`,
`-`, and `/`. Symbolic links are rejected.

Paths inside the YAML are resolved from the generated `fern/docs.yml`, including
when the source YAML is elsewhere. Moving `fern/docs.yml` to
`.github/fern-docs.yml` preserves `../docs/` and `../index.yml` references.
Keep configuration outside the pages directory overlaid by previews.
The preview collector needs only the page layout inputs. Include the new
configuration path in the caller's CI and preview path filters.

Version registration keeps `versions/v*.yml` beside the source YAML. With this
example, registry PRs update `.github/fern-docs.yml` and `.github/versions/`.
The action copies registry entries to `fern/versions/` and rebuilds their content
from release tags. Add `/fern/` to the caller's `.gitignore` when using this layout.

## Identity validation

Preserve Identity's push triggers on `master` and `pull-request/[0-9]+`, manual
dispatch, and caller concurrency. Its documentation is spread across directories
and listed in root `index.yml`:

```yaml
jobs:
  fern-check:
    uses: dsx-ai-factory/dsx-github-actions/.github/workflows/fern-docs-ci.yml@REVIEWED_FULL_COMMIT_SHA
    with:
      runner: ubuntu-24.04
      navigation_file: index.yml
      docs_paths: |
        docs
        examples
        cmd
        charts
        kata-uds-proxy
      frozen_versions: true
      native_checks: true
```

`native_checks` runs `fern docs md check` and `fern docs broken-links` instead of
Lychee. Frozen pages are extracted from release tags before validation. Fetching
full history and tags is part of the workflow.

For a directory layout such as Exchange, leave `navigation_file` empty and use
`docs_directory: docs`. Select its reviewed CLI version before adoption.
`docs_paths` accepts one path per line and defaults to
`docs_directory`. The original Lychee `link_args` input remains available.
Fern config, multi-source settings, product URLs, and slugs stay in the caller.

## Preview handoff

Keep two thin caller workflows:

1. A push workflow for vetted `pull-request/[0-9]+` branches calls
   `fern-docs-preview-build.yml`. Include `index.yml`, published Markdown paths,
   Fern config, and caller workflows in its path filters.
2. A `workflow_run` workflow, listening to that caller workflow's **name**, calls
   `fern-docs-preview-comment.yml` with the same layout inputs and explicit
   `secrets: { FERN_TOKEN: "${{ secrets.FERN_TOKEN }}" }`. Exchange can map
   `DOCS_FERN_TOKEN` to the same shared secret name.

The collector has no Fern token. The preview checks the producer repository,
successful run, bot branch number, and current PR head SHA. It reads shared tools
from the called workflow SHA and Fern configuration from the caller's default
branch, then overlays only staged pages. PR edits to trusted configuration or
tools are intentionally not previewed before merge. Copy-pr-bot vetting remains
required: the path checker is defense in depth, not an MDX sandbox.

For directory layouts such as Exchange, the staged PR docs directory replaces
the default-branch docs directory, including deletions and renames. Navigation
in the configured site YAML still comes from the default branch: PR navigation edits are
not previewed. If that navigation references a deleted PR page, validation fails.
Confirm this limitation with the caller's owners before migration.

Previews use `shared-fern-rREPOSITORY_ID-pr-N` by default. The numeric GitHub
ID identifies the repository running the workflow, so independent runs in a
fork and upstream have separate previews. Set a distinct `preview_namespace`
and `post_comment: false` for an isolated preview test.
Namespaces use lowercase letters, digits, and single hyphens between parts.
Before publication, the workflow rejects an `ORG-preview-ID` label longer than
62 characters, the truncation limit in Fern CLI 5.186.0. Shorten the namespace
if necessary. Reusing the same namespace/repository/PR tuple updates that
preview. The shared comment marker is `<!-- shared-fern-preview -->`.
See [Fern's preview behavior](https://buildwithfern.com/learn/docs/preview-publish/preview-changes).

The caller's `workflow_run` listener must exist on its default branch. A new
feature branch alone cannot prove that handoff. Replace each old workflow in the
same migration PR that enables its shared replacement; do not run two publishers.

## Release publication

`publish-fern-docs.yml` defaults to `publish: false`: it resolves the tag, prepares
and checks the site, and makes no Fern publication or registry PR. Omit
`FERN_TOKEN` during these tests. An ordinary feature-branch push has no publishing
trigger in the shared repository.

The shared publisher owns release concurrency with group `shared-fern-publish`,
`queue: max`, and `cancel-in-progress: false`. Thin publish callers must omit
concurrency at both workflow and calling-job level. A caller-level group can
cancel active or pending releases before the shared queue can process them.
This rule does not change validation workflow concurrency.

Real publishing requires `publish: true`, `FERN_TOKEN`, and a release/tag event
or manual dispatch on the caller's default branch. A manual dispatch from a
feature branch is rejected. This gate does not protect against callers changing
their own workflow code; retain normal branch and runner protections.
Automatic stable publication requires a tag reachable from the caller's default
branch. Automatic RC publication requires its matching release branch, as
described below.

A manual dispatch with an explicit `tag` accepts `vMAJOR.MINOR.PATCH` with an
optional pre-release or build suffix, such as `v1.16.0-rc.5`. The tag must be
reachable from the default branch or `origin/release/MAJOR.MINOR.PATCH`.
Dispatch from the default branch and forward the caller's tag input to the shared
workflow. This works with either value of `register_versions` and does not require
`publish_rc`. An empty tag input keeps the automatic release rules.

For Identity, keep `register_versions: true`, `navigation_file: index.yml`, and
its MDX paths. Registration uses the tag's navigation, retains the selected tag
and the newest other versions up to `max_versions` (default 3), and opens
`docs/fern-register-TAG` after publication.
Merge the preceding registry PR before publishing another version. A tag without
`index.yml` is rejected. Version and source ref remain the same; docs-only source
overrides are not implemented.

For Exchange, use `register_versions: false`, empty `navigation_file`, and
`native_checks: false` to validate/publish the release tree without a registry PR.
Set `publish_rc: true` to accept `vMAJOR.MINOR.PATCH-rc.N` tags during automatic
publication. This requires `register_versions: false` and a tag reachable from
`origin/release/MAJOR.MINOR.PATCH`. Automatic publication defaults to stable
releases and skips other pre-release formats. Protect release branches and tags.

RC publication updates the same site configured in the release's site YAML.
It does not create a separate preview site. Selecting an older tag replaces newer
content on that site. The release tree must contain the selected configuration
path. With the default layout, `fern/fern.config.json` must match the CLI version.
Backport caller and config changes to existing release branches before cutting a new RC there.

## Verification

`Fern contracts` exercises the real helpers against temporary Git repositories:
accepted and rejected paths, pages outside `docs/`, trusted overlays, frozen
content and pinned links, registration ordering/pruning, and publication event
guards. For the real Identity integration check, run its existing manually
triggered validation workflow from an isolated feature branch, calling the
reviewed shared SHA with no secrets. Identity is internal, so a default token
from this shared repository cannot check it out. These checks do not exercise
a live preview or production publish.

The staging, path, and frozen-content helpers originate from
[Identity](https://github.com/dsx-ai-factory/nvidia-workload-identity/tree/cebaa6ae31dd3b891a2428ad7de554f1b8424c2e/tools).
