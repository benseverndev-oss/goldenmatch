# Bucket native as the default/suggested backend up to 750k (design)

**Date:** 2026-06-01
**Status:** design (approved, pre-plan)
**Decision context:** 2026-06-01 re-prioritization. The Arrow-native polars-direct
columnar work (Phase 1) is paused. The average user dedupes ~200k-row CSVs where
polars-direct is fine, and bucket+native is the proven scale path (1.7-3.7x faster
1k-60k; the production winner at 5M-25M). Goal: make **bucket native the
default/suggested backend up to 750k rows** for the average user.

## Problem

The v3 planner ALREADY selects bucket when the native kernel is present:
`core/autoconfig_planner_rules.py::_scoring_backend()` returns `"bucket"` iff
`native_enabled("block_scoring")` (signed off), else `"polars-direct"`. `rule_simple`
(<100k) and `rule_fast_box` (>=100k, pairs fit RAM, >=32GB) both call it.

So the planner threshold is NOT the blocker. Two real gaps:

1. **Native isn't installed for the average user.** `goldenmatch-native` (the
   compiled abi3 kernel, polars-runtime split) is behind the `[native]` EXTRA, not
   a core dependency. `pip install goldenmatch` is pure-Python by design ->
   `native_enabled` is False -> polars-direct. (Unlike Polars, which ships the
   compiled wheel AS the main package.) The wheels DO exist: `goldenmatch-native`
   0.1.0 is published on PyPI with abi3 wheels for macOS x86_64 + arm64, Linux
   x86_64 + aarch64, and Windows amd64.
2. **The fast_box 32GB floor** blocks bucket on 16GB boxes in the 100k-750k band,
   even when the pairs would fit in RAM -- so an average 16GB user at 200k-750k can
   fall through to chunked/distributed instead of bucket.

## Decisions (approved)

1. Native available by default via a **platform-marker-guarded core dependency**
   (auto-install where abi3 wheels exist; wheel-less platforms stay pure-Python).
2. "Up to 750k" = a **planner guarantee that keeps RAM safety**: waive the blanket
   32GB floor for the <=750k band but keep the per-dataset pair-memory-fit check.
3. Bench-validate bucket vs polars-direct at 200k/500k/750k before trusting the
   recommendation (we only have 1k-60k data).

## Section 1: Native as a marker-guarded default dependency

Move `goldenmatch-native` from the `[native]` optional extra into core
`dependencies` in `packages/python/goldenmatch/pyproject.toml`, guarded by PEP 508
markers limiting it to the platforms with published abi3 wheels:

```toml
dependencies = [
  ...,
  "goldenmatch-native>=0.1.0; sys_platform == 'darwin' or (sys_platform == 'win32' and platform_machine == 'AMD64') or (sys_platform == 'linux' and (platform_machine == 'x86_64' or platform_machine == 'aarch64'))",
]
```

- On the 5 covered platforms (~95% of users) `pip install goldenmatch` now pulls
  the kernel; the loader (`_native_loader.py`) uses bucket under the default
  `GOLDENMATCH_NATIVE=auto` (block_scoring is signed off) -> bucket auto-selects.
- **musl wrinkle (must handle):** PEP 508 markers can target OS + arch but NOT
  glibc-vs-musl, so the `linux` marker also matches Alpine/musl, which has no
  compatible wheel -> pip would attempt to build the sdist (needs Rust) and could
  fail the INSTALL. Resolution: **publish a `musllinux` abi3 wheel** in the
  `goldenmatch-native` publish matrix (preferred -- keeps the marker honest). If a
  musllinux wheel is out of scope for now, the fallback is to document that Alpine
  users install without native (and the loader's runtime fallback handles a missing
  kernel gracefully -- the only risk is install-time, not runtime).
- **uv monorepo safety:** keep `[tool.uv.sources] goldenmatch-native = { workspace
  = true }` (or the path source) so dev/CI resolve the local crate, not PyPI -- per
  the CLAUDE.md gotcha that an extra pointing at a not-yet-published package broke
  `uv sync --all-packages` repo-wide. Verify `uv sync --all-packages` resolves
  after the move.
- Keep `[native]` as a **back-compat no-op extra** (e.g. `native =
  ["goldenmatch-native>=0.1.0"]` still listed) so existing
  `pip install goldenmatch[native]` and docs referencing it keep working.

## Section 2: Planner -- bucket suggested up to 750k, RAM-safe

In `core/autoconfig_planner_rules.py`, add `BUCKET_SUGGESTED_MAX_ROWS = 750_000`
and relax `_is_fast_box_eligible` so the 32GB floor is WAIVED for the <=750k band
while the per-dataset pair-memory-fit check stays:

```
fast_box eligible if:
  n_rows >= SIMPLE_PLAN_MAX_ROWS (100_000)
  AND <estimated pair memory fits available RAM>          # the safety guard
  AND (available_ram_gb >= FAST_BOX_MIN_RAM_GB (32)  OR  n_rows <= 750_000)
```

