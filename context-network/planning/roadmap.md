# Roadmap — Arrow-native arc

The destination is **engine portability** (DataFusion single-box → Sail distributed) —
see [../decisions/0001-gate-reframe-engine-portability.md](../decisions/0001-gate-reframe-engine-portability.md).

## Done
- **Step 1 — id_prep plannable (#696).** `ClusterPairScores.from_frames` rewritten as a
  group-by; id_prep 566→34s @100M; end-to-end flips to 2.11×.
- **Step 2 — DataFusion spine, Stages A-E.** Merged. Scale-mode contract shipped
  (#702); Stage E spill verdict recorded as HONEST-NULL on one-box survival (#706).
  See [../architecture/datafusion-spine.md](../architecture/datafusion-spine.md).

## Decided NOT to do (now)
- **Flip `mode` default to `"scale"`** — blocked: the one-box-survival gate is not met
  (Stage E). Revisit when the Sail tier removes the UF island.

## Next — the Sail tier (SPECCED 2026-06-03, build not started)
**The real value of the arc** (Stage E showed one-box is non-binding). A Sail-native
distributed pipeline (Spark Connect / PySpark) that re-expresses the spine's relational
plan across nodes, computes connected components distributed (removing the one-box UF
island), and ultimately REPLACES the Ray distributed stack. See
[../architecture/sail-tier.md](../architecture/sail-tier.md) +
[../decisions/0004-sail-tier-scope.md](../decisions/0004-sail-tier-scope.md).
Spec: `docs/superpowers/specs/2026-06-03-sail-tier-design.md`. Staged, each a gate:
- **S1 — SHIPPED (PR #709, 2026-06-03)** — Sail harness + scorer pandas UDF + score/dedup,
  connectivity + pair-set parity gates green on the new `sail` CI lane.
- **S2 — SHIPPED (PR #712, 2026-06-03)** — **WCC on Sail** via min-label propagation; partition-
  parity green (chain + junction + singleton). The make-or-break gate; existential risk CLOSED.
  (Led with label-prop; large-star/small-star is an S4 prerequisite.)
- **S3 (golden) — SHIPPED (PR #714, 2026-06-03)** — distributed survivorship via
  `collect_list` + a scalar pandas UDF calling the one-box `merge_field`; content-parity green.
  Scoped to golden; identity split to its own stage.
- **S4 harness — SHIPPED (PR #717, 2026-06-04)** — chain-robust O(log n) WCC (pointer-jumping;
  the blind large-star attempt was wrong, caught by plan-review hand-trace), `run_sail_pipeline`
  end-to-end, and the 100M bench scaffold. The `sail` lane has 6 green gates. The BUILDABLE Sail
  tier is COMPLETE.
- **Remaining (needs a BYO cluster):** the real 100M multi-node run (`SAIL_REMOTE` secret) → the
  binding verdict + Ray retirement. The only thing left, and it needs real infrastructure.
- **Identity on Sail** — split off; its own stateful stage, not yet done.
- **S3** — golden (incl. custom rules) + identity on Sail.
- **S4** — binding 100M+ multi-node bench + Ray retirement. Kill criterion: completes
  where one-box can't, per-node RSS bounded, wall scales with nodes.

## Other candidates (not specced)
- **Relational-stages-only spill bench** — score+dedup under a cgroup `MemoryMax` cap,
  excluding the UF collection, to show relational spill survival crisply in isolation.
- **Fix the pre-existing empty/all-singleton `run_spine` SchemaError** (frames-out tail,
  null vs i64 join key) — flagged during Stage D, out of scope there.

## Adjacent — security hardening arc (2026-06-05, COMPLETE)
Cleared 42 open alerts (7 Dependabot + 35 code scanning) and lifted Scorecard
6.1->7.3: Token-Permissions 0->10 (#760/#772), Signed-Releases 0->8 (#770;
->10 on next pg release), Fuzzing 0->10 (#778/#783). New hardening helpers:
`_logging.sanitize_for_log()` (9 sites), `_paths.safe_path()` (all entry
boundaries + 9 MCP tool handlers; opt-in `GOLDENMATCH_ALLOWED_ROOT` sandbox).
Property suites found 4 real bugs pre-merge. OPEN ACTIONS: Railway
`GOLDENMATCH_ALLOWED_ROOT=/data`; issue #784 (dice/jaccard bloom); TS
`stdNameProper` titlecase. See [security-hardening.md](security-hardening.md).

## Adjacent — surface hardening + parity arc (2026-06-05, Waves 0-4 executed)
A risk-first sweep of the four user surfaces (CLI/TUI/web/API) from the same-day
audit: fail-closed auth on all five HTTP servers, the confirmed CLI bugs, the three
orphaned TUI components, and the tractable Python->TS parity gaps (resolveClusters,
config optimizer, faithful PPRL — each with Python-emitted parity fixtures).
Waves 0/1/2.1 merged (#766/#767/#769); ten PRs open (#771-#782). OPEN ACTION:
`GOLDENMATCH_MCP_TOKEN` on the Railway MCP service. Remaining heavy: AgentSession.
See [surface-hardening.md](surface-hardening.md).

## Adjacent — SQL-native extensions surface (SHIPPED 2026-06-05)
Not part of the scale arc, but the same Arrow-native theme: the graph + embedding
UDFs went **native-direct** (no CPython bridge) across DuckDB, Postgres, and DataFusion
via a shared pyo3-free `graph-core` kernel + a `goldenmatch-embed` wheel over
`goldenembed-rs` (#509; PRs #740/#743/#745). See
[../architecture/sql-native-extensions.md](../architecture/sql-native-extensions.md) +
[../decisions/0005-sql-native-direct-udfs.md](../decisions/0005-sql-native-direct-udfs.md).

## Adjacent — GoldenFlow Arrow-native kernel (SHIPPED 2026-06-07)
Same Arrow-native theme, sibling package: GoldenFlow's date/phone normalization
(was ~92 % of a 1M-row run, per-row `dateutil`/`phonenumbers`) is now vectorized
Polars fast paths with a per-row fallback (76× date / 19× phone; ~14× end-to-end),
plus an optional `goldenflow-native` Rust/PyO3 abi3 kernel (mirrors
`goldenmatch-native`) for the phone residual — Arrow zero-copy, **NANP-only gated**
so it's parity-safe with the `phonenumbers` library by construction. Includes the
per-platform wheel publish workflow + two `native_flow` CI lanes. See
[../architecture/goldenflow-native-kernel.md](../architecture/goldenflow-native-kernel.md)
+ [../decisions/0006-goldenflow-native-nanp-gating.md](../decisions/0006-goldenflow-native-nanp-gating.md).
Open follow-up: publish the first `goldenflow-native` wheel, then promote
`goldenflow[native]` into `[all]` + a marker-guarded default dep.

## Adjacent — auto-config search strategy after the engine speedup (v1.28.0, 2026-06-06)
The mirror image of the scale arc: because execution got ~5x cheaper, the auto-config
*brain* can now measure instead of extrapolate and use its power tools. v1.28.0 shipped the
spine — a `fast`/`normal`/`thinking`/`einstein` **planning-effort tier**, measure-don't-
extrapolate at the higher tiers, and a provider-aware in-house-embedding exemption. Full
successive-halving + an LLM-judge labeling objective are staged behind the tier seam. See
[autoconfig-search-strategy.md](autoconfig-search-strategy.md).

## Related larger arcs (in `packages/python/goldenmatch/CLAUDE.md`)
- The Splink-Spark parity roadmap (Ray Phases 1-6) — distributed loader → controller →
  clustering → golden → multi-node → identity. Mostly plumbing-complete, gated behind
  `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1`.

---
**Classification:** planning/active • **Last updated:** 2026-06-07
