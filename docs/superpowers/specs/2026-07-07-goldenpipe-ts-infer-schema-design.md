# goldenpipe TS `infer_schema` stage (InferMap port) design

**Date:** 2026-07-07
**Status:** Approved (design)
**Branch:** `feat/goldenpipe-ts-infer-schema` off `origin/main`.

## 1. Goal

Give TypeScript goldenpipe the domain-aware schema-inference stage the Python
pipeline already has (`packages/python/goldenpipe/goldenpipe/stages/infer_schema.py`),
consuming the TS `infermap` package (now WASM-capable), and producing the
**identical `InferredSchema` artifact** — cross-surface parity with the Python
stage. TS goldenpipe references `infermap` nowhere today; this is a purely
additive port.

## 2. What the Python stage does (the reference)

`@stage(name="infer_schema", produces=["inferred_schema"], consumes=[])`:
- Validates that at most one of `schema` / `no_infer` / `domain` is set (else
  raises). Precedence: **schema > no_infer > domain > auto-detect**.
- `schema` set → `ctx.artifacts["inferred_schema"] = schema` (passthrough).
- `no_infer` → `inferred_schema = None`.
- `ctx.df is None` → `inferred_schema = None`.
- else: `domain = cfg["domain"] or detect_domain_detailed(df).domain or "generic"`
  (detect_score = 1.0 when the domain is explicitly pinned; else the detection
  score; 0.0 on the generic fallback).
- `result = infermap.map(df, DomainPackTarget(load_domain(domain)), soft=True)`.
- `_result_to_inferred_schema(result, domain)` -> `InferredSchema`, then
  `confidence` is replaced with `detect_score` (reflects detection quality, not
  the min-of-mapping-confidences the converter computes).
- Sets **two** artifacts on the auto-detect path: `ctx.artifacts["inferred_schema"]
  = inferred` and `ctx.artifacts.setdefault("infer_schema_evidence",
  detect_evidence)` -- where `detect_evidence` is `{detect_reason: "explicit"}`
  (pinned domain), or `{detect_reason, detect_score, runner_up, runner_up_score}`
  (detected), or that plus `fallback: True` (generic fallback). The
  schema/no_infer/no-df early returns set only `inferred_schema` (no evidence).

`_result_to_inferred_schema`:
```python
fields = {}
for fm in result.mappings:
    fields[fm.source] = FieldMapping(
        source_col=fm.source, canonical=fm.target,
        type=fm.target if fm.target else UNMAPPED_TYPE,
        confidence=fm.confidence, evidence={"reasoning": fm.reasoning})
for col in result.unmapped_source:
    if col not in fields:
        fields[col] = FieldMapping(source_col=col, canonical=None,
            type=UNMAPPED_TYPE, confidence=0.0, evidence={})
confidence = min((fm.confidence for fm in result.mappings), default=0.0)
return InferredSchema(domain=domain, fields=fields, confidence=confidence)
```

## 3. TS building blocks (all exist)

- **`goldencheck-types`** exports `InferredSchema`, `FieldMapping`, `UNMAPPED_TYPE`,
  `loadDomain`, `DomainPack`. The TS `FieldMapping` uses the SAME snake_case field
  names as Python (`source_col`, `canonical: string | null`, `type`, `confidence`,
  `evidence: Record<string, unknown>`), and `InferredSchema = { domain, fields:
  Record<string, FieldMapping>, confidence, schema_version? }`. So the conversion
  is a 1:1 mirror and the artifact is structurally identical cross-surface.
- **`infermap`** exports `map(source, target, options)` (with `options.soft`),
  `DomainPackTarget`, `MapResult` (`{ mappings: {source, target, confidence,
  reasoning}[], unmappedSource: string[] }` — camelCase `unmappedSource`).
- **`detectDomainDetailed`** exists (`infermap/src/core/detect.ts`) and accepts
  `{ records }` or `{ columns }`. **BUT it is not re-exported from the barrel** —
  `core/index.ts` only surfaces `detectDomain`. This spec adds `detectDomainDetailed`
  (and `DEFAULT_MIN_SCORE` is already there) to that re-export so the stage can
  `import { detectDomainDetailed } from "infermap"` (also aligns with Python, which
  exports `detect_domain_detailed` at the top level).