Behavior:
- **<=750k on any box where the pairs fit RAM -> bucket** (the "suggested up to
  750k" guarantee, now reaching 16GB users).
- A <=750k run whose pair set will NOT fit RAM -> routes to chunked (the RAM-fit
  check prevents OOM).
- **>750k unchanged:** big boxes keep getting bucket via the existing 32GB fast_box
  (preserves the proven 5M-25M bucket scale story -- bucket is NOT capped at 750k);
  small boxes route to chunked/distributed as before.

**Implementation guard (CONFIRMED ABSENT -- must ADD, not preserve):** code
review found `_is_fast_box_eligible`'s ONLY RAM guard is the 32GB floor. The
existing `estimated_pair_count < SIMPLE_PLAN_MAX_PAIRS` (50M) check is a
pair-density proxy, NOT a RAM-fit check -- it does not scale by
`available_ram_gb`, so 49M pair-score tuples would plausibly OOM a 16GB box. The
implementation therefore MUST ORIGINATE an explicit
`estimated_pair_bytes <= available_ram_gb * SAFETY_FACTOR` check for the relaxed
<=750k band (treat as a real new feature, not a verification step). The RAM-fit
guard is non-negotiable -- it is what distinguishes this from a blanket relax and
prevents OOM on 16GB. Pick `SAFETY_FACTOR` conservatively and bench it.

`GOLDENMATCH_PLANNER_BUCKET=0` opt-out continues to force polars-direct throughout.

## Section 3: Validation, docs, telemetry, testing

**Validation (de-risk the 750k claim).** We have bucket-vs-polars data only at
1k-60k (1.7-3.7x). Before finalizing the ceiling, run `scripts/bench_native_bucket.py`
(or `bench_fs_and_stages.py`) on `large-new-64GB` at **200k / 500k / 750k**,
bucket+native vs polars-direct, recording wall + peak RSS + identical-cluster
parity. If bucket wins across the band, ship 750k; if it stops winning at e.g.
500k, lower `BUCKET_SUGGESTED_MAX_ROWS` to the evidence. **The 750k value is
PROVISIONAL until this bench confirms it -- the plan must make the bench a HARD
GATE on the constant, not advisory: either land the planner relaxation behind the
constant and SET the constant only after the bench (a follow-up commit), or ship
the relaxation and constant together in the same PR as the bench run that
justifies the number. Do not merge a 750k ceiling that no bench has confirmed.**

**Validation RESULT (2026-06-01, run 26781636345, `bench-fs-stages` on
`large-new-64GB`, `bench_fs_and_stages.py --ns 200000,500000,750000 --runs 1`):
GATE PASSED. 750k confirmed; constant stays `BUCKET_SUGGESTED_MAX_ROWS = 750_000`.**

| N       | polars-direct | bucket+native | speedup | clusters identical |
| ------- | ------------- | ------------- | ------- | ------------------ |
| 200,000 | 10.75s        | 2.04s         | 5.28x   | True               |
| 500,000 | 24.97s        | 5.57s         | 4.48x   | True               |
| 750,000 | 37.49s        | 8.05s         | 4.66x   | True               |

`native ext importable: True`; `_resolve_fast_path` engaged (2x jaro_winkler,
threshold 0.85). bucket+native wins 4.5-5.3x across the whole band with
byte-identical clusters at every scale -- bucket is still winning 4.66x at 750k,
so the ceiling is conservative (big boxes already keep bucket above 750k via
`fast_box`). Pair counts were modest (54k / 136k / 203k) -- well within the RAM-fit
guard. Note: this bench captures wall + parity but not peak RSS; the planner's
`est_pair_gb <= available_ram_gb * 0.5` guard is what bounds RSS, and the pair
counts here are orders of magnitude under any 16GB ceiling.

**Docs.**
- Scale-envelope / backend-selection docs: "bucket+native is the default-installed,
  suggested backend up to 750k rows; above that -> chunked/distributed."
- README / install: `pip install goldenmatch` now pulls native acceleration on
  common platforms (no `[native]` needed). Opt out with `GOLDENMATCH_NATIVE=0` or
  `GOLDENMATCH_PLANNER_BUCKET=0`.

**Telemetry.** The planner already surfaces `backend` + `rule_name` via
`serialize_telemetry`. The relaxed-band selection gets a distinct `rule_name` (e.g.
`plan_selected_bucket_suggested`) so it is visible WHY bucket was picked at <=750k
on a sub-32GB box.

**Testing.**
- *Packaging:* `goldenmatch-native` is a core dep with the platform markers;
  `uv sync --all-packages` resolves; `[native]` still works as a no-op alias.
- *Planner unit tests* (mock RuntimeProfile + native flag): 16GB + 300k that fits
  RAM -> bucket; 16GB + 300k whose pairs do NOT fit -> chunked (no OOM); 16GB + 1M
  (>750k) -> not bucket; 32GB + 1M -> bucket (fast_box unchanged); native-absent ->
  polars-direct (the `_scoring_backend` fallback).
- *musllinux:* if the wheel is published, confirm the publish matrix builds it;
  either way a doc/test that Alpine degrades to pure-Python gracefully.

## What this does NOT do

- Make bucket the backend ABOVE 750k on small boxes (chunked/distributed still owns
  that), or cap bucket at 750k on big boxes (the 5M-25M bucket story is preserved).
- Resume the polars-direct Arrow-native columnar work (paused; PR #666 holds the
  Phase 1 gate code for a later circle-back).
- Change the `GOLDENMATCH_NATIVE` sign-off model or the loader's discovery order.

## References

- Planner: `core/autoconfig_planner_rules.py` (`_scoring_backend`, `rule_simple`,
  `rule_fast_box`). Loader: `core/_native_loader.py`.
- Native package: `goldenmatch-native` 0.1.0 on PyPI (abi3 wheels, 5 platforms);
  crate at `packages/rust/extensions/native/`.
- Prior evidence: bucket+native 1.7-3.7x vs polars-direct 1k-60k (PR #526);
  25M-on-one-node bucket 6.5min/57.7GB (run 26095134836).
- Related memory: bucket native scoring win; goldenmatch-native package
  (multi-platform publish was an outstanding item); controller v3 planner.
