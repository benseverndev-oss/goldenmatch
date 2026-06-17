---
title: Full GroupProvenance surfacing end-to-end
date: 2026-06-17
status: design (approved in brainstorming; pre-spec-review)
owner: Ben Severn
related:
  - docs/superpowers/specs/2026-06-17-correlated-survivorship-and-conditional-golden-rules-design.md
  - docs/superpowers/plans/2026-06-17-correlated-survivorship-and-conditional-golden-rules.md
---

# Full GroupProvenance surfacing end-to-end

> v2 workstream 1 of 5 deferred by the correlated-survivorship v1 spec.
> Stacks on v1 (PR #1047, merging). Design-only until #1047 lands on main.

## 1. Problem

v1 (correlated survivorship) added lock-step `field_groups`: a set of
columns that promote together from ONE winning source record. The
resolver computes a rich `GroupProvenance` for each group, but that
provenance is **thrown away before it reaches any output surface**.
Confirmed against the v1 branch code:

- `GroupProvenance` (dataclass, `core/golden.py`) is fully modeled:
  `name`, `columns`, `strategy`, `winner_row_id`, `winner_source`,
  `values: dict`, `tie: bool`, `confidence: float`. `ClusterProvenance`
  has a `groups: list[GroupProvenance]` field.
- `resolve_cluster` (`core/survivorship/resolve.py`) builds the groups
  into a `ClusterProvenance(cluster_id=-1, ...)` placeholder and returns
  `(result, prov)`.
- **The surface discards it.** `build_golden_records_batch`
  (`core/golden.py` ~L783) has the only survivorship branch (~L884-897);
  it calls `resolve_cluster` but binds the prov to `_prov` and returns a
  list of row dicts (no provenance). The two provenance-producing entry
  points cannot recover the groups:
  - `build_golden_record_with_provenance` (~L1115) runs its OWN plain
    per-field `merge_field` loop and is **survivorship-blind** (no
    groups, ignores `when:`/`validate:`).
  - `golden_records_to_provenance` (~L1001) reconstructs
    `ClusterProvenance` from output rows (value + source_row_id), which
    structurally **cannot** carry groups.
- The NL renderers exist but are **dead-wired**:
  `render_group_provenance_line` and `render_cluster_provenance_nl`
  (`core/lineage.py` ~L321/L345) are defined and unit-tested
  (`tests/survivorship/test_lineage_nl.py`) but are **never called from
  `build_lineage`** (verified: no caller in the `goldenmatch` package).

Net effect: a user who configures `field_groups` gets correct golden
records but **no lineage, explain, MCP, or review-queue evidence** that
columns were promoted lock-step. The audit trail the whole feature
exists to provide is invisible.

Good news that shapes the design: `_serialize_provenance`
(`core/lineage.py` ~L133) is `[asdict(p) for p in provenance]`, and
`save_lineage(golden_provenance=...)` already writes a top-level
`"golden_records"` section from it (~L195). The pipeline already threads
`golden_provenance` in (`core/pipeline.py` ~L2118, gated on
`config.output.lineage_provenance`). So once the core wiring (A) makes the
provenance objects carry groups, the pipeline's lineage JSON carries them
with no caller change. The remaining surfaces each need one explicit edit
(NOT "free"): `cli/lineage` and the MCP `_tool_lineage` don't pass
`golden_provenance` today, `cli/explain --cluster` renders via
`explain_cluster_nl` (not lineage), and the NL renderer needs a caller.
Those are Sections 2 and 4.

## 2. Goals / non-goals

**Goals**
- The `GroupProvenance` that `resolve_cluster` already computes now reaches
  every Python consumer surface: the Python API (`GoldenRecordResult.provenance`),
  JSON lineage export, `cli/explain` + `cli/lineage`, the MCP `lineage` tool,
  and a new golden-composition view in the review queue.
  (`agent_explain_cluster` is deferred — a stateless stub, Section 4.)
- `resolve_cluster` is the **single source of truth** for survivorship
  provenance; downstream surfaces never re-derive groups from output rows.
- **Byte-identical output when no survivorship levers are used.** The
  non-survivorship provenance path is untouched; a parity gate enforces it.
- Conditional / validated `FieldProvenance` extras (`condition`,
  `validator`, `dropped_invalid`) ride the same wiring (they share the
  resolver and the dead NL renderer), so they surface together.
- Fail-open everywhere, matching the project posture: any rendering or
  serialization error degrades to today's behavior (record still emitted,
  audit line omitted), never a crash.

