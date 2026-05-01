# Design: InferMap → GoldenCheck handoff (v1)

**Date:** 2026-05-01
**Author:** Ben Severn (with Claude)
**Status:** Draft

## Context

GoldenPipe today orchestrates `GoldenCheck → GoldenFlow → GoldenMatch` over a single labeled CSV. Schema discovery is implicit: GoldenCheck infers field types from headers using its own heuristics. InferMap exists as a standalone schema-mapping engine (`source → target`) but is not wired into the pipe.

This spec wires InferMap in as **stage 0** of GoldenPipe. The four products read a shared type registry (`goldencheck-types`), eliminating the current drift between InferMap detection and GoldenCheck rule selection. Single-source only in v1; multi-source `align()` is deferred.

## Goals

- One shared type registry (`goldencheck-types`) consumed by both InferMap (as detection priors) and GoldenCheck (as rule routing keys).
- GoldenPipe runs InferMap before GoldenCheck so downstream rules and matching see canonical types instead of raw headers.
- Soft-target behavior: low-confidence columns pass through tagged `unknown`, never blocking the pipeline.
- TS parity for the three packages that have JS ports (`infermap`, `goldencheck`, `goldencheck-types`). Pipe orchestration ports later.
- Backward-compatible: `goldenpipe.run("file.csv")` without flags still works; new behavior auto-engages when a domain pack matches.

## Non-goals

- Multi-source `align()` — separate spec.
- Per-user/per-run threshold tuning UI.
- Domain-pack auto-learning from `unmapped_column` findings.
- Runtime coupling between `infermap` and `goldencheck` (they remain decoupled, communicating via the shared yaml + the `InferredSchema` data type).

## Architecture

```
                     ┌─────────────────────────┐
                     │ goldencheck-types/      │  single source of truth
                     │   domains/*.yaml        │  yaml schema unchanged
                     │     name_hints          │  read by InferMap
                     │     value_signals       │  read by InferMap
                     │     suppress            │  read by GoldenCheck
                     │     confidence_threshold│  optional per-type override
                     └────────────┬────────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  ▼                               ▼
        ┌─────────────────┐             ┌─────────────────┐
        │   InferMap      │ Inferred    │   GoldenCheck   │
        │ + DomainPack    │  Schema     │ + schema input  │
        │   target        ├────────────►│ + unknown-col   │
        │ + soft mode     │             │   finding       │
        └─────────────────┘             └────────┬────────┘
                  ▲                              │
                  │                              ▼
                  └──────────┬───────────────────┘
                             │
                  ┌──────────┴──────────┐
                  │     GoldenPipe      │
                  │ + --domain flag     │
                  │ + auto-detect       │
                  │ + InferMap stage 0  │
                  └─────────────────────┘
```

Four units, each with one clear responsibility, communicating via shared types:

1. **`goldencheck-types`** — yaml registry + Python/TS bindings.
2. **`infermap`** — schema mapping with domain-pack target adapter and soft-fail.
3. **`goldencheck`** — quality checks routed by canonical type.
4. **`goldenpipe`** — orchestrates the three.

## Components

### 1. `goldencheck-types`

**yaml schema:** unchanged for v1, plus one optional new key per type:

```yaml
types:
  ssn:
    name_hints: ["ssn", "social_security"]
    value_signals: { regex: "^\\d{3}-\\d{2}-\\d{4}$" }
    suppress: ["uniqueness"]
    confidence_threshold: 0.85    # optional; defaults to global threshold
```

**Bindings (new):**

- `packages/python/goldencheck-types/` — new Python package. Exposes:
  ```python
  from goldencheck_types import FieldSpec, DomainPack, load_domain, list_domains
  pack = load_domain("finance")          # DomainPack
  pack.types["account_number"]           # FieldSpec
  pack.types["account_number"].name_hints
  ```
- `packages/typescript/goldencheck-types/` — already exists; extend its public API to mirror the Python bindings (`loadDomain`, `DomainPack`, `FieldSpec`).

Both bindings read the same yaml files in `packages/typescript/goldencheck-types/domains/` (the existing location). yaml is loaded at runtime, not codegen — keeps single source of truth.

### 2. `infermap`

**New: `DomainPackTarget` adapter.**

