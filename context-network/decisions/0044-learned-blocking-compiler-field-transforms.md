# 0044 — Learned blocking lowers to `multi_pass` via per-field transform chains (compiler, currently inert)

**Status:** Accepted (compiler landed; wiring deferred — see status note). **Shipped:** goldenmatch 3.4.0 (PRs #1831 per-field transform chains, #1841 compiler + differential harness, #1840 throughput, #1838 drop-cap invariant).

## Context

Auto-config sets `strategy="learned"` for `total_rows >= 50_000`, but the bucket
scorer (`_use_bucket_scorer`, `core/pipeline.py`) refuses `learned` — so every
zero-config run at scale silently forfeited the 5-7x bucket scorer and fell to
the legacy per-block path (~400k Python frame ops at 1M). The gap is a
**representation** gap, not a semantic one: the bucket scorer derives candidates
from `blocking.passes/keys`, and a learned config carries neither, so relaxing
the gate alone would leave bucket with no keys.

A learned rule is a conjunction of `(field, transform)` predicates. Expressing
that requires the blocking key to hold a **different transform chain per field**
— which the key schema could not do (`transforms` applied uniformly to all
fields), so a mixed Splink rule like `last + zip5 + first-initial` collapsed to
initials or forced a lossy pass-split (splitting a conjunction into passes
*unions* the conditions, a massive widening).

## Decision

1. **Per-field transform chains as the key primitive (#1831, closes #1826).**
   `BlockingKeyConfig.field_transforms: dict[str, list[str]]` — a listed field
   uses its own chain, others keep the key-level `transforms`. Chosen over
   pass-splitting (unions conditions) and warn-harder (leaves the mega-block in
   place) because only per-field chains translate a conjunction **exactly**.
   `from_splink` now maps mixed `SUBSTR` rules exactly (per-field offsets that
   were dropped now convert); uniform rules keep the legacy key-level shape
   (goldens unchanged). Threaded through all six derivation sites + arrow
   `block_key(field_chains=...)` with polars==arrow parity pinned. TS
   `BlockingKeyConfig` / `fromSplink` don't know `field_transforms` yet (ignores
   it → finer blocks, no crash; port is a follow-up).
2. **A learned→`multi_pass` compiler (#1841).** `lower_rule_to_key` /
   `lower_rules_to_blocking_config` / `LoweringUnsupportedError`
   (`core/learned_blocking.py`) lower a learned config to a `multi_pass` key
   config the bucket scorer already honors — `learned → bucket? False; lowered →
   bucket? True`. It **refuses rather than lowering approximately** (two
   refusals: same-field conjunctions like `last:exact AND last:soundex` that
   `field_transforms` cannot hold, and unknown transforms) — because an
   approximate lowering moves only recall, silently, precision staying 1.0: the
   exact failure shape of #1800/#1837/#1839.
3. **Differential-harness discipline.** `scripts/learned_lowering_diff.py` +
   `tests/test_learned_lowering_parity.py` report *which records* are at stake,
   not just that sets differ — it caught a hand-written parity table getting the
   NULL case wrong. Tests split PARITY vs CHARACTERIZATION; residual divergence
   (empty-string / sentinel edges) is recorded, not resolved.

## Consequence

- **STATUS: the compiler is landed but wired to nothing** — #1841 is compiler +
  evidence only, no routing/default change, and the follow-up wiring (#1845) was
  closed as invalid. So zero-config learned runs at scale still take the legacy
  path today; the compiler sits inert pending a correct wiring PR. Record this
  before assuming learned→bucket is live.
- **The drop-cap-vs-selection-budget invariant (#1838).** Auto-config *selects* a
  learned key against `_compute_max_safe_block` (25,000 on 1M native, #1784) but
  the upgrade left `max_block_size` at the schema default 5000 and
  `apply_learned_blocks` **drops** blocks above it — so the selector accepted a
  key promising "blocks up to the budget are fine," then the runtime silently
  discarded them (1M recall 0.82, precision 1.0, fp 0, 358,860 true pairs lost).
  Invariant: `_learned_block_cap()` raises `max_block_size` to the same budget
  the key was selected against, **raise-only** (below ~200k the default already
  exceeds it, so those stay byte-unchanged), and `apply_learned_blocks` now WARNs
  on drops instead of eating them.
- **Throughput (#1840).** `apply_learned_blocks` deduped overlapping rules by
  materializing every candidate block to hash its `__row_id__` set;
  `frozenset(member_positions)` (already in hand, a bijection with `__row_id__`)
  is an equivalent key, dedup'd before building the LazyFrame — collects went
  `3 + 2*n_blocks` → flat 3 (~400k → 3 at 1M). Tests assert the *shape*, not a
  magic number.
- Multi-predicate `learned` null handling is part of the "missing is not a value"
  fix family (#1860, [0041](0041-fs-missing-value-semantics.md) /
  [0043](0043-bucket-default-fs-route.md)).