**Non-goals (called out inline where relevant)**
- **TypeScript parity.** The TS port has per-field survivorship strategies
  but NOT the field-groups / conditional / validated feature at all, so
  there is nothing in TS to populate a `GroupProvenance`. Deferred to a
  separate "port correlated survivorship to TS" workstream (large; its own
  spec). Recorded as a follow-up, not attempted here.
- Distributed-native group provenance (Ray/Sail). v1 already REFUSES
  survivorship on those paths (`assert_in_memory_survivorship`); this spec
  does not change that. Group provenance surfaces only on the in-memory
  builder, which is the only path that produces groups.
- New provenance *fields* on `GroupProvenance` / `ClusterProvenance`. The
  shapes are sufficient; this is plumbing + exposure, not modeling.
- `allow_fill`, anchor strategy, LLM-proposed groups (the other four v2
  workstreams).

## 3. Design overview

Two wiring fixes unlock everything; the rest is exposure + tests.

1. **A (core wiring):** make the live provenance path
   (`build_golden_records_batch(provenance=True)` ->
   `golden_records_to_provenance`) carry `resolve_cluster`'s rich
   `ClusterProvenance` (groups + conditional/validated fields) with a
   correct `cluster_id`. The pipeline already feeds that into the
   `save_lineage` `"golden_records"` JSON section.
2. **B (surfacing):** the structured `groups` ride the existing
   `"golden_records"` JSON section (via `asdict`); the dead
   `render_cluster_provenance_nl` gets a real home on the human surface
   (CLI cluster-explain) plus an optional per-cluster `"audit"` NL string
   in that JSON. `build_lineage` (per-pair) is NOT touched.
3. **C-F (exposure):** thread `golden_provenance` into the standalone
   `cli/lineage` + MCP `_tool_lineage` (they don't today); render the
   `Survivorship:` block in `cli/explain --cluster`; add the review-queue
   golden-composition view. (`agent_explain_cluster` is a stateless stub —
   out of scope.)
4. **G-H:** docs + the parity gate and end-to-end tests.

The single gate `_survivorship_active(rules)` (already used by
`_polars_native_eligible` and the batch builder) selects the new path
everywhere, guaranteeing the non-survivorship path is byte-identical.

---

## Section 1 — Core wiring (A)

### 1.1 `resolve_cluster` builds a correctly-stamped ClusterProvenance

`resolve_cluster(cdf, rules, order, ...)` gains two optional params:
`cluster_id: int | None = None` and `cinfo: dict | None = None`. When
provided, it stamps the returned `ClusterProvenance` with the real
`cluster_id`, `cluster_quality = cinfo.get("cluster_quality", "strong")`,
and `cluster_confidence = cinfo.get("confidence", 0.0)` instead of the
`cluster_id=-1` placeholder. Defaults preserve today's behavior (the
placeholder), so existing callers and tests are unaffected.

### 1.2 Route the provenance entry points through the resolver

**The one live provenance path** is the batch builder, and it already
flows to JSON: `build_golden_records_batch(provenance=True)` ->
`golden_records_to_provenance` (~L1001) -> `save_lineage(golden_provenance=...)`,
wired in `core/pipeline.py` (~L2118-2132, gated on
`config.output.lineage_provenance`). So fixing the batch path makes the
pipeline's lineage JSON carry groups with no caller change.

- **`build_golden_records_batch(provenance=True)` + `golden_records_to_provenance`
  (~L783 / ~L1001) — PRIMARY.** The batch survivorship branch (~L884-897)
  currently binds `_prov` and drops it. Change it to collect `prov`
  (built with the real `cluster_id`/`cinfo` threaded into `resolve_cluster`)
  alongside `rec`. `golden_records_to_provenance` returns those collected
  `ClusterProvenance` objects directly when survivorship was active, rather
  than reconstructing from rows. Reconstruction stays the path for
  non-survivorship configs -> byte-identical.
