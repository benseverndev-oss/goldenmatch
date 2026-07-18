# 0042 — Native kernel owns 100% Fellegi-Sunter scorer coverage (`fs-core`), numpy stays the reference fallback

**Status:** Accepted. **Shipped:** goldenmatch 3.4.0 (PRs #1869 `fs-core` extraction, #1871 embeddings = 100% coverage, #1808 perf primitive; #1876 cross-surface `date` scorer). Design note: `docs/superpowers/specs/2026-07-17-fs-core-cross-surface-extraction-design.md`.

## Context

FS block scoring had **three hand-synced implementations** — Python
numpy+scalar, the native pyo3 crate, and hand-written TypeScript — and any
matchkey whose scorer the native kernel lacked an id for silently forced the
**whole matchkey onto the numpy fallback**. That silent fallback is the failure
mode that made the person-shape scale bench look like a product problem when it
was a routing gap: the auto-configured person matchkey uses
`given_name_aliased_jw` / `name_freq_weighted_jw`, which the kernel could not
score, so "native" ran numpy. Parity-by-hand across three surfaces is also how
identical Python+Rust bugs pass a byte-identical parity gate (see
[0041](0041-fs-missing-value-semantics.md)).

## Decision

**One FS implementation; the Rust kernel is the source of truth; numpy is the
lossy-but-complete fallback and parity oracle.**

1. **`fs-core` extraction (#1869).** The FS math moves into a new **pyo3-free
   `goldenmatch-fs-core` crate** (`packages/rust/extensions/fs-core/`), consumed
   by both the native kernel and a new `fs-wasm` crate — parity is now
   by-construction, not by hand. #1869 also gives the kernel scorer ids for the
   scorers that previously fell back: the reference-data name scorers
   (`name_freq_weighted_jw`, `given_name_aliased_jw`), the Winkler
   `tf_adjustment`, and `ensemble` (id 6, valid as a regular and an NE scorer).
2. **100% coverage (#1871).** The last two — model-backed `embedding` /
   `record_embedding` (id 7, `embedding_cosine` = dot of L2-normalized vectors)
   — port to the kernel, so it now has an id for **every** FS comparison scorer:
   `jaro_winkler` / `levenshtein` / `token_sort` / `exact` (base),
   `name_freq_weighted_jw`, `given_name_aliased_jw`, `tf_adjustment`,
   `ensemble`, `embedding` / `record_embedding`.
3. **Data seams keep `fs-core` data-free.** An injected `SurnameFreq` /
   `NameAliases` provider backed by a **process-level** registry
   (`set_name_reference_data`, built once per process — no per-call marshaling);
   per-field `tf_freqs` / `tf_collision` kwargs; and for embeddings the model
   stays host-side (torch / Vertex / goldenembed) — only the already-normalized
   vectors cross the pyo3 boundary.
4. **numpy is retained, not deleted (#1871 defers increment 7b).** The
   pure-Python numpy+scalar path stays as the reference-mode **lossy fallback +
   parity oracle**. Deleting it (making native required) is *explicitly
   deferred* pending product sign-off, because `pip install goldenmatch` without
   `[native]` must still run FS. The routing posture: **the kernel declines
   rather than silently diverging** — `_fs_native_eligible` admits a scorer only
   when the wheel advertises the matching capability AND the backing refdata pack
   is loaded; anything else (or `disagree` missing-mode, or reference-mode
   `GOLDENMATCH_NATIVE=0`) routes to numpy.

## Consequence

- **The native path is now the authoritative + default scoring path and is
  100%-capable**, so with the wheel present it engages by default for
  auto-configured data (verified on person 3.4.0: all five field scorers native,
  `missing_mode="unobserved"`, no decline). The bench's forced-`FS_NATIVE=0`
  numpy lane is therefore a small-scale reference only — it cannot finish the
  auto-config candidate volume at 1M+.
- **Wheel-skew capability consts** (Rust advertises, Python probes; old wheels
  stay byte-identical on the Vec path): `FS_SUPPORTS_NAME_SCORERS`,
  `FS_SUPPORTS_TF_ADJUSTMENT`, `FS_SUPPORTS_ENSEMBLE` (#1869),
  `FS_SUPPORTS_EMBEDDING` (#1871), `FS_SUPPORTS_EXCLUDE_SET` /
  `FS_SUPPORTS_ARROW` (#1808), `date_similarity` symbol (#1876). This is the
  #688-class contract: a new kernel symbol benefits an env only once the wheel is
  **republished**. Native `0.1.16` (#1808), `0.1.18` (#1876).
- **#1808 is the enabling perf primitive** the bucket-default route
  ([0043](0043-bucket-default-fs-route.md)) rides on: the FS exclude-set is
  passed as an **Arc handle built once** rather than rebuilt per bucket call (the
  #552/#688 HashSet-rebuild pathology, previously fixed only for the weighted
  path), plus a zero-copy Arrow C-Data-Interface entry
  `score_block_pairs_fs_arrow`.
- **#1876 (`date` scorer)** is the cross-surface authoring pattern: canonical
  logic in the general `score-core` kernel (distinct from the FS-specific
  `fs-core` id space), funneled to native/Python/TS/wasm, with a measurement-led
  choice (Damerau-Levenshtein over the digit string).
