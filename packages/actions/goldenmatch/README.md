# GoldenMatch Action

Deduplicate data files in CI with [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch),
report cluster/duplicate counts, gate on a duplicate threshold, and post a PR
comment. Companion to the GoldenCheck and GoldenFlow actions.

## Usage

```yaml
# With explicit match keys:
- uses: benseverndev-oss/goldenmatch/packages/actions/goldenmatch@main
  with:
    files: "data/*.csv"
    exact: "email"
    fuzzy: "name:0.85,address:0.8"
    max-duplicates: "0"     # fail if any duplicate rows are found

# Or with a config file:
- uses: benseverndev-oss/goldenmatch/packages/actions/goldenmatch@main
  with:
    files: "data/*.csv"
    config: goldenmatch.yml
```

> A `config`, `exact`, or `fuzzy` input is **required** — zero-config dedupe is
> disabled in the action so CI never reaches the network for cross-encoder
> rerank (the local controller can enable it on 3+ field weighted matchkeys).

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `files` | (required) | Glob pattern for data files to deduplicate |
| `config` | `""` | Path to a goldenmatch YAML config (recommended) |
| `exact` | `""` | Comma-separated exact-match key columns |
| `fuzzy` | `""` | Comma-separated `field:threshold` fuzzy keys |
| `max-duplicates` | `-1` | Fail if duplicate rows exceed this (`-1` disables the gate) |
| `python-version` | `3.12` | Python version |
| `version` | latest | GoldenMatch version to install |

## Outputs

| Output | Description |
|--------|-------------|
| `clusters` | Total clusters across all files |
| `duplicates` | Total duplicate rows across all files |
| `files-processed` | Number of files deduped |

On pull requests the action posts (and updates) a comment summarizing the
clusters and duplicates found per file.