- **`build_golden_record_with_provenance` (~L1115) — thin delegation, NOT
  a second implementation.** This entry point has **zero production callers**
  (only `tests/test_golden.py`); the live path is the batch builder above.
  It is survivorship-blind today. Give it a thin survivorship branch that
  delegates to `resolve_cluster` (so the test-only API is correct for
  groups configs), but do not invest in a full second resolution path here.

`build_resolution_order(rules.field_rules, rules.field_groups, user_cols)`
is computed once per call (as the batch path already does) and passed to
each `resolve_cluster`.

### 1.3 Rejected alternative

*Reconstruct groups in the adapter* (`golden_records_to_provenance`
re-derives `GroupProvenance` from output rows + config): brittle, it
re-runs winner selection against already-merged output, cannot recover
`tie`/`confidence`/`winner_source` faithfully, and duplicates resolver
logic. Rejected in favor of resolve_cluster-as-source-of-truth.

### 1.4 Internal columns

`resolve_cluster` already strips internal columns; the surfaced
`GroupProvenance.columns` and `.values` contain only real user columns
(no `__row_id__` / `__cluster_id__` / `__source__`). Confirm in a test.

---

## Section 2 — Where group provenance surfaces (B)

**Correction from spec review:** `build_lineage` is the WRONG target. It
emits one record per scored PAIR (`scored_pairs`, keyed
`row_id_a`/`row_id_b`), has no per-cluster record, and takes no
provenance argument — it is "why two records matched", a different
concept. Leave `build_lineage` untouched.

Golden-record (survivorship) provenance has its own dedicated surface:
`save_lineage` / `save_lineage_streaming` (`core/lineage.py` ~L168/L202)
already accept `golden_provenance: list | None` and, when given, write a
top-level `"golden_records"` JSON key via `_serialize_provenance`
(`asdict`). Two carriers, structured + human:

1. **Structured (machine):** the `groups` array rides inside each
   `ClusterProvenance` in the `"golden_records"` section automatically once
   Section 1 makes `golden_records_to_provenance` carry groups — no
   serializer change. This is the carrier for JSON export + the MCP
   lineage tool. (It only *appears* where the caller passes
   `golden_provenance`; the pipeline already does, the standalone callers
   do not yet — Section 4.)
2. **Human (NL):** `render_cluster_provenance_nl` (`core/lineage.py` ~L345)
   is the single entry point — it composes `render_group_provenance_line`
   (~L321) for group lines and `render_field_condition_line` (~L328) for the
   condition/validation lines. All three are dead-wired (no caller in the
   package). Wire `render_cluster_provenance_nl` (the fail-open wrapper goes
   here, covering all three) into the human-facing surface — the CLI
   cluster-explain path (`core/explain.explain_cluster_nl`, Section 4) — and
   additionally emit
   the rendered NL as a per-cluster `"audit"` string alongside the
   structured `groups` in the `"golden_records"` JSON (a small addition in
   the `save_lineage` golden-provenance branch), so JSON consumers get both
   the structured array and a ready-to-read line.

The exact NL strings already live in the renderers (do NOT invent new ones):

- Group: `"{cols} promoted together from record {winner_row_id} via {strategy} (group '{name}')"`
  e.g. `"street, city, state, zip promoted together from record 7 via most_complete (group 'mailing_address')"`.
- Condition: `"{field} used {strategy} because {condition}"`.
- Validation suffix (only when `dropped_invalid > 0`):
  `" ({dropped_invalid} candidate(s) dropped by {validator})"`.

`render_cluster_provenance_nl` returns `''` for a cluster with nothing
survivorship-specific, so non-survivorship clusters add no lines (parity).
Wrap every render call so an error logs once and omits the line rather than
breaking the surface (fail-open).

---

## Section 3 — Serialization (C)

No serializer change for the structured array. `_serialize_provenance`
does `asdict(p)`, which recurses into `ClusterProvenance.groups` (list of
`GroupProvenance`) and the `GroupProvenance.values` dict, so once Section 1
makes `golden_records_to_provenance` carry groups, the `"golden_records"`
section gains a `groups` array per cluster with no serializer edit.
(`json.dumps(..., default=str)` already handles any non-JSON scalar in
`values`.) The only addition is the optional per-cluster `"audit"` NL
string (Section 2.2) in the `save_lineage` golden branch.

