# GoldenMatch Polars Eviction -- Own the Rust/Arrow/Fusion Stack

- **Date:** 2026-07-09
- **Status:** Approved (design); implementation planned wave-by-wave
- **Driver:** Full in-house ownership of the data plane (Arrow) and compute plane
  (owned Rust kernels), mirroring the completed goldenflow and in-flight
  goldencheck evictions. Secondary driver: install footprint (~185MB, the
  figure the sibling eviction programs use).
- **End state (user decision):** ZERO Polars anywhere in the goldenmatch
  package -- not an optional `[polars]` accelerator extra, not a decline class.
  Every module ports.

## 1. Motivation and thesis

GoldenMatch already owns its hot compute path in Rust over Arrow:

- `match_fused` -- block + score + dedup + cluster in one FFI call; takes
  `dict[str, pa.Array]` in, returns cluster ids. No Polars inside.
- `golden_fused` -- clusters -> golden-record indices over Arrow. No Polars
  inside.
- `score-core`, `graph-core`, `fingerprint-core`, the goldenflow transform
  kernels -- the scoring/standardize/transform layer largely exists in owned
  Rust already.

Polars' remaining role is glue between owned kernels: file IO, relational
plumbing (joins/group_by/partition_by), and an expression long tail in the
controller/profiling code. Each of those decomposes onto things we either
already ship (pyarrow is a base dep and the kernel FFI boundary) or can own.

The thesis: **Arrow is the data plane, owned Rust kernels are the compute
plane, and Polars is replaceable glue.** This is the same "Rust is the
reference" direction the suite committed to on 2026-07-01
(`docs/design/2026-07-01-rust-is-the-reference-roadmap.md`), applied to the
substrate itself.

### Why goldenmatch is harder than goldenflow/goldencheck

Those packages use Polars as a *calculator* (per-value transforms, per-column
reductions), so a scalar-op seam sufficed. GoldenMatch uses Polars as a
*relational engine*: exact matching is a self-join, blocking is a group_by,
golden is a partition_by, ingest is a lazy scan. The counter-move is the fused
kernel program (above) plus `pyarrow.compute` (which has hash joins and
group_by) for the cold glue. The port is bigger -- 124 of 304 package files
import polars; the densest are `distributed/clustering.py` (72 refs),
`core/pipeline.py` (70), `core/scorer.py` (57), `core/golden.py` (55),
`core/blocker.py` (44) -- but it is the same seam pattern, executed in waves.

## 2. Decisions locked

| Decision | Choice | Rationale |
| --- | --- | --- |
| End state | Zero Polars anywhere | Full ownership; no decline class, no accelerator tail. Everything ports; only ordering varies. |
| Substrate | Owned Frame/Column seam backed by `pyarrow.Table` | The proven goldencheck/goldenflow pattern; port is mechanical file-by-file; substrate swappable later (e.g. arro3) without re-touching 124 files. |
| Public API | Arrow-native results; semver major (v3.0.0) | Inputs stay polymorphic (any Arrow C stream object: polars, pandas, duckdb -- zero-copy, callers unchanged). `DedupeResult`/`MatchResult` frame fields become `pa.Table`. Polars users migrate with `pl.from_arrow(result.golden)` (zero-copy one-liner). The repo executed a clean 2.0.0 break before. |
| Fallback contract | Pure-Python lane becomes Arrow + Python | Today's "pure-Python fallback" for the pipeline IS Polars. Post-eviction, no-wheel platforms run pyarrow.compute glue + Python scoring: correct but slower. Consistent with the reference-mode thesis (Rust reference, lossy fallback); the native wheel already covers win/mac/linux x86_64+aarch64. Documented plainly, not hidden. |

Out of scope (explicitly retained): `pyarrow` (the data plane and kernel FFI
boundary), `duckdb` (a feature backend, not glue), the TypeScript port (own
engine), and any attempt to reproduce a Polars-style expression engine.

## 3. Program shape: two arms, six waves

The fused kernels are the existence proof but not the whole engine: they
DECLINE to None on configs they do not cover (validators, plugins, LLM
survivorship, adaptive, memory, identity, ...) and fall back to the classic
pipeline -- which is Polars. So the program has two arms working toward each
other:

1. **Expand fusion downward** -- grow `match_fused`/`golden_fused` config
   coverage so more runs never touch classic glue (continues the existing
   fusion program).
2. **Port the classic glue upward** -- move the remaining relational ops onto
   the Frame seam, backed by `pyarrow.compute`, promoting measured hot spots
   into owned kernels.

### Waves

Each wave ships independently, parity-gated, branched off fresh origin/main
(one wave merges before the next branches -- the goldencheck stacking lesson).

