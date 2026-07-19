# FS Rust+Arrow-only — retire numpy/polars from the Fellegi–Sunter path

**Goal (owner directive, 2026-07-18):** a correctly-functioning FS needs neither
polars nor numpy — it is Rust + Arrow native end-to-end. Land goals 1–4 on `main`.

**Why:** the 1M `gm_probabilistic` OOMs trace to numpy still in the hot path — the
dense scorer builds O(block²) float64 matrices, and the EM trainer is numpy
end-to-end. The native Arrow scoring kernel + native clustering already exist; the
gap is (a) numpy is still a *runtime* scoring path, (b) EM training has no Rust
implementation (`bridge::train_em` calls back into Python), (c) blocking is polars.

**Provenance:** audit `docs/... fs-10day-audit` (this session); builds on the
#1803 "FS scale parity" epic. Two prerequisite fixes already on branch
`claude/benchmark-failure-gh-7h5ryr`: biblio megablock guard (9d765bc) + link-cut
off the neutral 0.50 point (84168dc, `bench-probabilistic` green).

## Current surface (where numpy/polars still live in FS)

- **Scoring dispatch:** `pipeline.py::_fs_use_bucket_route` chooses the native
  bucket kernel (`score_block_pairs_fs_arrow`, O(N)) **vs** `score_probabilistic_
  blocks_batched` → `score_probabilistic_vectorized` / `scorer._fuzzy_score_matrix`
  (numpy dense, O(block²) — the OOM). `GOLDENMATCH_FS_NATIVE` / `FS_DEFAULT_BUCKET`
  gate it.
- **EM training:** `probabilistic.py::train_em` — comparison matrix + E/M steps all
  numpy (`np.zeros`, `log_m`/`log_u`, `posteriors`). Rust `bridge::train_em` is a
  `prob.call_method("train_em", …)` shim → Python.
- **Blocking / candidate-gen:** `blocker.py`, `autoconfig.py` — polars.
- **Already native (Rust+Arrow):** `score_block_pairs_fs_arrow`, `build_clusters_
  arrow`, `connected_components_arrow`, `mst_split_components`, `dedup_pairs_arrow`,
  `build_block_index_arrow`; scorer reference `score-core::score_one`.

## Sequenced PRs (each gated; smallest safe increments)

### PR-A (goal 4) — bench discipline *[do first; trivial, unblocks signal]*
Drop / last-order the numpy `gm_probabilistic` lane from the ≥1M sweep so a known
non-native OOM can't mask the native result; make `gm_probabilistic_native` the
reported 1M number. **Gate:** `bench-er-headtohead` person 1M native leg completes.
**Rollback:** revert workflow input default. *(Premise check: the native-only 1M
run queued 2026-07-18 confirms native survives 1M before we lean on it.)*

### PR-B (goal 1) — native is the ONLY runtime FS scorer
Make `_fs_use_bucket_route` authoritative for every FS scoring call (route all FS
block scoring through `score_block_pairs_fs_arrow`); demote `score_probabilistic_
vectorized` / `_fuzzy_score_matrix` to **test-only parity oracles** (mirror the
"Rust is the reference" posture). Keep a loud `GOLDENMATCH_FS_NATIVE=0` escape hatch
for parity tests only. **Gates:** `bench-probabilistic` panel (F1 parity on
historical_50k/febrl3/dblp_acm/synthetic) + `bench-er-headtohead` 1M person/biblio
no-OOM + existing FS unit/parity suites. **Rollback:** env flag flips numpy back.
**Risk:** native/numpy scoring parity at boundary levels — the parity oracle test is
the guard.

### PR-C (goal 2) — EM training in Rust + Arrow  *[the big one]*
Port the E/M loop (comparison-vector build, u-from-random-pairs, m EM iterations,
match-weight `log2(m/u)`) into `score-core`/`native` over Arrow columns; expose a
real `train_em_arrow`. `bridge::train_em` stops delegating to Python. Python keeps a
byte-parity reference for tests only. **Design doc required first** (calibration is
the #1835/#1836 minefield — the per-pass conditioning + near-unique-u handling must
be reproduced exactly). **Gates:** byte-parity `EMResult` vs the Python reference on
a fixture matrix (incl. the #1836 near-unique-blocking case) + `bench-probabilistic`
+ auto-config quality gate. **Rollback:** keep Python `train_em` importable behind
`GOLDENMATCH_FS_EM_NATIVE=0` for one release.

### PR-D (goal 3) — Arrow-native blocking / candidate-gen  *[largest surface; last]*
Move FS candidate generation off polars onto the Arrow block-index path
(`build_block_index_arrow` already exists). Likely several sub-PRs. **Gate:** blocking
recall parity + the scale envelope. **Rollback:** per-stage flags.

## Order & rationale
A (signal) → B (kills the OOM, keeps EM in Python) → C (removes the last `import
numpy` from `probabilistic.py`) → D (removes polars). B delivers the crash fix and
most of the "no numpy in scoring" win; C is the calibration-heavy long pole; D is
mechanical but broad.

## Non-negotiable gates for every PR
`bench-probabilistic` panel green (per-dataset F1 parity) + `bench-er-headtohead`
no-OOM at 1M + FS unit/parity suites. Land via PR + merge queue (the FS calibration
gates are not in `ci-required`, so confirm them out-of-band before arming auto-merge).
