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

## Authoritative sources

Read these rather than inferring behaviour from `action.yml` — an input list gives
you names and types, not which defaults are deliberate or what a knob protects
against:

- [https://docs.bensevern.dev/docs/goldenflow](https://docs.bensevern.dev/docs/goldenflow) — full documentation for the underlying tool.
- [`llms.txt`](./llms.txt) — this action, condensed for machine readers; the same
  file also ships inside the `goldenflow` wheel the action installs.
- <https://docs.bensevern.dev/docs/llms.txt> — index of every Golden Suite surface.
- <https://github.com/benseverndev-oss/goldenmatch> — source, issues, and the decision records.