| Wave | Content | Polars status after |
| --- | --- | --- |
| W0 | Four deliverables: (1) lazy-import linchpin (goldenflow's `_LazyPolars` proxy, all 124 imports); (2) `core/frame.py` seam scaffold with a delegating PolarsFrame backend; (3) the semantic-op audit (section 4.1); (4) the module-level dtype-constant enumeration (section 7) | Present, but `import goldenmatch` no longer loads it |
| W1 | Arrow IO (`core/io_arrow.py`: pyarrow csv/parquet; openpyxl for Excel) + ArrowFrame backend + env-gated ingest lane `GOLDENMATCH_FRAME=arrow` + the differential CI harness and frozen pre-port parity fixtures. Pre-W2, arrow mode converts once at the ingest boundary (`pl.from_arrow`) and the engine runs classic; configs the fused kernels decline fall back to the Polars classic path with a logged notice. | Default engine still Polars |
| W2 | Classic engine port: blocker/scorer/cluster/golden/standardize/matchkey onto seam ops; measured hot spots become kernels; fused coverage widened in parallel; ALSO ports the fused-kernel prep derivation (`_build_block_key_expr`, `_get_transformed_values`, `fused_match.py`'s internal `pl.DataFrame`) so the covered-config spine runs polars-free end-to-end -- moved here from W1 by post-W0 recon (the prep is expression glue, W2's class). | Core engine dual-backend |
| W3 | Controller/autoconfig front: profiling, indicators, complexity signals -- mostly column reductions (goldencheck-shaped; many map onto already-proven seam ops) | Controller dual-backend |
| W4 | Tails: distributed (Ray `map_batches(batch_format="pyarrow")` -- Ray's object store is natively Arrow), chunked, db/identity, web, TUI engine, MCP/A2A handlers, in-repo downstream consumers | Polars unreferenced |
| W5 | The flip: `polars` removed from dependencies, PolarsFrame + `GOLDENMATCH_FRAME` deleted, test assertions migrated to Arrow, ship v3.0.0 | ZERO |

W0-W4 ship as 2.x minor releases; users see no behavior change (Polars remains
the default backend, Arrow is env-gated experimental). W5 is the major.

**W1 scope note (2026-07-10):** W0 recon found `run_match_fused_arrow`'s prep
derives block keys and transformed score columns via Polars expressions,
which this spec assigns to the classic-glue class. The polars-free spine
therefore lands with W2's expression-glue port; W1 delivered the Arrow IO
front, the ArrowFrame backend, and the differential harness.

## 4. The Frame seam

### 4.1 Semantic ops, never an expression DSL

Pipeline Polars usage looks like
`pl.col(x).cast(pl.Utf8).str.strip_chars() != ""` -- chains of a DSL the seam
must NOT reproduce (rebuilding an expression engine is how this program dies).
Instead, each call site's *intent* becomes one named op:

- `frame.filter_nonnull_nonempty(col)`
- `frame.concat_str(cols, sep)`
- `frame.self_join_on(key)` (suffix semantics pinned to the exact-match join)
- `frame.partition_by(col)`
- `frame.group_by_len(keys)`
- column-level: `col.cast_str()`, `col.null_count()`, `col.n_unique()`, ...

Ops are derived from an **op audit of actual usage** (a W0 deliverable), not
designed up front, and added wave-by-wave as call sites port -- exactly how the
goldencheck seam grew (`str_match_count`, `value_counts_desc`, ...). Estimate
40-80 semantic ops total; each is small, testable, and implemented twice.

### 4.2 Two backends, differential CI, one dies at W5

- **PolarsFrame** -- delegates each op to the exact Polars call it replaced
  (byte-identical by construction). Default through W4; regression protection
  for the existing 1300+ tests, which keep running UNEDITED against it.
- **ArrowFrame** -- `pa.Table` + `pyarrow.compute`, with `to_arrow()` /
  `to_arrow_columns()` as the kernel FFI boundary (the fused kernels already
  consume `dict[str, pa.Array]`; that interface is unchanged).
- Selection: `GOLDENMATCH_FRAME` env, default `polars` until W5. `to_frame()`
  coercion is idempotent at every public entry point.
- **Differential CI lane:** runs the suite's engine-relevant paths under both
  backends and diffs canonicalized outputs (section 5). This is what makes each
  wave safe to merge. Also records wall + peak RSS per backend.
- At W5, PolarsFrame, the env var, and the differential lane are deleted
  together; the test suite's assertions migrate to Arrow (acknowledged W5
  work, not a hidden cost).

### 4.3 Kernel-vs-glue routing rule

Glue ops (join/group_by/filter/concat) start as `pyarrow.compute` behind the
seam -- the cheapest correct implementation. Any op that regresses more than
10% on the standing benches moves into `goldenmatch-native` as an owned Arrow
kernel instead of shipping slow. W2 also widens fused-kernel config coverage,
so a shrinking share of runs touches classic glue at all. End-state hot path:
pyarrow IO -> fused kernels -> Arrow out, with pa.compute serving only
cold/exotic configs.

### 4.4 IO layer (`core/io_arrow.py`)

`pyarrow.csv` / `pyarrow.parquet`; Excel goes through openpyxl directly
(Polars was already delegating to it). Two known semantic gaps tracked as
risks from day one:

- Polars' `utf8-lossy` has no direct pyarrow equivalent -- dirty files
  (Leipzig latin-1, junk rows) need a decode-with-replace strategy (read as
  binary + errors="replace" decode pass, or pyarrow ReadOptions encoding where
  sufficient).
- Dtype inference differs between readers (Polars reads zips as Int64 -- a
  documented gotcha call sites already `str()` around). Ingest parity gets its
  own fixture corpus (section 5.1).

Small consequences on the list: `DedupeResult._repr_html_` re-renders from
Arrow; `write_csv`/`write_parquet` output paths move to pyarrow writers.

## 5. Parity contract

Byte-identity across engines is unattainable (row order, dtype inference,
cluster-id assignment order), so the gate is **canonicalized equivalence**,
pinned in dependency order:

1. **Ingest parity FIRST.** A fixture corpus runs both readers over every
   standard test file and diffs values + canonicalized dtypes. If the readers
   disagree, nothing downstream is comparable; fixes land at the IO layer, not
   papered over later.
2. **Cluster partitions** compare as sets-of-frozensets of row ids
   (order-free).
3. **Golden records** compare as value maps keyed by sorted cluster members.
4. **Scored pairs** compare as canonical `(min, max, score)` sets. Scores must
   match exactly -- both backends feed the same kernels/rapidfuzz once ingest
   parity holds.

Pre-port outputs are frozen as serialized fixtures; the Arrow backend must
reproduce them. The existing test suite runs unedited against the Polars
backend through W4; the differential lane covers Arrow.

## 6. Performance gates

- The standing 100K zero-config <= 24s CI gate stays green on both backends.
- The 1M and 25M benches re-run on every wave that touches engine ops.
- The differential lane records peak RSS alongside wall (RSS is a tracked
  workstream; Arrow should be leaner, but this is measured, not promised).
- Binding rule: > 10% wall regression on any ported op -> owned kernel, not
  shipped slow.
- Named suspects to measure EARLY (W1/W2): exact-match self-join at 1M+
  (Polars' join is excellent; `pa.Table.join`/Acero must prove itself),
  blocking group_by, and the distributed columnar WCC (Phase B was
  memory-tuned on Polars frames -- re-verify the 5M chain bench; Ray plasma is
  natively Arrow so this is expected to get cleaner, but expected != measured).

## 7. Risk register

| Risk | Mitigation |
| --- | --- |
| Join null-key semantics differ between Polars and Acero | Dedicated semantics fixtures pinned before the exact-match port; behavior documented in the seam op's contract |
| `utf8-lossy` dirty-CSV handling | decode-with-replace strategy in io_arrow; Leipzig latin-1 + junk-row files in the ingest parity corpus |
| Dtype inference drift (Int64 zips etc.) | Ingest parity corpus is gate #1; canonicalization rules live in one module |
| Module-level dtype constants break the lazy-import proxy | goldencheck hit 7; expect more here. W0 audit enumerates them; each becomes an lru_cache function (proven pattern) |
| Polars expression long tail in controller/profiling | Semantic-op discipline (4.1); goldencheck already proved the column-reduction subset |
| Ray/distributed re-port destabilizes the tuned WCC memory profile | Re-run the 5M chain bench as the W4 gate for that module |
| In-repo downstream consumers (SQL-extensions bridge JSON->polars, goldenmatch-duckdb `.pl()`, goldenmatch-kg, dbt) | All port in W4; duckdb gets simpler (`.arrow()` is native); bridge converts JSON->Arrow |
| Program size (124 files) | Wave/batch decomposition with per-batch merges, mirroring goldencheck (P0 + batches); no wave stacks on an unmerged predecessor |

## 8. Rollout and versioning

- **W0-W4:** 2.x minors. Polars default, Arrow env-gated experimental
  (`GOLDENMATCH_FRAME=arrow`). No user-visible change.
- **W5 = v3.0.0:**
  - `polars` leaves the dependency list entirely.
  - Results become `pa.Table`; migration guide is the `pl.from_arrow(...)`
    one-liner; inputs unchanged (Arrow C stream protocol).
  - Fallback-contract change documented plainly (no-wheel platforms:
    correct-but-slower Arrow lane).
  - Full docs sweep (tuning.mdx, api-quick-reference, README, examples,
    CHANGELOG) per the rollout-docs-sweep discipline.
  - golden-suite floor bump + release per the lockstep rule.
- Version bumps follow the three-spot rule (pyproject.toml,
  `goldenmatch/__init__.py`, CHANGELOG).

## 9. Success criteria

1. `pip install goldenmatch` (v3) installs and imports with no `polars`
   distribution present; `python -c "import goldenmatch, sys; assert 'polars' not in sys.modules"` passes.
2. `grep -r "import polars" packages/python/goldenmatch/goldenmatch/` returns
   nothing.
3. Differential fixtures: Arrow backend reproduces all frozen canonicalized
   outputs (clusters, golden, pairs) from the Polars baseline.
4. Perf: 100K zero-config gate green; 1M/25M benches within 10% of the Polars
   baseline (or the offending op moved into a kernel).
5. The fused spine (pyarrow IO -> match_fused -> golden_fused) is the default
   covered-config path with zero Polars in the call stack.
