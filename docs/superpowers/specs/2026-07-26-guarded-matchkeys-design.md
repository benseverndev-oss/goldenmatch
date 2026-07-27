# Guarded / conditional matchkeys

**Date:** 2026-07-26
**Status:** approved (design) — implementing.
**Frame conformance:** `context-network/architecture/one-product-two-engines.md` + decision 0047. This is a
config-surface + Python-orchestration feature (no kernel change), single-owner predicate semantics, shipped
Python-first with an explicitly declared TypeScript gap (per the amended commitment 3: "shared capabilities
must conform; surface-specific gaps must be explicit, justified, declared").

## The problem

A matchkey today fires on every candidate pair it scores. But real ER logic is often *conditional*: "match on
SSN **only when** the SSN is a real value, not the `000-00-0000` placeholder"; "trust an email match **only
when** the email isn't a shared role address (`info@`, `sales@`)". Without a guard, a matchkey mega-merges every
record that happens to share a junk value. Today the only per-field conditional gate is **negative evidence**
(subtract a penalty when a field *disagrees*) — there is no way to say "this matchkey doesn't apply to this
pair at all."

This is the one bucket-B capability add from the dbt-converter Phase 2 taxonomy that stands on its own merits:
useful to anyone writing GoldenMatch configs by hand (and the natural target for the converter's CASE-ladder
recognizer later).

## Design

### Reuse the existing predicate evaluator (single semantic owner)

GoldenMatch already has a safe predicate mini-language: `core/survivorship/conditions.py::eval_predicate(expr,
resolved)` — an AST-allowlist evaluator (NOT `eval`) behind `GoldenFieldRule.when`, supporting `and/or/not`,
`== != < <= > >= in not in`, literals, names, and list/tuple literals, with well-defined miss semantics (an
unknown name → the clause doesn't fire). Guards **reuse it verbatim** rather than introducing a second predicate
dialect. `referenced_names(expr)` gives the columns a guard reads (for config-time validation).

### Pair-level predicates (`a_`/`b_` naming)

A guard is a **pair** predicate: it can reference both records via `a_<field>` (left/first record) and
`b_<field>` (right/second record). Example:

```yaml
matchkeys:
  - name: ssn_match
    type: exact
    fields: [{field: ssn}]
    guard: "a_ssn != '000-00-0000' and b_ssn != '000-00-0000'"
```

The resolved dict handed to `eval_predicate` binds `a_<col>` and `b_<col>` for every column the guard references
(pulled from the two records). **Config-time validation** parses the guard and requires every referenced name to
be `a_`/`b_`-prefixed and to resolve to a real column — a bare/unprefixed name is a config error, not a silent
always-miss (which would quietly disable the matchkey). Collision caveat (documented): a literal column named
`a_ssn` shadows field `ssn`'s left alias — rare; the validator surfaces the ambiguity.

### Semantics: guard-fails → the matchkey doesn't fire (composable, not a veto)

When a guard evaluates False (including a miss), the matchkey **does not emit that pair**. It is a *per-matchkey
pre-filter*, NOT a global veto — the pair can still match via another matchkey. (Global rejection is negative
evidence's job.) This composes cleanly with GoldenMatch's "a pair matches if ANY matchkey fires" model.

### Granularity: matchkey-level AND field-level (v1 ships matchkey-level)

The config schema carries **both** `MatchkeyConfig.guard` (matchkey-level) and `MatchkeyField.guard`
(field-level). v1 WIRES matchkey-level guards; field-level is a fast follow (the piece with the most
implementation risk — modifying the vectorized weighted-average — so it's deferred until it can be designed +
tested carefully, and rejected loudly meanwhile so nothing is silently ignored).

- **Matchkey-level** (`MatchkeyConfig.guard`) — SHIPPED for **exact + weighted** matchkeys, applied as a per-pair
  post-filter on the emitted pairs (uniform across the exact / bucket-fast / slow scoring paths). This is the
  headline SSN-placeholder case. A guard on a **probabilistic** matchkey raises a clear "planned follow-up" config
  error (the FS pair stream needs its own post-filter seam).
- **Field-level** (`MatchkeyField.guard`) — SHIPPED for **weighted** matchkeys. A guard-failing field drops out of
  the weighted average and the remaining weights renormalize; if every field's guard fails, the pair is not
  emitted. Implemented as a per-pair mask folded into each field's `valid` weighting term in `find_fuzzy_matches`
  (a dropped field contributes 0 to BOTH the numerator and the denominator, so renormalization is automatic);
  weighted matchkeys carrying a field guard route off the vectorized bucket path onto that slow path. Field-level
  guards on **exact / probabilistic** matchkeys raise a clear config error (no per-field weight to renormalize).
  Guards should be SYMMETRIC in a/b; the per-pair mask is `O(block²)` in guard evaluations, acceptable because
  blocks are bounded (a columnar lowering is the future lever).

### Implementation seam (no kernel; uniform pair post-filter)

Negative evidence is the structural precedent: a per-pair condition gating a matchkey, applied in Python
orchestration around the (optional) native kernel. The matchkey-level guard is implemented as a **uniform
post-filter on the emitted pairs** — `scorer._apply_guard_to_exact_pairs(pairs, mk, df, raw_values=…)` — applied
in `_run_dedupe_pipeline` right after each scoring path produces its pairs (exact slow path, weighted bucket path,
weighted slow path). This needs **no change to the hot `score_buckets` / `find_fuzzy_matches` internals** and no
fast-path rerouting: the guard just drops pairs after scoring. Motivation class: **semantic / config-surface
expressiveness**, not perf.

**Raw-value capture** is the load-bearing subtlety. Prep (auto-fix / standardize / a content-based normalizer)
mutates columns in place — an SSN column is phone-normalized to `+1000000000` before scoring — so a guard reading
the *prepared* frame would see mangled values and its literals would never match. `_run_dedupe_pipeline` therefore
collects the guard-referenced columns' RAW values into a Python dict (`{col: {row_id: value}}`) at pipeline entry,
*before* any prep and *outside* the frame (an in-frame snapshot column gets normalized too; a list-typed snapshot
breaks the quality scanner). The guard filter reads that dict, falling back to the prepared column only when a
column has no snapshot. Columnar guard lowering (`core/golden_fused_predicate.py::lower_predicate`) is a documented
future optimization if a measured at-scale workload needs it.

### Guard helper

`core/guard.py`:
- `pair_resolved(referenced: set[str], row_a: dict, row_b: dict) -> dict` — build the `a_`/`b_` binding dict for
  a guard's referenced names from two records' column values.
- `guard_passes(expr: str, row_a, row_b) -> bool` — resolve + `eval_predicate`.
- `validate_guard(expr: str, columns: set[str]) -> None` — config-time check (parse, `a_`/`b_` prefix required,
  underlying column exists).

## Surface / parity

- **Config schema only** — `guard` on `MatchkeyConfig` + `MatchkeyField`. Config-schema fields are **not** an
  `api_parity`-gated surface (the gate covers MCP tools / CLI commands / a2a skills / scorers / transforms /
  blocking strategies — not schema shape), so this introduces **no** automated parity obligation and no new
  scorer/transform name.
- **TypeScript gap: declared.** The TS port (`packages/typescript/goldenmatch/src/core/types.ts`) mirrors
  MatchkeyConfig by convention; a guard field + evaluator is a follow-up port, explicitly noted here as a
  surface-specific gap (frame-conformant). No forced simultaneous WASM port.

## Testing

- Unit: `eval_predicate` reuse via the `a_`/`b_` binding (placeholder guard, not-in-set, both-must-pass, miss →
  no-fire); `validate_guard` rejects bare names + unknown columns.
- Exact matchkey-level guard: an SSN-placeholder pair that would merge without a guard is suppressed; a valid-SSN
  pair still merges; the pair still matches via a second unguarded matchkey (composability).
- Weighted matchkey-level + field-level guards: field drop + weight renormalization; all-fields-drop → no emit.
- Probabilistic matchkey-level guard: emitted pairs post-filtered.
- End-to-end `dedupe_df` with a guarded config runs and produces the expected clusters.
- Fast-path routing: a guarded matchkey does not take the vectorized bucket/native path (parity: guarded slow ==
  expected).

## Boundaries (stated honestly)

- **Shipped:** matchkey-level guards on exact + weighted matchkeys; field-level guards on weighted matchkeys.
  Probabilistic-matchkey guards and field-level guards on exact/probabilistic raise a clear config error —
  defined in the schema, not yet applied, so a guard is never silently ignored.
- The guard is a per-pair post-filter over the guard's referenced columns; it's cheap but not vectorized (same
  cost profile as negative evidence). Columnar lowering is the future lever if an at-scale workload needs it.
- TypeScript parity is a declared follow-up gap (config-schema fields are not an `api_parity`-gated surface).