**Caveat (not "free" everywhere):** the `groups` array appears only where
the caller passes `golden_provenance` to `save_lineage`. The pipeline path
already does (`pipeline.py` ~L2118). The standalone `cli/lineage` and the
MCP `_tool_lineage` do NOT and must be updated (Section 4). Covered by a
round-trip test (Section 7).

---

## Section 4 — Explain CLI + MCP (D, E)

Each of these is an explicit wiring edit (none are truly "free" — spec
review corrected the earlier framing):

- **`cli/explain.py --cluster` (human NL).** This path calls
  `core.explain.explain_cluster_nl(cinfo, df, ...)`, NOT `build_lineage`.
  Compute the cluster's `ClusterProvenance` (via the batch builder /
  `golden_records_to_provenance`) and thread it into `explain_cluster_nl`,
  which appends a labeled `Survivorship:` block rendered by
  `render_cluster_provenance_nl`. This is the primary human surface for the
  group audit lines.
- **`cli/lineage.py` (JSON).** Currently calls `save_lineage(...)` WITHOUT
  `golden_provenance` (~L40-50), so it never emits a `"golden_records"`
  section. Thread `golden_provenance` (from `golden_records_to_provenance`)
  into that call so the JSON carries the structured `groups` array.
- **MCP `lineage` tool — `_tool_lineage` (`mcp/server.py` ~L1509).**
  Calls `build_lineage` + `save_lineage(...)` WITHOUT `golden_provenance`
  and never calls `_serialize_provenance`. Thread `golden_provenance` in so
  the tool's output (and any written file) carries the `"golden_records"`
  section incl. `groups`. **This is the MCP surface for group provenance.**
