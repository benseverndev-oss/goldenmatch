# goldencheck-types

Shared canonical field-type registry for the Golden Suite. Producer: `packages/python/goldencheck/`. Consumers: `packages/python/goldenpipe/` (stage I/O), `packages/python/infermap/` (target schema), `packages/typescript/goldencheck-types/` (cross-language mirror over JSON wire).

## Why this package exists
The Golden Suite needs a single source of truth for "what does field type `email` mean?" — name hints, value signals, confidence thresholds. Goldencheck profiles datasets and emits these types; goldenpipe/infermap consume them. Cross-language because the TS port of goldencheck (and consumers) must produce identical types.

## snake_case exception
This package is the **single Golden Suite location where TypeScript code keeps `snake_case` field names** instead of camelCase. Reason: producer-side YAML and consumer-side JSON wire pass through unchanged, and cross-language parity with the Python sibling matters more than language-idiomatic case style. See `packages/typescript/CLAUDE.md` for the broader convention this exception lives within.

Affected fields: `name_hints`, `value_signals`, `confidence_threshold`, `source_col`, `schema_version`.

Any TS code that directly constructs / consumes these interfaces gets the same exception.

## Layout
```
goldencheck_types/
├── __init__.py     # public API: SchemaVersion, FieldType, InferredSchema, etc.
├── types.py        # Pydantic models. snake_case enforced via Field(alias=...) where needed.
├── loader.py       # YAML → FieldType. Single load_field_types(path) entrypoint.
└── _domains/       # Bundled domain packs (healthcare.yml, finance.yml, ecommerce.yml)
```

## Schema versioning
Every emitted type carries `schema_version`. Consumers MUST check this and refuse unknown versions rather than silently degrade. Bump when:
- A new required field is added to `FieldType`
- An enum gains a value (since old consumers won't recognize it)
- Wire format of `value_signals` changes shape

## Cross-language parity contract
The TS mirror at `packages/typescript/goldencheck-types/` ships the same `FieldType` / `InferredSchema` interfaces. When changing this package's types:
1. Update `packages/typescript/goldencheck-types/src/types.ts` in the same PR.
2. Run TS typecheck: `pnpm --filter goldencheck-types typecheck`.
3. If shape changes, bump `schema_version` and add a migration note to both packages' CHANGELOG.

## Testing
- `pytest packages/python/goldencheck-types/` from monorepo root.
- Domain pack loader is exercised by goldencheck's own test suite (this package has no domain-pack tests of its own — that's intentional, the YAML format is validated end-to-end through goldencheck).

## Gotchas
- Don't add runtime deps beyond `pyyaml`. This package is intentionally minimal so every consumer can pull it in without dragging in numpy/polars/etc.
- `FieldType.value_signals` is a flexible dict by design — adding too much structure here defeats the purpose of "consumer-extensible signal bag". Push back on PRs that try to tighten it.
- Domain YAML files under `_domains/` are bundled as package data. New domain packs need a `[tool.hatch.build.targets.wheel.force-include]` entry if added.
