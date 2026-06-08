# Quality-gated review routing (+ provenance) — design spec

Date: 2026-06-07
Branch: `claude/goldenmatch-quality-gated-review`
Intended PR base: `claude/goldenmatch-quality-survivorship` (#794) — reuses
`goldencheck.cell_quality`; once #794 merges, base on `main`.
Status: PROPOSED — spec for review before implementation.

**Doors #5 + #6** of the GoldenCheck → GoldenMatch map — the *trust/safety*
lever (doors #1/#794 = recall/results, #3 = precision). Unlike the other doors
it touches the **human-in-the-loop review queue**, not the tuned auto-config /
NE / blocking subsystems, so its blast radius is small.

---

## 1. Problem & hypothesis

GoldenMatch's review queue auto-merges a pair when its score is confident
(`gate_pairs`: `score > 0.95` → auto-merge, `0.75–0.95` → review, `< 0.75` →
reject — `core/review_queue.py:146`). The score measures **string agreement**.
It says nothing about whether that agreement is built on **trustworthy data**.

Failure mode: two records agree strongly on `name` + `city`, score 0.98, get
auto-merged — but `city` on one of them is a GoldenCheck-flagged fuzzy variant or
a future-dated record, i.e. the agreement is on a value we already suspect is
wrong. A confident merge on suspect cells is exactly the kind of silent false
merge a steward would want to *see* before it happens.

**Hypothesis:** routing high-score pairs built on GoldenCheck-flagged cells to
**review instead of auto-merge** catches false merges that score alone misses —
a precision/safety win at the cost of a few more review items, with **zero recall
cost** (it never rejects, only asks a human).

This reuses `cell_quality` (PR #794) directly — **no new GoldenCheck API**.

---

## 2. Design — a safety-only downgrade

`gate_pairs` gains an optional per-row quality signal:

```
gate_pairs(pairs, merge_threshold=0.95, review_threshold=0.75,
           row_quality=None, quality_floor=0.7)
  for (a, b, score):
    bucket = (by score, as today)
    # NEW: a would-be auto-merge on low-quality cells drops to review.
    if bucket == auto_merge and row_quality is not None:
        q = min(row_quality.get(a, 1.0), row_quality.get(b, 1.0))
        if q < quality_floor:
            bucket = review   # downgrade only; never upgrade
```

Invariants (this is the whole safety argument):
- **Downgrade-only.** auto-merge → review. A `review` or `reject` pair is never
  touched. So recall can't drop and a low-quality pair can't be auto-*accepted*.
- **Fail-open / opt-in.** `row_quality=None` (the default, and the value when
  goldencheck is absent or the flag is off) ⇒ `gate_pairs` is **byte-identical**
  to today.

`row_quality[row_id]` = the **worst** cell quality in that row (min over its
penalized cells; 1.0 if clean), from `cell_quality` mapped to `__row_id__`.
"Worst cell" is the right aggregation: one suspect cell in the matched record is
enough to warrant a look.

### Door #6 (provenance), folded in cheaply
When a pair is downgraded, attach the reason to the `ReviewItem`
(`explanation`/`why` already exist): *"auto-merge held for review: row 1188 has a
low-quality value in 'city' (fuzzy variant)."* So the steward sees **why** it was
surfaced, not just that it was. This is the minimal, cohesive slice of the
"explainable provenance" door — the same `cell_quality` signal, surfaced at the
decision point.

---

## 3. Architecture / data flow

```
core/quality.py::row_quality_floor(df) -> dict[int, float] | None   (NEW bridge)
   fail-open -> goldencheck.cell_quality(df), min weight per __row_id__.

review surfaces (cli/review.py, tui/app.py, core/agent.py):
   rq = row_quality_floor(df) if _quality_gated_review_enabled() else None
   gate_pairs(scored_pairs, ..., row_quality=rq)
```

- **Bridge:** `row_quality_floor(df)` mirrors `compute_quality_scores` /
  `blocking_risk` (fail-open, positional→`__row_id__`, min-per-row).
- **Gate flag:** env `GOLDENMATCH_QUALITY_GATED_REVIEW=1`, default OFF (the #662
  pattern); no dead config field.
- **Call sites:** the 3 `gate_pairs` callers compute `row_quality` (when enabled
  and the row-id'd frame is in scope) and pass it. They already hold the result
  frame; `df` with `__row_id__` is available via the engine result.

---

## 4. Changes (by file)

GoldenMatch only (GoldenCheck unchanged — reuses `cell_quality`):
- `core/quality.py::row_quality_floor(df)` — new fail-open bridge.
- `core/review_queue.py::gate_pairs` — optional `row_quality` / `quality_floor`
  params + the downgrade + the provenance string on downgraded items.
- `cli/review.py`, `tui/app.py`, `core/agent.py` — pass `row_quality` at the
  `gate_pairs` call when the flag is on.
- Docs: this spec + CLAUDE.md.

No new GoldenCheck API.

---

## 5. Test + measurement plan

This door changes *routing*, not match scores, so the gate is mostly behavioural
+ a precision-on-dirty-data check rather than a DQbench-composite gate.

Unit / behaviour:
- `row_quality_floor` fail-open (no goldencheck → None; clean → None) + min-per-row.
- `gate_pairs`: a 0.98 pair with a low-quality row downgrades to review; a clean
  0.98 pair still auto-merges; a 0.80 (review) pair is untouched; a 0.50 (reject)
  pair is untouched; `row_quality=None` ⇒ byte-identical buckets.
- Downgraded `ReviewItem` carries the quality reason.

Accuracy / value:
- On a fixture with planted false merges that hinge on a dirty cell (a fuzzy
  `city` variant shared by two different entities), assert the false merge lands
  in **review** (not auto-merge) with the flag on, and in auto-merge with it off.
- **No-regression:** the existing review/agent/TUI gating tests pass unchanged
  with the flag off (byte-identical).

Honest note: there's no labeled "auto-merge was wrong" benchmark wired today, so
the value is demonstrated on the planted-fixture + the mechanism, not a composite
number. The cost (extra review volume) is the per-row-quality penalty rate — tiny
on clean data (the flag's effect is proportional to how dirty the data is).

---

## 6. Default posture & risks

- **Default OFF** (env opt-in). When off: zero change. When on: only adds review
  items (never removes matches), so the downside is reviewer volume, not recall.
- **Risk — review-queue volume.** On very dirty data many auto-merges could drop
  to review. Mitigations: `quality_floor` is a knob (default 0.7 ⇒ only the worse
  half-penalized cells trip it); only auto-merge candidates are considered (a
  bounded set); the steward can raise the floor.
- **Risk — `__row_id__` availability.** Some review surfaces reconstruct the
  frame (`label.py` does when `_df` is None). The bridge no-ops (returns None →
  unchanged gating) when the row-id'd frame isn't available, so it degrades
  safely rather than mis-keying.
- **Low subsystem risk.** Touches `gate_pairs` + 3 thin call sites, not
  auto-config / NE / scoring. This is the spec's main attraction vs door #3.

---

## 7. Scope boundary

In scope: the auto-merge→review downgrade in `gate_pairs` + the provenance reason
(minimal door #6), behind a flag, fail-open.

Out of scope: the full explainable-provenance surface (lineage sidecar enrichment
with quality reasons across ALL survivorship decisions — the larger door #6);
changing match *scores*; door #2 (transform selection).

---

## 8. Implementation notes (as built, 2026-06-07)

Status: IMPLEMENTED (mechanism + tests). Built on `main` (#794 already merged, so
`cell_quality` is available).

- **Bridge:** `core/quality.py::row_quality_floor(df)` — worst cell quality per
  `__row_id__` (min over penalized cells), reusing `goldencheck.cell_quality`.
  Fail-open: `None` when goldencheck absent, `__row_id__` missing, or clean.
- **`gate_pairs`** gained keyword-only `row_quality` / `quality_floor=0.7` /
  `reasons`. A would-be auto-merge with `min(q_a, q_b) < quality_floor` drops to
  **review**; review/reject buckets are never touched. `row_quality=None` ⇒
  byte-identical (22 existing review-queue tests pass). `reasons` (optional out
  dict) carries the door-#6 provenance string for downgraded pairs.
- **Enablement:** `_quality_gated_review_enabled()` → `GOLDENMATCH_QUALITY_GATED_REVIEW=1`,
  **default OFF**. No config field (env-only, per the #795/#797 precedent).
- **Wired surface:** `cli/review.py` (the `goldenmatch review` steward loop) — it
  has the row-id'd `_df` cleanly. The downgrade reason is prepended to the
  `ReviewItem` explanation (door #6). **TUI (`tui/app.py`) + agent (`core/agent.py`)
  are NOT wired** — their `gate_pairs` calls don't have the row-id'd frame in
  scope; #5 degrades safely there (no `row_quality` → unchanged gating). Wiring
  them is a one-liner once their frame is threaded — a noted follow-up.
- **Measurement deferred:** the planted-false-merge accuracy fixture (§5) is the
  honest demonstration; there's no labeled "auto-merge was wrong" benchmark
  wired, so value is shown on the mechanism + fixture, not a composite.
- Tests: `tests/test_quality_gated_review.py` (8: downgrade, clean-stays,
  review/reject-untouched, None-identical, sparse-map, provenance, + bridge
  fail-open/flag).
