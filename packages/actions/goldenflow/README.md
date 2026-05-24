# GoldenFlow Action

Run [GoldenFlow](https://github.com/benseverndev-oss/goldenmatch) data
transformations on your data files in CI, report what changed, and post a PR
comment. Companion to the GoldenCheck action.

## Usage

```yaml
- uses: benseverndev-oss/goldenmatch/packages/actions/goldenflow@main
  with:
    files: "data/*.csv"
    config: goldenflow.yml   # optional; zero-config when omitted
    strict: "false"          # set "true" to fail on transform errors
```

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `files` | (required) | Glob pattern for data files to transform |
| `config` | `""` | Path to a goldenflow YAML config |
| `domain` | `""` | Domain pack (e.g. `people_hr`, `healthcare`) |
| `strict` | `false` | Fail the check if any transform errors occur |
| `python-version` | `3.12` | Python version |
| `version` | latest | GoldenFlow version to install |

## Outputs

| Output | Description |
|--------|-------------|
| `transforms-applied` | Total transforms applied across all files |
| `files-processed` | Number of files transformed |
| `errors` | Total transform errors |

On pull requests the action posts (and updates) a comment summarizing the
transforms applied per file.