Today `MapEngine.map(source, target)` accepts a target schema (CSV, DataFrame, DB URI, schema YAML). Add a code path that recognizes a `DomainPack` (from `goldencheck-types`) as a target. The adapter translates `name_hints` and `value_signals` into the existing scorer inputs — no new scorer types needed.

```python
import goldencheck_types as gct
import infermap

result = infermap.map(source_df, gct.load_domain("finance"), soft=True)
# result.fields: list[FieldMapping]
#   FieldMapping(source_col="account_id", canonical="account_number",
#                type="account_number", confidence=0.92)
```

**New: `soft` parameter.** When `soft=True` (default for pipe use), columns whose aggregated confidence is below threshold return:

```python
FieldMapping(source_col="region_code", canonical=None, type="unknown",
             confidence=0.41, evidence=...)
```

Threshold resolution:
1. If the type has `confidence_threshold` in yaml, use that.
2. Else use the InferMap engine's global default (configurable, default `0.7`).

**New: `infermap.detect_domain(df, candidates=None)`.** Lightweight: hash column names, score against each domain pack's combined name_hints, return the highest-scoring pack name (or `None` if all below `0.3`). Used by GoldenPipe for auto-detection. Implementation reuses existing `name_hints` matching scorer; no new code path.

### 3. `goldencheck`

**Updated `check()` signature:**

```python
def check(df, schema: InferredSchema | None = None,
          domain: str | None = None) -> CheckReport:
    ...
```

When `schema` is provided:
- For each column, look up its `FieldMapping`. If `type != "unknown"`, route rules by canonical type via `domain_pack.types[type].suppress`.
- For `type == "unknown"` columns: fall back to current header-heuristic detection AND emit a new finding type:
  ```python
  Finding(severity="info", code="unmapped_column",
          column="region_code",
          message="Column 'region_code' could not be typed against domain "
                  "pack 'finance'. Consider adding name_hints to the pack.")
  ```

When `schema` is None: legacy behavior (header-heuristic detection on all columns). No `unmapped_column` findings emitted.

**Unknown-column fallback detail:** when a column is `type="unknown"` inside an otherwise typed schema, GoldenCheck runs its full header-heuristic path on that column *and* applies the matched pack's universal-rule subset (e.g. null %, cardinality). The pack's per-type `suppress` rules do not apply (since no canonical type was matched), but pack-wide universal rules still do.

The `domain` parameter is a convenience: `check(df, domain="finance")` internally calls `infermap.map` with that pack and uses the result. Useful when calling GoldenCheck without going through the pipe.

### 4. `goldenpipe`

**New `--domain` flag** on `goldenpipe run`:
```
goldenpipe run customers.csv                    # auto-detect
goldenpipe run customers.csv --domain finance   # explicit pack
goldenpipe run customers.csv --no-infer         # skip InferMap entirely
goldenpipe run customers.csv --schema my.yaml   # user-provided schema, skip InferMap
```

**Flag precedence** (highest to lowest, conflicting flags error out):

| Flag | Behavior | Conflicts with |
|---|---|---|
| `--schema <file>` | Use this schema; skip InferMap. | `--domain`, `--no-infer` |
| `--no-infer` | Skip InferMap; pass `schema=None` to GoldenCheck. | `--domain`, `--schema` |
| `--domain <name>` | Use this pack; run InferMap. | `--schema`, `--no-infer` |
| (none) | Auto-detect pack; run InferMap. | — |

Conflicting flags cause exit code 2 with a clear error message.

**Run-time flow:**

```python
def run(source, *, domain=None, no_infer=False, schema=None):
    df = _load(source)

    if schema is not None:
        inferred = schema                          # user-provided, trust it
    elif no_infer:
        inferred = None                            # legacy mode
    else:
        pack_name = domain or infermap.detect_domain(df) or "generic"
        pack = goldencheck_types.load_domain(pack_name)
        inferred = infermap.map(df, pack, soft=True)

    report = goldencheck.check(df, schema=inferred)
    fixed = goldenflow.fix(df, report) if report.has_issues else df
    matched = goldenmatch.match(fixed, schema=inferred)
    return PipeResult(inferred=inferred, check=report, transform=fixed, match=matched)
```

`"generic"` pack is a built-in empty pack (no types defined) — every column ends up `unknown` and goldencheck falls back to header heuristics. This is the no-domain-matched path.

## Data flow contract

