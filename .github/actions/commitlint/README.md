# Commitlint Action

A GitHub Composite Action that validates commit messages against [Conventional Commits](https://www.conventionalcommits.org/) using `commitlint`.

## Usage

```yaml
steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0  # Required to access commit history
  - name: Lint Commits
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/commitlint@main
```

### With custom config

```yaml
steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0
  - name: Lint Commits
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/commitlint@main
    with:
      config-file: '.commitlintrc.js'
```

### Lint a pull request title

```yaml
steps:
  - uses: actions/checkout@v4
  - name: Lint PR Title
    uses: dsx-ai-factory/dsx-github-actions/.github/actions/commitlint@main
    with:
      pr-title: ${{ github.event.pull_request.title }}
```

## Inputs

| Input | Description | Required | Default |
| :--- | :--- | :--- | :--- |
| `config-file` | Path to commitlint config file. If empty, uses default discovery. | `false` | `''` |
| `pr-title` | Lint this pull request title instead of a commit range. When set, `from` and `to` are ignored. | `false` | `''` |
| `from` | Lint commits starting from this ref (exclusive). | `false` | `''` |
| `to` | Lint commits up to this ref (inclusive). | `false` | `HEAD` |
| `node-version` | Node.js version to use. | `false` | `24.22.1` |

## Behavior

1. **Set up Node.js** using `actions/setup-node`.
2. **Install commitlint** packages (`@commitlint/cli` and `@commitlint/config-conventional`).
3. **Determine commit range** automatically:
   - If `from` input is provided, uses that ref.
   - In PR context: lints commits from the base branch (`origin/$GITHUB_BASE_REF`).
   - In push context: lints only the latest commit (`HEAD~1..HEAD`).
   - On initial commits or shallow clones: lints `HEAD` only.
4. **Run commitlint** with verbose output. If `pr-title` is set, the title is linted on standard input and the commit range is not used.

## Notes

- In range mode the calling workflow must use `actions/checkout` with `fetch-depth: 0` (or at least enough depth to cover all commits in the PR) for commitlint to access the full commit range. Linting a `pr-title` needs no history, only a checkout so commitlint can discover its config.
- npm packages are installed with `--ignore-scripts` for supply chain safety.
- The default toolchain is pinned to [Node.js `24.22.1`](https://nodejs.org/en/blog/release/v24.22.1), the minimum patched 24.x release for this remediation. The `node-version` override remains available; callers are responsible for selecting a patched release when overriding it.
- Automatic npm caching is disabled because this action installs without a lockfile and does not need the caller's package cache.
- This toolchain pin controls `node` and `npm` on `PATH`, not the Node.js binary bundled with the Actions runner. Updating action SHAs does not patch that binary or its CA bundle; runner updates remain a separate requirement.
