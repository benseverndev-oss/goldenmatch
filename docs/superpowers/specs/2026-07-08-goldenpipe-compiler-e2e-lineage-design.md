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
- `data_df` = the source frame carrying `__row_id__` (the frame dedupe scored — the same
  one whose row ids the clusters' `members` reference).
- `clusters` = the `clusters` artifact (dict `{cluster_id: {members, size, ...}}`).
- `rules` = the survivorship rules from the match `GoldenMatchConfig` the adapter used
  (the field-rules / survivorship config; the adapter already holds the config).

Attach `ctx.artifacts["golden_provenance"]` = the returned list (or `None`).

**Additive + byte-identical:** `golden_provenance_for_run` is fail-open (returns `None`
on any error), returns `None` for non-survivorship configs (`_survivorship_active` is
false) and when there are no multi-member clusters. So a default pipeline is unchanged;
the artifact simply appears when survivorship is active. Wrap the call in try/except so a
lineage failure never breaks the match stage (mirror the adapter's existing best-effort
enrichment pattern).

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

- `golden_provenance is None` → `{"entries": [], "notes": ["survivorship inactive — use
  field_lineage(compiled) for the plan-only view"]}`.
- `format_end_to_end(result) -> str` — host presentation, one line per entry, e.g.
  `cluster 1 email = 'j@x.com' (row 24 via most_complete); pre-match transforms[email_normalize], scorer-input`.

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
- **Real-pipeline proof** (box): a pipeline **with survivorship rules** so
  `golden_provenance` populates — `compile_and_run` the full `load→check→flow→match`,
  run Part-A surfacing (call `golden_provenance_for_run` with the run's frame/clusters/
  rules), stitch, and assert a golden field's entry carries **both** `source_row_id`
  (goldenmatch) **and** the `email` `transforms` (SP2). If standing up a survivorship
  config on a tiny fixture proves heavy, the unit test carries the join proof and the
  real-pipeline test asserts Part-A produces a non-`None` provenance + a non-empty stitch.

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
