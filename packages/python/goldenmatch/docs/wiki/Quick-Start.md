# Quick Start Guide

## 0. First-Time Setup (Optional)

Run the setup wizard to configure GPU, API keys, and database:

```bash
goldenmatch setup
```

![Setup Wizard](../screenshots/setup-welcome.svg)

This is optional — GoldenMatch works out of the box with CPU-safe mode.

## 1. Simplest Usage (Zero-Config)

Point GoldenMatch at a CSV and it figures out the rest:

```bash
goldenmatch dedupe customers.csv
```

GoldenMatch will:
1. Analyze your columns (detect names, emails, phones, etc.)
2. Pick appropriate scorers for each column type
3. Choose a blocking strategy
4. Show a gold-themed auto-config summary screen
5. Let you run immediately, adjust settings, or save for next time

Press `F5` to run, `E` to edit config, or `?` to see all keyboard shortcuts.

## 2. With a Config File

For full control, write a YAML config:

```yaml
# config.yaml
matchkeys:
  - name: exact_email
    type: exact
    fields:
      - field: email
        transforms: [lowercase, strip]

  - name: fuzzy_name
    type: weighted
    threshold: 0.85
    fields:
      - field: name
        scorer: jaro_winkler
        weight: 1.0
        transforms: [lowercase, strip]

blocking:
  keys:
    - fields: [email]
      transforms: [lowercase]
```

```bash
goldenmatch dedupe customers.csv --config config.yaml --output-all --output-dir results/
```

## 3. Match Two Files

```bash
goldenmatch match new_leads.csv --against existing_customers.csv --config config.yaml --output-all
```

## 4. Database Sync

```bash
pip install goldenmatch[postgres]

goldenmatch sync \
  --table customers \
  --connection-string "postgresql://user:pass@localhost/mydb" \
  --config config.yaml
```

## 5. Boost Accuracy with LLM

```bash
pip install goldenmatch[llm]

# Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# First run labels pairs with Claude (~$0.30), trains local model
goldenmatch dedupe products.csv --llm-boost

# Subsequent runs use saved model ($0)
goldenmatch dedupe products.csv --llm-boost
```

## 6. Remember Steward Decisions (Learning Memory, v1.6.0)

If you have humans reviewing borderline pairs, persist their decisions so the same correction never has to be made twice.

```yaml
# add to your config
memory:
  enabled: true
  backend: sqlite
  path: .goldenmatch/memory.db
  reanchor: true
  dataset: customers
```

```bash
goldenmatch dedupe customers.csv --config goldenmatch.yml   # 1. produce review queue
goldenmatch review              --config goldenmatch.yml   # 2. steward decides
goldenmatch dedupe customers.csv --config goldenmatch.yml   # 3. corrections apply
# > Memory: 12 corrections applied, 0 stale, 0 stale-ambiguous, 0 unanchorable
```

Corrections re-anchor across row reorders via `record_hash`. After 10+ corrections accumulate against a matchkey, `goldenmatch memory learn` adjusts that matchkey's threshold automatically. See [[Learning-Memory]] for the full feature.

## Output Files

With `--output-all`, GoldenMatch produces:

| File | Contents |
|------|----------|
| `golden_records.csv` | Merged canonical records |
| `clusters.csv` | Which records belong to which cluster |
| `duplicates.csv` | Records identified as duplicates |
| `unique.csv` | Records with no duplicates |
