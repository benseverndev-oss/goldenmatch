# Golden Suite Package Audit (2026-05-13)

**Author:** session audit, post-Identity-Graph-v2.1 (PR #203 merged 33a2379)
**Scope:** all non-GoldenMatch suite packages -- goldenpipe, infermap, goldencheck, goldenflow, goldensuite-mcp, dbt-goldencheck, TS ports, Rust extensions
**Question:** What is the highest-leverage next non-GoldenMatch bet, given the v1.15 Identity Graph just shipped?
**Constraint:** small audit, no implementation, prefer docs/spec PR over code changes.

---

## Executive recommendation

**Attack GoldenPipe next.** Specifically: ship **GoldenPipe v1.2 -- suite orchestration for Identity Graph v2.0**.

The audit confirms the hypothesis. GoldenPipe already markets itself as "Golden Suite orchestrator", already has the stage registry + entry-points machinery, and is the only package that does *not* require new product surface area to integrate Identity Graph -- just a fourth stage and a config wire-through. Every other improvement here either has lower adoption leverage (InferMap, GoldenCheck, GoldenFlow are mature standalone tools) or higher engineering cost relative to the win (goldensuite-mcp is unpublished; TS ports are healthy but not bottlenecks; Rust extensions just released).

The single most evidence-laden gap: Identity Graph v2.0 landed in PR #201, ships in `goldenmatch>=1.15.0`, and is invisible from GoldenPipe + the 12 Airflow DAGs. The headline feature has no orchestration surface.

---

## Method

For each package: read `pyproject.toml`, `README.md`, `CLAUDE.md`, the test directory, the entry-points / stage registry, and `examples/`. Cross-check with `.github/workflows/ci.yml` for which lanes run real pytest vs lint-only. Verify release state on PyPI + tags. Map every package against the eight audit questions.

Hard evidence anchors (file:line cited inline below):
- `packages/python/goldenpipe/pyproject.toml:52` -- entry-points stage registry
- `packages/python/goldenpipe/goldenpipe/adapters/match.py:22` -- DedupeStage produces clusters/golden but not entity_id
- `examples/airflow/*.py` -- 12 DAGs, zero `IdentityConfig` or `IdentityStore` references
- `.github/workflows/ci.yml:155` -- dynamic per-package matrix is real
- `packages/python/goldensuite-mcp/goldensuite_mcp/server.py:47-50` -- aggregator does transitively expose identity tools via `goldenmatch.mcp.server.TOOLS`

---

## Per-package scorecard

| Package | Promise | Quickstart | Prod example | CI > lint | Composes w/ Identity v2 | Release/MCP/container | Verdict |
|---|---|---|---|---|---|---|---|
| **goldenpipe** | "Golden Suite orchestrator" | ✅ `gp.run("customers.csv")` | ✅ 4 examples; 12 Airflow DAGs (via goldenmatch direct) | ✅ 18 test files in matrix | ❌ **No identity stage, no `IdentityConfig` wiring** | ✅ PyPI 1.1.0, MCP @8250, Railway live | **Primary target.** Headline composition gap. |
| **infermap** | "Schema mapping engine" | ✅ `infermap.map(src, tgt)` | ✅ 4 Airflow DAGs use it | ✅ 23 test files | ❌ Output doesn't feed identity dataset/source_pk hints | ✅ PyPI 0.4.0, npm parity | Solid; runner-up. |
| **goldencheck** | "Data validation by discovery" | ✅ `goldencheck data.csv` | ✅ Airflow `quality_gate.py` | ✅ 66 test files | ❌ Not wired as identity input gate | ✅ PyPI 1.2.0, TS parity, MCP @8100, Railway live | Mature; tactical work only. |
| **goldenflow** | "Standardize messy data" | ✅ `goldenflow transform data.csv` | ✅ Airflow DAGs use `transform_df` | ✅ 37 test files | ❌ Not wired as identity-safe preprocessor | ⚠️ `pyproject` says 1.1.6, PyPI is 1.1.5, CHANGELOG stops at 1.1.5 -- **silent local version drift** | Mature; one tiny cleanup item below. |
| **goldensuite-mcp** | "One MCP endpoint for the suite" | ✅ `goldensuite-mcp serve` | None | ❌ **No `tests/` directory** | ✅ Transitively picks up `identity_*` tools via `goldenmatch.mcp.server.TOOLS` | ⚠️ Local **v0.1.0**, **never published to PyPI**, no CHANGELOG, no Dockerfile in CI matrix | Architecturally sound, releasing-shaped work; not the next big bet. |
| **dbt-goldencheck** | "dbt-side sanity check + Python runner" | ✅ `dbt deps` + `run_goldencheck.py` | ⚠️ Single test macro by design | ⚠️ no dbt CI in this repo | ❌ N/A by scope | n/a | Intentionally narrow; nothing to do. |
| **TS `goldenmatch`** | "ER toolkit (TS)" | ✅ | ✅ | ✅ vitest | ✅ ships `InMemoryIdentityStore` in 0.8.0 | ✅ npm 0.8.0 | Healthy. v2.1 follow-up = persistent SQLite backend (already scoped). |
| **TS `infermap`** | "Schema mapping (TS)" | ✅ | ✅ | ✅ | ❌ same gap as Python sibling | ✅ npm 0.4.0 | Healthy. |
| **TS `goldencheck-types`** | "Shared field types" | ✅ | n/a (types-only) | ✅ minimal | n/a | ✅ npm 0.1.0 | Healthy. |
| **Rust `goldenmatch_pg`** | "pgrx extension" | ✅ tarball install per release | ✅ identity functions land in 0.4.0 | ✅ `rust_pgrx` lane PG 15/16/17 | ✅ five identity UDFs (PR #201) | ✅ tarballs on release (PR #202) | Healthy; just shipped. |
| **Rust `goldenmatch-duckdb`** | "DuckDB UDFs" | ✅ `pip install` + `register()` | ✅ identity tests | ✅ `duckdb_extensions` lane | ✅ five identity UDFs (PR #201) | ✅ PyPI 0.3.0 (PR #202) | Healthy; just shipped. |

---

## Evidence: the Identity Graph composition gap

Quick grep run for any identity reference outside goldenmatch:

```
$ grep -rn "identity_resolve\|IdentityStore\|goldenmatch\.identity\|identity_graph" \
    packages/python/{goldenpipe,goldencheck,goldenflow,goldensuite-mcp,infermap}/
(no output)

$ grep -rn "IdentityStore\|IdentityConfig\|identity_resolve" examples/airflow/
(no output)
```

Confirmed three structural gaps in the orchestrator surface:

1. **No identity stage in GoldenPipe.** Entry-points at `packages/python/goldenpipe/pyproject.toml:52`:

   ```toml
   [project.entry-points."goldenpipe.stages"]
   "goldencheck.scan" = "goldenpipe.adapters.check:ScanStage"
   "goldenflow.transform" = "goldenpipe.adapters.flow:TransformStage"
   "goldenmatch.dedupe" = "goldenpipe.adapters.match:DedupeStage"
   ```

   There is no `"goldenmatch.identity_resolve"` stage. A `stages/infer_schema.py` file exists but isn't even registered as a stage.

2. **DedupeStage drops `IdentityConfig` on the floor.** `packages/python/goldenpipe/goldenpipe/adapters/match.py:34-54` builds a `GoldenMatchConfig` from `stage_config` *or* from upstream column contexts -- but the helper `_build_config_from_contexts` (lines 69-243) constructs `MatchkeyConfig` + `BlockingConfig` only. `IdentityConfig` is never set. Even if a user adds `identity:` to their YAML, the adapter would have to opt into propagating it.

3. **Zero Airflow DAGs invoke identity.** All 12 DAGs in `examples/airflow/` use `goldenmatch.dedupe` patterns that pre-date v1.15. `golden_suite_customer_360.py` literally has the comment "Cross-source customer 360: unify identity across CRM + warehouse + support" -- but the identity surface it ships predates the durable graph and only does run-local clusters.

GoldenPipe's `Pipeline.run()` already has the contract for adding a stage (entry-points + StageInfo + adapter). The work is small and tightly bounded.

---

## Stale items discovered

These were noticed during the audit but are not the recommended bet:

1. **`goldenflow` version drift.** `packages/python/goldenflow/pyproject.toml` says `1.1.6`, but `goldenflow/__init__.py` still says `1.1.5` and `CHANGELOG.md` stops at `1.1.5`. PyPI is at `1.1.5`. No `goldenflow-v1.1.6` git tag. Either:
   - the pyproject bump was made anticipating a 1.1.6 cut that hasn't happened, **or**
   - someone forgot to land the matching `__init__.py` bump + CHANGELOG + tag.
   This is a 10-line fix (revert to 1.1.5, *or* add a CHANGELOG entry and cut the tag). Worth filing as an issue, not worth blocking on.

2. **`goldensuite-mcp` is local-only.** Local version is `v0.1.0`. `pypi.org/pypi/goldensuite-mcp/json` returns `404 Not Found`. No `tests/` directory. No CHANGELOG. No publish workflow in `.github/workflows/`. The architecture is sound (and it transitively exposes the new identity tools), but the package isn't shipping. If we want the suite-MCP story to be real, this needs:
   - a publish workflow (mirror `publish-goldenmatch.yml`)
   - at minimum a smoke test that imports each sub-package and asserts `TOOLS` is non-empty
   - a first published version

3. **PR #146's per-package pytest claim verified.** All five Python packages with `tests/` directories do run real pytest in CI via the dynamic matrix at `.github/workflows/ci.yml:155`. The audit found no lint-only-disguised-as-tested package.

4. **PRs #165/#166/#167's MCP/PyPI sync verified for the existing packages.** Every published Python package has a matching `.github/workflows/publish-<pkg>.yml`. `publish-mcp.yml` routes to the registry on release. The only blank in the registry routing is `goldensuite-mcp` (because it never publishes).

5. **PR #201 / Identity Graph v2.0** is live (1.15.0 on PyPI, 0.8.0 on npm, 0.3.0 on PyPI for duckdb, 0.4.0 tarballs on the GH release). v2.1 hardening (PR #203) added cross-surface contract test + auto-conflict-detection + schema v2 migration.

---

## Top 3 next bets, ranked

### Bet 1 (recommended): GoldenPipe v1.2 -- suite orchestration for Identity Graph

**Why it wins:**
- Closes the only structural gap between the headline feature (Identity Graph) and the orchestrator the suite is marketed around.
- Tiny new surface area (one stage + one config field + one Airflow DAG + one example). Reuses existing stage registry, existing column-context plumbing, existing manifest persistence.
- High narrative leverage: "one CLI runs InferMap -> GoldenCheck -> GoldenFlow -> GoldenMatch -> Identity Graph" becomes literally true.
- All adjacent packages stay on their current version and need no changes.

### Bet 2: InferMap-as-identity-feeder (medium)

Hook InferMap's mapping output into the identity store as `IdentityAlias` rows. When InferMap maps `crm.cust_id -> customer_id`, that's exactly the alias relationship `identity_aliases` already models. Smaller win than Bet 1 because it doesn't change the user's quickstart, but it's the natural follow-up.

### Bet 3: GoldenCheck quality-gate guard on identity inputs (small)

Add an opt-in "identity-safe" preflight in GoldenCheck that warns when a dataset is missing a stable `source_pk` column. Closes the documented v1.15 gotcha that hash-fallback `record_id`s can collide on duplicate raw rows. Small, low-risk, but doesn't move adoption nearly as much as Bet 1.

**Not recommended for now:**
- TS port enhancements (TS v0.9 / TS persistent identity store) -- already scoped, no urgency
- Force-graph UI -- still YAGNI per the v2 deferred list
- Web "review queue for conflicts" tab -- valuable but smaller leverage than Bet 1
- goldensuite-mcp release -- ship along with Bet 1, not as the headline

---

## Proposed first PR: spec for GoldenPipe v1.2

Single committed doc, no implementation. Targets `docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md`.

**Acceptance criteria for the v1.2 feature it specs:**

1. **New stage:** `goldenmatch.identity_resolve` registered at `goldenpipe.stages` entry-point, adapter at `goldenpipe/adapters/identity.py`.
   - `consumes=["df", "clusters", "scored_pairs"]`
   - `produces=["entity_ids", "identity_summary", "conflicts"]`
2. **Adapter wires `IdentityConfig`:** the existing `DedupeStage` either gains an `identity:` field on `stage_config`, or the new IdentityResolveStage takes the same dict shape as `IdentityConfig` and constructs it. Prefer the second so dedupe stays single-purpose.
3. **One CLI path** that runs the full pipeline:
   ```bash
   goldenpipe run customers.csv \
     --stages goldencheck.scan,goldenflow.transform,goldenmatch.dedupe,goldenmatch.identity_resolve \
     --identity-path .goldenmatch/identity.db
   ```
4. **One production-shaped example:** `examples/airflow/golden_suite_identity_graph.py` -- daily DAG that runs the full chain, persists the identity store to S3, and surfaces `identity_summary.conflicts_flagged` as an Airflow XCom for downstream review.
5. **One persisted run manifest** in the GoldenPipe `PipeResult.artifacts` dict: inputs, configs, quality findings, identity_store_path, conflicts_flagged count, review-queue snapshot.
6. **Determinism test:** small fixture, two runs, byte-equal `entity_id` set, byte-equal event-log shape (modulo timestamps -- reuse the v2.1 `_strip_volatile` helper).
7. **Docs:** "when to use GoldenPipe vs direct GoldenMatch calls" page. GoldenPipe = orchestrator + manifest + airflow-friendly. Direct = library use, embedded pipelines, real-time match-one.

**Non-acceptance / explicit non-goals:**
- No new MCP / A2A / REST surface in GoldenPipe v1.2. Use the GoldenMatch surfaces that already exist.
- No web UI changes. The "Identities" tab in the GoldenMatch workbench is sufficient.
- No identity-graph features that didn't already ship in v1.15 / v2.1. This is wiring, not new product.
- No backfill DAG for retroactive entity_id stability (already deferred to v2.x).
- No InferMap integration (Bet 2; separate PR).

---

## Risks / unknowns

- **`DedupeStage` cluster-output coupling.** The current adapter stores `result.clusters` in `ctx.artifacts`. The new IdentityResolveStage needs `(clusters, df, scored_pairs)` -- but the dedupe adapter doesn't currently expose `scored_pairs` (the pipeline runs identity resolution inside `_run_dedupe_pipeline` already, so the artifacts are downstream of the call). Need to decide: does `goldenmatch.identity_resolve` re-run resolution (cheap if the store already has matching events / idempotent), or do we extend the DedupeStage to surface `scored_pairs` for the next stage?
- **Stage config schema vs `GoldenMatchConfig.identity`.** Two reasonable paths: (a) the IdentityResolveStage takes `IdentityConfig` directly, or (b) it lifts `identity:` off the DedupeStage's `GoldenMatchConfig` and treats them as one logical config. (b) keeps the user's YAML simpler.
- **goldensuite-mcp publishing as a side-effect.** If we want the new identity stage to surface via the aggregator MCP too, it does so automatically (aggregator reads `goldenmatch.mcp.server.TOOLS`). But goldensuite-mcp is unpublished, so end users still won't see it.
- **Airflow example will need the new stage AND the v1.15 store path on shared infra** (S3 or NFS) for multi-task runs. This is a docs/runtime concern, not a code concern.

---

## What this session changed in the repo

Just this doc and the audit branch. No code edits. No new tests. The next PR (the v1.2 spec doc) is the recommended follow-up.

---

## Summary

**Inspected:** 7 Python packages (goldenpipe, infermap, goldencheck, goldenflow, goldensuite-mcp, goldencheck-types, dbt-goldencheck), 3 TS packages (goldenmatch, infermap, goldencheck-types), 2 Rust extensions (postgres, duckdb), all 12 Airflow DAGs, `.github/workflows/ci.yml`, all per-package CLAUDE.md files, all per-package CHANGELOG.md files, every PyPI/npm version vs local pyproject/package.json.

**What changed:** This audit doc. No code.

**Recommended next package:** **GoldenPipe.** Reason: GoldenMatch v1.15 shipped a headline feature (Identity Graph v2.0) that has no path through the suite orchestrator. Closing that gap is small, bounded, and immediately changes what "Golden Suite" means to a user.

**Exact next PR:** committed spec at `docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md` per the acceptance criteria above. No implementation, no other-package changes.

**Risks / unknowns:** scored_pairs coupling between dedupe and identity_resolve stages (resolvable by re-running resolution idempotently); shared-storage requirement for the SQLite identity store across Airflow tasks.