- **goldenpipe** `Stage = { info: { name, produces, consumes }, validate(ctx),
  run(ctx): Promise<StageResult> }`; `PipeContext = { df: Row[] | null, artifacts:
  Record<string, unknown>, stageConfig: Record<string, unknown> }`; `Row =
  Record<string, unknown>`. Stages are registered in `buildDefaultRegistry()`
  (`core/adapters/index.ts`).

## 4. The stage

New file `packages/typescript/goldenpipe/src/core/adapters/infer.ts`:
```ts
export const InferSchemaStage: Stage = {
  info: { name: "infer_schema", produces: ["inferred_schema", "infer_schema_evidence"], consumes: [] },
  validate(_ctx) { /* no precondition — consumes []; df===null is a valid SUCCESS branch */ },
  async run(ctx) { /* flag-conflict check FIRST (throws), then the 4 branches + convert */ },
};
```
- **Name `infer_schema`** (no dotted prefix) matches the Python stage name, so a
  shared pipeline config referencing `infer_schema` resolves on both surfaces.
- `stageConfig` keys mirror Python: `schema?: InferredSchema`, `no_infer?: boolean`,
  `domain?: string`.
- **Flag-conflict check lives in `run()`, not `validate()`** — mirroring Python,
  where `_validate_flags(cfg)` is the first line of `run()` and *raises* on
  conflict. The TS `run()` calls a `validateFlags(cfg)` helper first; it throws
  when more than one of `{schema, no_infer, domain}` is set. (The Python tests call
  `.run()` and expect the raise; the TS tests mirror that — §7. `validate()` stays
  a no-op: `consumes: []` and `df === null` is a legitimate SUCCESS branch, so
  `validate` must NOT throw on a null df.)