- **MCP `agent_explain_cluster` — out of scope (stateless stub).** The
  handler (`mcp/agent_tools.py` ~L533) returns only `{"cluster_id", "note"}`
  and explicitly has no run state / no `ClusterProvenance` access ("each MCP
  tool call is stateless; run agent_deduplicate first"). Surfacing groups
  there requires giving the tool stateful provenance access — a separate,
  larger change. Recorded as a follow-up; MCP coverage for this spec is the
  `lineage` tool above. (If a future run-context mechanism lands, a `groups`
  section keyed `name`/`columns`/`winner_row_id`/`winner_source`/`strategy`/
  `tie`/`confidence` is the intended shape.)

---

## Section 5 — review_queue golden-composition view (F)

`review_queue` is pair/correction-oriented (`ReviewItem` dataclass ~L22,
`gate_pairs` ~L157, `why_for_correction` ~L79); its provenance is "why
id_a and id_b matched", keyed by `(id_a, id_b)`. GroupProvenance is a
different concept (how the golden record for a CLUSTER was survived), so
this adds a NEW, optional view rather than overloading the pair string.

- `ReviewItem` gains `golden_composition: str | None = None` (defaulted,
  so existing construction is unaffected).
- When the review queue is built with access to the run's golden
  `ClusterProvenance` (the gating path that has it), populate
  `golden_composition` for the item's cluster by mapping the reviewed
  record -> its `__cluster_id__` -> that cluster's `ClusterProvenance`,
  rendered via `render_cluster_provenance_nl`. The reviewer then sees both
  "why these matched" (pair) and "how the surviving record was built"
  (composition, incl. group lock-step).
- Fail-open: no golden provenance available, or no cluster match ->
  `golden_composition` stays `None`; the review item is unchanged. This
  keeps the pair-review flow byte-identical when survivorship is off or
  golden provenance is not threaded in.

Spec-review question: the exact bridge (pair -> cluster). The reviewed
unit is a pair; the golden record is per cluster. The mapping requires the
cluster assignment for the records, which the gating path has post-
clustering. Confirm the gating call site that owns both the pairs and the
cluster `ClusterProvenance`, and pass the latter in (optional arg,
defaulted None = today's behavior).

---

## Section 6 — Docs (G)

Per the rollout-docs-sweep discipline, at the end:
- Golden-record / lineage docs (docs-site): document that `field_groups`
  survivorship now emits a group audit line and a `groups` array in
  lineage JSON; show the NL example.
- `docs-site/goldenmatch/tuning.mdx`: cross-reference from the
  `field_groups` / `GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP` entry to the new
  lineage output.
- MCP tool docs: note the `"golden_records"` (incl. `groups`) section now
  in the MCP `lineage` tool output.

## Section 7 — Testing (H)

Load-bearing test is **byte-identical parity**: a config WITHOUT
survivorship levers produces identical `GoldenRecordResult.provenance`,
identical `build_lineage` output, identical `_serialize_provenance` JSON,
and identical MCP/CLI output vs. a pre-feature baseline. Then:

- **Wiring (A):** a `field_groups` config -> `GoldenRecordResult.provenance`
  has populated `groups` with the **real** `cluster_id` (not -1),
  correct `winner_row_id`/`winner_source`/`tie`/`confidence`, and
  `columns`/`values` containing only user columns. Both entry points
  (`build_golden_record_with_provenance` and
  `build_golden_records_batch(provenance=True)` -> `golden_records_to_provenance`)
  return groups.
- **Surfacing (B):** the `"golden_records"` section's per-cluster `"audit"`
  NL carries the exact group line for a groups config, the condition line
  for a `when:` config, and the validation suffix when `dropped_invalid > 0`;
  a plain config emits NO survivorship output (and `build_lineage`'s
  per-pair output is unchanged — assert it byte-for-byte).
- **Serialize (C):** the `"golden_records"` JSON section round-trips a
  `groups` array per cluster (when `golden_provenance` is passed);
  `GroupProvenance.values` survives `asdict` + `json.dumps(default=str)`.
- **Explain / MCP (D, E):** `cli/explain --cluster` shows the
  `Survivorship:` block; `cli/lineage` and the MCP `_tool_lineage` emit a
  `"golden_records"` section with `groups` for a survivorship config and
  none for a plain config. (`agent_explain_cluster` is out of scope; assert
  it is unchanged.)
- **review_queue (F):** with golden provenance threaded in, a flagged
  item's `golden_composition` carries the cluster's survivorship trail;
  without it (or survivorship off), `golden_composition is None` and the
  item is byte-identical to today.
- **Fail-open:** a renderer raising does not break lineage / explain / the
  review item (monkeypatch the renderer to raise; assert the record/line
  still emits).

Test files: extend `tests/survivorship/` (e.g.
`test_provenance_surfacing.py`, `test_lineage_integration.py`) plus a
parity snapshot test mirroring the v1 parity-gate discipline.

## Section 8 — Rollout / flags

No new flags. Surfacing is governed entirely by whether the config uses
survivorship levers (the existing `field_groups` /
`field_group_detection` / `GOLDENMATCH_FIELD_GROUP_SURVIVORSHIP` opt-ins).
A config with no levers is byte-identical to today across every surface;
that is the rollout safety property and the parity gate enforces it.

## Section 9 — Open questions for spec review

Resolved during the first spec-review pass (recorded so the plan inherits
the answers): `build_lineage` is per-pair and not the golden-provenance
surface (use the `save_lineage` `golden_records` section instead, Section 2);
`build_golden_record_with_provenance` has zero production callers, so its
survivorship branch is thin delegation, not a second implementation
(Section 1.2); `agent_explain_cluster` is a stateless stub and is out of
scope (Section 4). Remaining:

- **`explain_cluster_nl` provenance plumbing (Section 4):** confirm
  `core.explain.explain_cluster_nl` can accept the cluster's
  `ClusterProvenance` (or that the CLI can cheaply compute it for the
  one requested cluster) to render the `Survivorship:` block, and that
  doing so does not perturb the existing non-survivorship cluster summary
  (parity).
- **Per-cluster NL key in `golden_records` (Section 2.2):** key name
  (`"audit"` proposed) and whether to emit it always or only when the
  cluster has survivorship content (proposed: omit when empty, for parity
  with plain configs).
- **review_queue bridge (Section 5):** the exact gating call site that
  owns both the pairs and the cluster `ClusterProvenance`, and whether the
  cluster assignment is already in hand there or must be passed.
