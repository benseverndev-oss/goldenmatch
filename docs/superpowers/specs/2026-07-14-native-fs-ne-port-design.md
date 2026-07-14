# Native FS_SUPPORTS_NE Port (+ fused level_thresholds)

**Date:** 2026-07-14
**Status:** Approved (design)
**Thesis phase:** Rust/arrow-native port (phase 2) for FS negative evidence; TS/WASM surface is
phase 3, out of scope.
**Predecessors:**
- FS negative evidence core (`specs/2026-07-14-fs-negative-evidence-design.md`, PR #1764) -- the
  semantics being ported; its native/fused sections say "a future kernel port adds
  `FS_SUPPORTS_NE`".
- The N-level native port (PR #1752, goldenmatch-native 0.1.14) -- the playbook this port
  follows: optional kwargs old wheels never see, capability const, per-feature gate detection,
  real-kernel parity tests, 3-file version bump + wheel republish in the same rollout.
- The fan-out upgrade lever (`specs/2026-07-14-fanout-ne-upgrade-lever-design.md`, PR #1771) --
  now emits NE-bearing configs at migration time, so NE-bearing matchkeys are no longer a
  hand-authored rarity: they land on the pure-Python FS path today.

## Problem

`negative_evidence` on probabilistic matchkeys (#1764) declines the native Rust FS kernel
unconditionally (`_fs_native_eligible`) and the fused FS kernel (`match_fused_fs_ready`), so every
NE-bearing matchkey -- including everything the fan-out migration lever now produces -- runs on
the numpy/scalar fallback. The fused kernel additionally still declines custom `level_thresholds`
(the #1752 port only reached `score_block_pairs_fs`), so converted Splink configs (which usually
need N-level banding) can never use fused at all.

## Decisions (from brainstorming)

- **Scope: BOTH kernels** (`score_block_pairs_fs` + `match_fused_fs`), AND close fused's
  `level_thresholds` gap in the same release -- one 0.1.15 wheel brings `match_fused_fs` to full
  parity with `score_block_pairs_fs`. (User chose both over the score-only #1752-style scoping.)
- **FFI shape: Approach A -- flat parallel optional kwargs.** Python precomputes everything
  semantic (transforms, `w_fired` resolution); the kernel adds one dumb additive check per NE
  field. Rejected: pseudo-field encoding in the existing arrays (sentinel `levels=0` overloads
  the field arrays' meaning and breaks their validation); a structured dict/object param (PyO3
  extraction ceremony for no expressiveness gain).
- **Capability detection: two new module consts** -- `FS_SUPPORTS_NE` (NE in both kernels) and
  `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS` -- so each Python gate detects per-feature and old wheels
  keep declining exactly as today. `scripts/check_native_symbols.py` parses `m.add` consts since
  #1753; no gate-script change needed.

## Kernel: `score_block_pairs_fs` NE extension

Four new trailing optional kwargs, all defaulting `None` (crate
`packages/rust/extensions/native/src/score.rs`):

```
ne_values:     Option<Vec<Vec<Option<String>>>>   # per NE field, per row, POST-transform
ne_scorer_ids: Option<Vec<u8>>                    # score_one ids (same 0..=3 vocabulary)
ne_thresholds: Option<Vec<f64>>
ne_weights:    Option<Vec<f64>>                   # resolved w_fired (normally negative)
```

Upfront validation (PyValueError, mirroring the existing `level_thresholds` validation style):
all four present-or-absent together; equal lengths; each `ne_values[k]` length equals the row
count of the regular field arrays.

Inner loop, after the regular-field weight sum and before `fs_normalize`:

```rust
for k in 0..n_ne {
    if let (Some(a), Some(b)) = (&ne_vals[k][i], &ne_vals[k][j]) {
        if !a.is_empty() && !b.is_empty()
            && score_one(ne_scorer_ids[k], a, b) < ne_thresholds[k] {
            total_weight += ne_weights[k];
        }
    }
}
```

This is byte-for-byte `_ne_fired` (core/probabilistic.py:466): fires iff both values present
post-transform AND non-empty (empty string = inconclusive -- the deliberate NE null-handling that
differs from regular fields' null->level-0) AND similarity STRICTLY `<` threshold; contributes
exactly 0 otherwise. `fs_normalize` needs no change: the caller's `min_weight`/`weight_range`
already come from the NE-aware `fs_weight_range` (the `score_probabilistic_native` site was
centralized in #1764 precisely so it "can't drift" -- this port is where that pays off).

New const: `m.add("FS_SUPPORTS_NE", true)`.

## Kernel: `match_fused_fs` gets both gaps

Same four NE kwargs in Arrow form -- `ne_fields: Option<Vec<PyArrowType<ArrayData>>>` read via
the existing `StrCol` reader (Utf8/LargeUtf8), plus the three scalar vectors -- and a
`level_thresholds: Option<Vec<Option<Vec<f64>>>>` kwarg with the same validation
`score_block_pairs_fs` already performs (length == field count; `match_weights[f].len() ==
ts.len() + 1` per custom-banded field), threaded into the `fs_level_from_sim` call that today
hard-passes `None` (fused.rs ~line 269). NE firing uses the same both-present + non-empty +
strict-`<` rule on the `StrCol` values.

New const: `m.add("FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", true)`.

The two kernels stay drift-locked through the shared `fs_level_from_sim` / `fs_normalize` /
`score_one` (the property score.rs:179 documents).

## Python gates and callers

- **`_fs_native_eligible`** (core/probabilistic.py): the unconditional
  `if mk.negative_evidence: return False` becomes conditional -- eligible when EVERY NE field's
  scorer is in `_NATIVE_FS_SCORER_IDS` (an `ensemble`-scorer NE field, autoconfig's default pick
  for unknown columns, still declines the whole matchkey to the numpy path) AND the loaded module
  exposes `FS_SUPPORTS_NE`. Old wheels lack the const and decline exactly as today. The stale
  "NE never crosses the FFI" comments here and at the `fs_weight_range` call site are updated.
- **`score_probabilistic_native`**: when `mk.negative_evidence` is non-empty, build the NE args --
  values via the same `_field_values_for_block(block_df, ne, n)` used for regular fields
  (`NegativeEvidenceField` shares the `.field`/`.transforms` shape; `derive_from`-synthesized
  columns already exist on the block frame by this stage), `w_fired = -abs(ne.penalty_bits)` when
  set else `em.match_weights[f"__ne__{ne.field}"][0]` (`validate_for` guarantees the entry exists
  for penalty_bits-free NE; a missing entry raising KeyError here matches the scalar path's
  contract). The kwargs are sent ONLY when NE is present -- an old wheel must never see them even
  if the eligibility gate ever drifted (the #1752 discipline, restated in a comment).
- **`match_fused_fs_ready`** (core/fused_match.py): the two unconditional declines become
  per-feature capability checks -- NE allowed when `FS_SUPPORTS_NE` + every NE scorer native
  **+ no NE field uses `derive_from`**; `level_thresholds` allowed when
  `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS`. The derive_from decline exists because
  `run_match_fused_fs_arrow` takes a raw `columns` mapping and never runs
  `precompute_matchkey_transforms` (fused_match.py:330-334 builds `src_cols` from blocking keys
  + `mk.fields` only), so a derive_from-synthesized `ne.field` would not exist in the frame and
  NE would silently never fire -- declining keeps parity with the classic path, which handles
  derive_from upstream. (The Arrow-lane `derive_ne_joined` seam exists if a future change wants
  to synthesize instead; out of scope here.) Docstring rewritten to the new covered boundary.
- **`run_match_fused_fs_arrow`**: REPLACE the hand-rolled `max_w`/`min_w` sums (fused_match.py
  ~341-342, the known "unreachable for NE, cleanup candidate" copy) with
  `fs_weight_range(em_result, mk)` -- load-bearing now: the hand-rolled copy ignores `__ne__`
  entries and would mis-normalize every fused NE score. `src_cols` extended with the NE field
  names; NE value columns built through the same seam extraction + `_field_values_from_list`
  transform loop the regular fields use; both new kwarg groups threaded. An NE field absent from
  `columns` is unreachable given the gate (non-derive_from NE fields are data columns), but the
  prep degrades to all-null (never fires) rather than raising, matching the classic path's
  missing-column behavior.
- **`backends/score_buckets.py`**: no change -- the slim-projection keep-list already carries NE
  columns (#1764) and the bucket path routes through `probabilistic_block_scorer`, which picks
  the native scorer via the widened gate.

## Tolerance class (stated, not hidden)

A similarity landing exactly at an NE threshold can flip firing between rapidfuzz-rs and
Python-rapidfuzz -- the same documented boundary-tolerance class as FS level banding. Since the
#1752 reference-mode note, the native result is the DEFINED answer when native is on
(`GOLDENMATCH_FS_NATIVE=0` restores the reproducible numpy operating point). Parity tests use
fixtures whose similarities sit away from thresholds.

## Versioning + release discipline

`goldenmatch-native` 0.1.14 -> **0.1.15**, bumped in ALL THREE files in lockstep:
`packages/rust/extensions/native/Cargo.toml`, `packages/rust/extensions/native/pyproject.toml`
(the version maturin/publish actually reads -- these two have drifted before), and
`packages/rust/extensions/native/python/goldenmatch_native/__init__.py`. The wheel republish (tag
`goldenmatch-native-v0.1.15` -> `publish-goldenmatch-native.yml`) happens in the same rollout as
the merge: the Python caller gains capability-gated fast paths that every
`pip install goldenmatch[native]` env silently misses until the wheel ships (the #688
wheel-skew lesson). Tagging is Ben's call (or delegated post-merge). Rust CI runs clippy
`-D warnings` (run locally pre-push); rustfmt on touched files by name.

## Testing / success bar

- **Real-kernel parity** (in-tree build via `scripts/build_native.py` on this box;
  `tests/test_native_parity.py` conventions): native vs numpy identical pair sets + scores on
  NE-bearing matchkeys -- EM-learned and `penalty_bits`, fired / not-fired / null-on-either-side /
  empty-string-after-transform cases, NE combined with `level_thresholds`, multiple NE fields.
  Fixtures keep similarities away from thresholds per the tolerance discipline.
- **Fused parity**: `run_match_fused_fs_arrow` vs the classic pipeline on its covered boundary,
  now including NE-bearing and `level_thresholds`-bearing configs.
- **Gate tests**: module without the consts (monkeypatched) declines both gates; ensemble-scorer
  NE declines `_fs_native_eligible`; a `derive_from`-bearing NE field declines
  `match_fused_fs_ready` (but NOT `_fs_native_eligible` -- the classic path synthesizes it
  upstream); NE kwargs never sent when `mk.negative_evidence` is empty (spy on the native call);
  fused gate's per-feature independence (NE-supported + old level_thresholds and vice versa).
- **Success bar:** the FS-NE homonym E2E semantics (12/12 traps separate, 36/36 true dups merge)
  pass on the NATIVE path -- in-tree kernel, with an in-test assertion that
  `_fs_native_eligible(mk)` is True for the fixture's matchkey (so the test cannot silently fall
  back to numpy) -- and byte-identical clustering to the pure-Python run of the same fixture.
- Rust unit tests for the new validation errors; clippy `-D warnings` + rustfmt on touched files;
  `scripts/check_native_symbols.py` green (the two consts reconcile via the `_MADD` regex).

## Out of scope

- TS/WASM surface (thesis phase 3; must OPEN with a loud TS-side NE decline).
- TF-adjustment in the kernel (`tf_adjustment` fields still decline -- the kernel carries no
  per-value frequency tables).
- The numpy/scalar FS paths (byte-unchanged) and any scoring-semantics change.
- Continuous/Winkler path (already rejects NE).
- Autoconfig changes (NE promotion on probabilistic matchkeys remains migration-lever-only).