```python
@dataclass
class InferredSchema:
    domain: str                                  # pack name, "generic", or "user"
    fields: dict[str, FieldMapping]              # source_col -> mapping
    confidence: float                            # min across mapped fields
    unmapped: list[str]                          # source_cols with type="unknown"

@dataclass
class FieldMapping:
    source_col: str
    canonical: str | None                        # canonical name from pack, or None
    type: str                                    # canonical type name, or "unknown"
    confidence: float
    evidence: dict                               # InferMap-internal; shape is intentionally loose, consumers must not rely on its structure
```

`InferredSchema` is the only new cross-package type. Lives in `goldencheck-types` so all four packages can import it without inducing a runtime dep on InferMap.

## Error handling

| Condition | Behavior |
|---|---|
| Auto-detect finds no matching pack | fall back to `"generic"`; pipe emits one-line warning |
| All columns end up `unknown` | pipe runs to completion; CheckReport dominated by `unmapped_column` findings |
| User passes `--domain unknown_pack` | hard error, exit 2 |
| User passes `--schema`, file missing or malformed | hard error, exit 2 |
| User passes conflicting flags (`--schema` + `--domain`, etc.) | hard error, exit 2 with precedence-table guidance |
| InferMap raises during scoring | propagate; do not silently fall through |
| `confidence_threshold` in yaml outside `[0,1]` | yaml load raises at startup; not silent |

## Testing

**Unit (per package):**
- `goldencheck-types`: `load_domain` returns expected `DomainPack`; `confidence_threshold` parses; missing pack raises.
- `infermap`: `DomainPackTarget` adapter produces same scorer outputs as equivalent yaml-target. `soft=True` returns `unknown` mapping for engineered low-confidence columns. `detect_domain` picks correct pack from labeled fixture.
- `goldencheck`: with `schema=` argument, rules are routed by canonical type; unknown columns produce `unmapped_column` findings; legacy `schema=None` path unchanged.
- `goldenpipe`: `--domain` short-circuits auto-detect; `--no-infer` short-circuits InferMap; `--schema` overrides both.

**Integration:**
- Three fixtures: clean `finance.csv`, clean `healthcare.csv`, `mixed_unknown.csv` (deliberately includes columns no pack can type).
- Snapshot: `InferredSchema` + `CheckReport` per fixture.
- Assert: `mixed_unknown.csv` has at least one `unmapped_column` finding; pipe completes without error.

**Parity:**
- Shared fixture (`finance.csv`) run through Python and TS implementations of `goldencheck-types`, `infermap`, `goldencheck`.
- Assert: equal `InferredSchema.fields` (modulo TS vs Python representation), equal CheckReport finding codes.

## TS parity scope

Three of the four packages have existing TS ports and ship with this spec:
- `goldencheck-types` (TS): extend with `loadDomain`, `DomainPack`, `FieldSpec`.
- `infermap` (TS): add `DomainPackTarget` adapter + soft mode.
- `goldencheck` (TS): accept schema input, emit `unmapped_column` finding.

`goldenpipe` has no TS port today. Tracked TODO: TS pipe orchestration in a follow-up spec.

## Open questions

None — all locked during brainstorming:
- Q1: D + soft target (auto-detect domain pack default + user override + soft fail).
- Q2: B + C (header-heuristic fallback for unknowns + emit `unmapped_column` finding).
- Q3: C (single-source v1; multi-source deferred).
- Q4: B (Python + TS together for goldencheck-types, infermap, goldencheck; pipe is Python only since no TS port exists).
- Q5: C (single global default + per-type override in yaml).
- Q6: A (reuse existing `name_hints` / `value_signals` yaml fields; no new yaml structure beyond optional `confidence_threshold`).

## Risks

| Risk | Mitigation |
|---|---|
| Auto-detect picks the wrong pack on ambiguous data | User can override with `--domain`; pipe surfaces detected pack name in output so silent miss is hard |
| `goldencheck`'s legacy header-heuristic path drifts from typed path | Integration test asserts `schema=None` and `schema=<full>` give equivalent rule selection on a known clean fixture |
| Scorer aggregation in InferMap interpreted differently in Python vs TS | Parity test catches this on the shared fixture |
| Adding `confidence_threshold` to yaml breaks existing yaml consumers | Field is optional with a default; existing yaml files are still valid |
| InferMap runtime cost added to every pipe call | `--no-infer` opt-out; auto-detect is cheap (column-name hashing only, no full mapping) |