- `run` (after `validateFlags`):
  - `schema` present → `ctx.artifacts.inferred_schema = schema`; SUCCESS. (No
    evidence artifact — matches Python's early return.)
  - `no_infer` → `inferred_schema = null`; SUCCESS. (No evidence.)
  - `ctx.df === null` → `inferred_schema = null`; SUCCESS. (No evidence.)
  - else (auto-detect / explicit-domain path):
    - `const explicit = cfg.domain;`
    - explicit set → `domain = explicit; detectScore = 1.0; detectEvidence =
      { detect_reason: "explicit" };`
    - else → `const detection = detectDomainDetailed({ records: ctx.df });` (bind
      ONCE). If `detection.domain !== null`: `domain = detection.domain; detectScore
      = detection.score; detectEvidence = { detect_reason: detection.reason,
      detect_score: detection.score, runner_up: detection.runner_up,
      runner_up_score: detection.runner_up_score };` else (generic fallback):
      `domain = "generic"; detectScore = 0.0; detectEvidence = { detect_reason:
      detection.reason, detect_score: detection.score, runner_up:
      detection.runner_up, runner_up_score: detection.runner_up_score, fallback:
      true };`
    - `const result = map({ records: ctx.df }, new DomainPackTarget(loadDomain(domain)),
      { soft: true });`
    - `const inferred = { ...resultToInferredSchema(result, domain), confidence:
      detectScore };` (fresh object — `InferredSchema` is readonly; overwrite
      `confidence` with `detectScore`, matching Python's `replace(inferred,
      confidence=detect_score)`).
    - `ctx.artifacts.inferred_schema = inferred;`
    - **setdefault semantics:** `if (!("infer_schema_evidence" in ctx.artifacts))
      ctx.artifacts.infer_schema_evidence = detectEvidence;` (Python uses
      `setdefault` — only set when a prior stage hasn't already).
    - SUCCESS.

`resultToInferredSchema` is the direct mirror of `_result_to_inferred_schema`
(§2), using the snake_case `FieldMapping` fields, `UNMAPPED_TYPE`, and
`result.unmappedSource` (camelCase). Confirmed: TS `soft` sets `target: null` and
keeps the mapping in `mappings` (not moved to `unmappedSource`), so
`canonical: fm.target` (→ null) and `type: fm.target ? fm.target : UNMAPPED_TYPE`
reproduce Python's `None` handling exactly.

> **Fidelity detail to verify in the plan:** the Python `soft=True` sets a
> below-threshold `fm.target` to `None` (→ `canonical=None`, `type=UNMAPPED_TYPE`).
> The plan must confirm how the TS `map(..., { soft: true })` represents an
> unmapped target on `MapResult.mappings[].target` (typed `string`) — whether it
> becomes `""`, `null`, or the mapping moves to `unmappedSource`. The conversion's
> `canonical`/`type` branch (`fm.target ? fm.target : UNMAPPED_TYPE`) must reproduce
> the Python None-handling exactly for whatever representation TS uses. This is the
> one place a structural drift could hide.

## 5. Registration — opt-in, NOT default (parity-true)

Register `InferSchemaStage` in `buildDefaultRegistry()` (add
`registry.register(InferSchemaStage)` + export it from `adapters/index.ts`), but
**do NOT add it to `DEFAULT_STAGE_ORDER`**. Verified: the Python default/auto
pipeline (`pipeline.py:121`) is also just `["goldencheck.scan",
"goldenflow.transform", "goldenmatch.dedupe"]` — `infer_schema` is registered but
opt-in there too. Matching that (available, not default) is the true parity and
keeps the default TS pipeline unchanged. Consumers opt in by naming `infer_schema`
in their pipeline config (as they do in Python).

## 6. Dependencies

Add **both** to `packages/typescript/goldenpipe/package.json` `dependencies`
(goldenpipe currently depends only on `commander, goldencheck, goldenflow,
goldenmatch` — it references neither package today):
- `"infermap": "workspace:^"` — the `map`/`detectDomainDetailed`/`DomainPackTarget` API.
- `"goldencheck-types": "workspace:^"` — the `InferredSchema`/`FieldMapping`/
  `UNMAPPED_TYPE`/`loadDomain` types + loader. It is NOT currently a goldenpipe dep
  and has zero imports in goldenpipe `src`; do not rely on transitive/hoisted
  resolution — add the direct dep.

## 7. Testing — mirror the Python suite

Port all **seven** `packages/python/goldenpipe/tests/test_infer_schema_stage.py`
cases to a TS unit test (`tests/unit/infer-schema-stage.test.ts` or the goldenpipe
test convention). The Python tests call `stage.run(ctx)` and, for conflicts,
expect it to throw (`pytest.raises(ValueError, match="conflict")`) — the TS tests
mirror that (`await expect(InferSchemaStage.run(ctx)).rejects.toThrow(/conflict/)`,
since the conflict check is in `run()`):
1. auto-detect finance columns → `inferred.domain === "finance"`.
2. explicit `domain: "finance"` config → `inferred.domain === "finance"`.
3. `no_infer: true` → `inferred_schema === null`.
4. user `schema` passthrough → `ctx.artifacts.inferred_schema` **is** the input.
5. conflict `schema` + `domain` → `run` throws (`/conflict/`).
6. conflict `no_infer` + `domain` → `run` throws.
7. conflict `no_infer` + `schema` → `run` throws.

The `InferredSchema` shape is shared via `goldencheck-types`, so structural
cross-surface parity is by construction; these behavior tests lock the branch
logic + the conversion. (A deeper Python↔TS golden-vector parity test is possible
but out of scope — the shared type + mirrored tests are sufficient for this port.)

## 8. WASM note

The stage calls `infermap.map`, whose scorers are WASM-capable. If the consumer
has called `enableInfermapWasm()`, the stage's scorers dispatch to the Rust
kernels automatically; otherwise pure-TS (byte-identical). The stage does NOT
force-enable WASM — consumer's choice, consistent with every other WASM surface.

## 9. Out of scope

- Changing `DEFAULT_STAGE_ORDER` on either surface, or auto-inserting `infer_schema`.
- The goldenpipe CLI, or a Python↔TS golden-vector parity harness for this stage.
- Any change to the Python stage.

## 10. Risk assessment

Low — every building block exists (shared `InferredSchema` type, the `infermap`
API, the goldenpipe stage/registry pattern). It's a faithful translation of a
~90-line Python stage with a mirrored 7-case test. The single care point is §4's
soft-mode `target` fidelity in `resultToInferredSchema`; the plan resolves it by
reading the TS `map` soft path and matching the None-handling.

## 11. Build environment constraints

- **Box-runnable:** the Python reference stage + its tests run locally (for
  cross-checking expected outputs); TS is CI-only (tsc/vitest OOM) — write against
  spec + eye/`node --check` verify, CI is the first real test.
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
