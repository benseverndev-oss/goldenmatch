# goldencheck-types

Shared canonical field-type registry for the [Golden Suite](https://github.com/benseverndev-oss/goldenmatch) — a single source of truth for *"what does the field type `email` mean?"* (name hints, value signals, confidence thresholds) across every suite package and both languages.

## What it is

GoldenCheck profiles a dataset and emits **inferred field types**; downstream packages consume them:

- **Producer:** `goldencheck` (data-quality profiling).
- **Consumers:** `goldenpipe` (stage I/O contracts), `infermap` (target schema inference), and the TypeScript mirror `goldencheck-types` (cross-language, over a JSON wire).

Keeping the type vocabulary in one small, dependency-light package lets the Python and TypeScript sides produce byte-identical types, and lets consumers depend on the vocabulary without pulling in the full profiler.

## Install

```bash
pip install goldencheck-types
```

Only requires `pyyaml`.

## Usage

```python
from goldencheck_types import load_field_types, FieldType, InferredSchema

# Load the bundled canonical field types (16 domain packs: generic + 15 verticals)
field_types = load_field_types()

# Or inspect a single type
email = field_types["email"]
print(email.name_hints, email.confidence_threshold)
```

Public API (`goldencheck_types`): `SchemaVersion`, `FieldType`, `InferredSchema`, `load_field_types`, and the supporting Pydantic models in `types.py`.

## Schema versioning

Every emitted type carries a `schema_version`. Consumers **must** check it and refuse unknown versions rather than silently degrade. The version bumps when a required field is added to `FieldType`, an enum gains a value, or the `value_signals` wire shape changes.

## Cross-language parity

The TypeScript sibling at [`packages/typescript/goldencheck-types`](https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/typescript/goldencheck-types) ships the same `FieldType` / `InferredSchema` interfaces and the same bundled domain packs, synced byte-for-byte from one canonical source. This is the one Golden Suite package where the TypeScript side keeps `snake_case` field names, so the producer YAML and consumer JSON pass through unchanged.

## License

MIT. Part of the Golden Suite; see the [monorepo](https://github.com/benseverndev-oss/goldenmatch) for the full project.
