---
layout: default
title: CLI Reference
nav_order: 3
---

# CLI Reference

## infermap map

Map source columns to a target schema.

```bash
infermap map SOURCE TARGET [OPTIONS]
```

**Arguments:**
- `SOURCE` -- source file path, DB URI, or schema file
- `TARGET` -- target file path, DB URI, or schema file

**Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--table TEXT` | DB table name (required for DB sources) | |
| `--required TEXT` | Comma-separated required target fields | |
| `--schema-file TEXT` | Schema definition file (YAML/JSON) for extra aliases | |
| `--format TEXT` | Output format: `table`, `json`, `yaml` | `table` |
| `-o, --output TEXT` | Save mapping config to YAML file | |
| `--min-confidence FLOAT` | Minimum confidence threshold | `0.3` |
| `-v, --verbose` | Show INFO-level logs | |
| `--debug` | Show DEBUG-level logs | |

**Examples:**

```bash
# Basic file-to-file mapping
infermap map source.csv target.csv

# Map to a Postgres table
infermap map incoming.csv "postgresql://user:pass@host/db" --table customers

# JSON output
infermap map source.csv target.csv --format json

# Save mapping for reuse
infermap map source.csv target.csv -o mapping.yaml

# With required fields
infermap map source.csv target.csv --required email,phone
```

---

## infermap apply

Apply a saved mapping to remap a file.

```bash
infermap apply SOURCE --config FILE --output FILE
```

**Options:**

| Option | Description |
|--------|-------------|
| `--config, -c TEXT` | Mapping config YAML file (required) |
| `--output, -o TEXT` | Output file path (required) |
| `-v, --verbose` | Show INFO-level logs |

**Example:**

```bash
infermap apply source.csv --config mapping.yaml --output remapped.csv
```

---

## infermap inspect

Show fields, types, and sample values for a schema.

```bash
infermap inspect SOURCE [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--table TEXT` | DB table name |
| `-v, --verbose` | Show INFO-level logs |

**Example:**

```bash
infermap inspect data.csv
infermap inspect "postgresql://host/db" --table users
```

---

## infermap validate

Validate that a source file satisfies a mapping config. Useful as a CI gate.

```bash
infermap validate SOURCE --config FILE [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--config, -c TEXT` | Mapping config YAML file (required) |
| `--required TEXT` | Comma-separated required target fields |
| `--strict` | Exit code 1 if required fields are unmapped |
| `-v, --verbose` | Show INFO-level logs |

**Example:**

```bash
# Soft validation (warnings only)
infermap validate source.csv --config mapping.yaml --required email

# Strict mode for CI (fails on unmet requirements)
infermap validate source.csv --config mapping.yaml --required email,phone --strict
```
