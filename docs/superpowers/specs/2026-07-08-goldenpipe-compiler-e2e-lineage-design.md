# GoldenPipe Compiler — SP3: End-to-End Field Lineage (Flow-clean × Match-survivorship) — Design

**Date:** 2026-07-08
**Status:** Approved (brainstorming), pending implementation plan
**Program:** GoldenPipe compiler. SP1 (IR walking skeleton, PR #1592) + SP2 (field-level
provenance, PR #1597) shipped. This is **sub-project 3**.

## Why this shape (the measured pivot — again)

The literal ask was "row/cluster-level provenance." Code inspection shows goldenmatch
**already has a complete row/cluster/survivorship lineage system**, exposed via its
public API, CLI, MCP server, and web router:
- `core/golden.py`: `FieldProvenance{value, source_row_id, strategy, confidence,
  candidates, condition, validator, dropped_invalid}`, `GroupProvenance` (correlated
  survivorship), `ClusterProvenance{cluster_id, cluster_quality, cluster_confidence,
  fields: dict[str, FieldProvenance], groups}`, `GoldenRecordResult.provenance`.
- `core/lineage.py`: `build_lineage`, `golden_provenance_for_run`, `save_lineage`,
  `load_lineage`, FS scoring waterfalls, NL renderers.

So "build row/cluster provenance" would **rebuild `lineage.py`** — the exact dead end
this program has hit repeatedly (fusion, auto-config opt-out, emit-to-scale all already
existed too). The **only** net-new sliver: goldenmatch's lineage runs on the *post-Flow*
data — it knows survivorship + matching but has **no knowledge of what Flow cleaned each
column before matching**. SP2 has the transform lineage but no row-level survivor. Only
the *compiler* sees both. SP3 stitches them.

## Goal

Two host-only pieces:
1. **Surface** goldenmatch's existing golden-provenance as a GoldenPipe pipeline
   artifact (the match adapter drops it today).
2. **Stitch** it with SP2's field-lineage into an **end-to-end field journey**: for each
   golden field, `source row → pre-match Flow transforms/checks → matching role →
   survivorship (which row's value won, via what strategy)`.

Net-new because it joins the *pre-match cleaning* (GoldenPipe/goldenflow) with the
*post-match survivorship* (goldenmatch) — a view neither engine produces alone.

## Non-goals

- **No reimplementation** of survivorship / cluster / pair provenance — reuse
  goldenmatch's `golden_provenance_for_run` and `ClusterProvenance`/`FieldProvenance`
  wholesale.
- **No kernel / Rust / cross-surface.** SP3 needs the Match *execution output*, so it is
  entirely host-side Python (unlike SP1/SP2's pure IR kernel functions).
- **No execution change.** Additive artifact + opt-in stitch.
- No row/cluster provenance UI/CLI (goldenmatch already has those).

## Architecture

### Part A — surface goldenmatch provenance (match adapter)

In `adapters/match.py`, after dedupe produces `clusters`/`golden`, call goldenmatch's
public, fail-open entry:
```
golden_provenance_for_run(data_df, clusters, rules) -> list[ClusterProvenance] | None
```
Inputs (VERIFIED against the real code — do NOT use the naive `ctx.df`/`config` sources):
- `data_df` = **`result.dupes`** — the frame carrying `__row_id__` (Int64) + value columns
  for the multi-member members that `clusters[*].members` reference. `ctx.df` does NOT
  carry `__row_id__` (goldenmatch adds it internally via `_add_row_ids` *after* the
  adapter hands off the frame), so passing `ctx.df` makes the internal
  `multi_df.join(data_df, on="__row_id__")` fail → fail-open → the artifact would be
  **perpetually `None`**. `result.dupes` is dtype-correct and already surfaced as
  `ctx.artifacts["dupes"]`.
- `clusters` = the `clusters` artifact (dict `{cluster_id: {members, size, ...}}`) from
  the classic `DedupeStage`. (The `FusedDedupeStage` sets `clusters = {cid: [row_ids]}` —
  a list, incompatible with `golden_provenance_for_run`'s `cinfo.get("size"/"members")`;
  SP3 is a silent no-op there, which is fine — the fused stage produces no golden.)
- `rules` = **`result.config.golden_rules`** (a `GoldenRulesConfig`, or `None`).
  `DedupeResult.config` is the *resolved* config present on ALL adapter paths (including
  Priority-0 throughput and Priority-3 auto-config, where the adapter holds no local
  `config`). Do NOT use the `GoldenMatchConfig` the adapter built — it's unavailable on
  those branches, and the survivorship rules live at `.golden_rules`, not top-level.

Attach `ctx.artifacts["golden_provenance"]` = the returned list (or `None`).

**Additive + byte-identical:** `golden_provenance_for_run` is fail-open (returns `None`
on any error), builds only new frames (no mutation of `data_df`/`clusters`), returns
`None` for non-survivorship configs and single-member-only clusters, and
`golden_provenance` is a NEW artifact key (the adapter + downstream `IdentityResolveStage`
read only `clusters`/`scored_pairs`/`matchkey_used`, never `golden_provenance`). So a
default pipeline is unchanged. Wrap the call in try/except so a lineage failure never
breaks the match stage (mirror the adapter's existing best-effort enrichment pattern).

### Part B — the stitch (compiler host)

New `goldenpipe/compiler/e2e.py`:
```
end_to_end_lineage(compiled: dict, golden_provenance: list | None) -> dict
    -> { "entries": [EndToEndField], "notes": [str] }

EndToEndField {
    cluster_id: int,
    column: str,
    # survivorship (from goldenmatch FieldProvenance)
    value, source_row_id: int, strategy: str, survivor_confidence: float,
    # plan lineage (from SP2 field_lineage)
    checks: [str], transforms: [str], blocking_key: bool, scorer_input: bool,
}
```

**Join = column name.** Compute `field_lineage(compiled)` (SP2) once → a `{column: {checks,
transforms, blocking_key, scorer_input}}` lookup. For each `ClusterProvenance` cp, for
each `(column, fp)` in `cp.fields`, emit an entry merging `fp`'s survivorship with the
SP2 lookup for that column (defaults `[]`/`False` when the column has no SP2 lineage —
e.g. an untransformed column). Entry order: cluster order, then `cp.fields` order.

**Join-key limitation (documented, honest):** the join matches goldenmatch's golden
column names (post-Flow) to SP2's IR column names. If Flow **renamed** or **split** a
column (`name` → `first_name`/`last_name`), the golden column won't match an SP2 IR
column, so the entry carries empty `checks`/`transforms` (`blocking_key=False`) — the
pre-match half is honestly absent for exactly the columns Flow reshaped most. SP1's IR op
set has no column-creating op today, so in practice this is limited to explicit split/
rename configs; note it as a known gap rather than silently implying full coverage.

- `golden_provenance is None` → `{"entries": [], "notes": ["survivorship inactive — use
  field_lineage(compiled) for the plan-only view"]}`.
- `format_end_to_end(result) -> str` — host presentation, one line per entry. The
  `strategy` is whatever survivorship strategy actually won (a survivorship-active one,
  e.g. a conditional/group rule — NOT `most_complete`, which is inactive), e.g.
  `cluster 1 email = 'j@x.com' (row 24 via conditional:corporate_domain); pre-match transforms[email_normalize], scorer-input`.

`ClusterProvenance`/`FieldProvenance` are dataclasses; the stitch reads them directly
(in-process). No serialization needed; goldenmatch's `_serialize_provenance` is available
if a caller wants JSON.

## Error handling

- Part A: try/except around `golden_provenance_for_run`; on failure attach `None` and
  log (never break the stage). Missing `clusters`/config → `None`.
- Part B: `end_to_end_lineage(compiled, None)` → empty entries + note. A `ClusterProvenance`
  missing `fields` → no entries for it (no crash). A column in provenance absent from SP2
  lineage → entry with empty `checks`/`transforms`, `blocking_key=False` (honest).
- The stitch never raises on well-formed inputs; it reads dataclass attributes that
  goldenmatch always populates.

## Testing

- **Unit stitch** (box): a hand-built `compiled` (email `Scan`/`Map` + `Partition` naming
  email) + a synthetic `ClusterProvenance(cluster_id=1, fields={"email": FieldProvenance(
  value="j@x.com", source_row_id=24, strategy="most_complete", confidence=1.0)})` →
  exactly one entry combining both halves (`source_row_id=24` AND `transforms=[...]`).
  And `golden_provenance=None` → `entries:[]` + the note.
- **`format_end_to_end`** unit (structured → expected string).
- **Real-pipeline proof** (box): a pipeline with an explicit **survivorship-ACTIVE**
  golden-rules config so `golden_provenance` populates. The config MUST trip
  `_survivorship_active` — i.e. `golden_rules` with `field_groups`, OR a list-form
  (conditional) `field_rules`, OR a rule carrying `when`/`validate_with`. A plain
  `most_complete` / dict `field_rules` will NOT (→ `None`). Pass it as the explicit match
  stage config (`StageSpec(use="goldenmatch.dedupe", config=<GoldenMatchConfig with
  golden_rules>)`). Then `compile_and_run`, run Part-A surfacing (via `result.dupes` +
  `clusters` + `result.config.golden_rules`), stitch, and assert a golden field's entry
  carries **both** `source_row_id` (goldenmatch) **and** the `email` `transforms` (SP2).
  **The unit stitch test is the PRIMARY join proof** (deterministic, synthetic
  provenance); the real-pipeline test's job is to prove Part-A surfacing yields non-`None`
  provenance + a non-empty stitch on a genuinely-active config — if the tiny-fixture
  survivorship setup is fiddly, keep the real-pipeline assertion to "non-None + stitched",
  not a specific survivor value.

## Rollout

Pure-additive, host-only. Part A surfaces a new `golden_provenance` artifact (`None`
when survivorship inactive → byte-identical default pipeline). Part B (`end_to_end_lineage`,
`format_end_to_end`) is an opt-in host function callers invoke on the `compile_and_run`
result. No kernel, no cross-surface, no execution change, no new kernel symbols.

## Scope boundary

SP3 is the **column/field-level** end-to-end journey (per golden field: its survivor row
+ pre-match transforms + matching role). It does NOT re-expose goldenmatch's cluster/pair
NL explanations or its lineage CLI/MCP (those exist). It adds exactly the cross-engine
stitch the compiler is uniquely positioned to produce.
