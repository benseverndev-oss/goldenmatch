# GoldenCheck Action

GitHub Action for [GoldenCheck](https://github.com/benseverndev-oss/goldencheck) — data validation that discovers rules from your data.

Scans data files in CI, posts PR comments with findings, and provides pass/fail status checks.

## Usage

```yaml
- uses: benseverndev-oss/goldencheck-action@v1
  with:
    files: "data/*.csv"
```

### With options

```yaml
- uses: benseverndev-oss/goldencheck-action@v1
  with:
    files: "data/*.csv"
    fail-on: error          # or "warning"
    config: goldencheck.yml
```

### With LLM boost

```yaml
- uses: benseverndev-oss/goldencheck-action@v1
  with:
    files: "data/*.csv"
    llm-boost: true
    llm-provider: openai
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `files` | Yes | — | Glob pattern for data files |
| `fail-on` | No | `error` | Severity threshold: `error` or `warning` |
| `config` | No | — | Path to goldencheck.yml |
| `llm-boost` | No | `false` | Enable LLM enhancement |
| `llm-provider` | No | `anthropic` | LLM provider |
| `python-version` | No | `3.12` | Python version |
| `version` | No | latest | GoldenCheck version to install |

## Outputs

| Output | Description |
|--------|-------------|
| `errors` | Total error count |
| `warnings` | Total warning count |
| `health-grade` | Worst health grade across files |

## PR Comments

On pull requests, the action posts a comment with a summary table:

> ## GoldenCheck Results
>
> | File | Errors | Warnings | Findings |
> |------|--------|----------|----------|
> | orders.csv | 2 | 5 | 24 |
> | customers.csv | 0 | 1 | 8 |
>
> **2 files scanned, 2 errors, 6 warnings**

The comment is updated on subsequent pushes (not duplicated).


## Authoritative sources

Read these rather than inferring behaviour from `action.yml` — an input list gives
you names and types, not which defaults are deliberate or what a knob protects
against:

- [https://docs.bensevern.dev/docs/goldencheck](https://docs.bensevern.dev/docs/goldencheck) — full documentation for the underlying tool.
- [`llms.txt`](./llms.txt) — this action, condensed for machine readers; the same
  file also ships inside the `goldencheck` wheel the action installs.
- <https://docs.bensevern.dev/docs/llms.txt> — index of every Golden Suite surface.
- <https://github.com/benseverndev-oss/goldenmatch> — source, issues, and the decision records.

## License

MIT
