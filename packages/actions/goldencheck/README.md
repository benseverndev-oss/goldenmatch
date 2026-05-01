# GoldenCheck Action

GitHub Action for [GoldenCheck](https://github.com/benzsevern/goldencheck) — data validation that discovers rules from your data.

Scans data files in CI, posts PR comments with findings, and provides pass/fail status checks.

## Usage

```yaml
- uses: benzsevern/goldencheck-action@v1
  with:
    files: "data/*.csv"
```

### With options

```yaml
- uses: benzsevern/goldencheck-action@v1
  with:
    files: "data/*.csv"
    fail-on: error          # or "warning"
    config: goldencheck.yml
```

### With LLM boost

```yaml
- uses: benzsevern/goldencheck-action@v1
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

## License

MIT
