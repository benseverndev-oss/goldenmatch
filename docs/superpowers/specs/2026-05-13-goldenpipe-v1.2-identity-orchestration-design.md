# GoldenPipe v1.2 — Suite Orchestration for Identity Graph

**Status:** Design proposed (post-audit PR #204). No implementation yet.
**Owner:** GoldenPipe
**Targets:** GoldenPipe `1.1.0 -> 1.2.0`, no other-package version bumps required.

---

## Problem

GoldenMatch v1.15 shipped the Identity Graph (PR #201, hardened in PR #203) but GoldenPipe -- the package marketed as "Golden Suite orchestrator" -- has no surface for it. The audit (PR #204) found three structural gaps:

1. **No identity stage** in the stage registry. `packages/python/goldenpipe/pyproject.toml:52` registers exactly three stages: `goldencheck.scan`, `goldenflow.transform`, `goldenmatch.dedupe`.
2. **`DedupeStage` drops `IdentityConfig` on the floor.** Even if a user puts `identity:` in their YAML, the adapter (`goldenpipe/adapters/match.py`) never propagates it -- the constructed `GoldenMatchConfig` is built from matchkeys + blocking only.
3. **Zero Airflow DAGs invoke identity.** All 12 DAGs in `examples/airflow/` predate v1.15.

The fix is small, bounded, and reuses every existing piece of GoldenPipe machinery.

---

## Goal

A single CLI / Python / Airflow path that runs:

```
GoldenCheck.scan -> GoldenFlow.transform -> GoldenMatch.dedupe -> GoldenMatch.identity_resolve
```

…persists a run manifest including the identity store path + auto-detected conflict count, and is deterministic on a small fixture across re-runs (entity_ids stable, event log byte-equal modulo timestamps).

**Non-goal:** new product surface in GoldenPipe. This is wiring, not new features.

---

## Design

### One new stage

`goldenmatch.identity_resolve` registered at the `goldenpipe.stages` entry-point.

```toml
# packages/python/goldenpipe/pyproject.toml
[project.entry-points."goldenpipe.stages"]
"goldencheck.scan" = "goldenpipe.adapters.check:ScanStage"
"goldenflow.transform" = "goldenpipe.adapters.flow:TransformStage"
"goldenmatch.dedupe" = "goldenpipe.adapters.match:DedupeStage"
"goldenmatch.identity_resolve" = "goldenpipe.adapters.identity:IdentityResolveStage"  # NEW
```

### Adapter

```python
# packages/python/goldenpipe/goldenpipe/adapters/identity.py
class IdentityResolveStage:
    info = StageInfo(
        name="goldenmatch.identity_resolve",
        consumes=["df", "clusters"],
        produces=["identity_summary", "identity_store_path", "conflicts"],
    )

    def validate(self, ctx):
        # IdentityStore comes with goldenmatch>=1.15; no separate optional dep
        from goldenmatch.identity import IdentityStore  # noqa: F401

    def run(self, ctx):
        from goldenmatch.identity import IdentityStore, resolve_clusters

        stage_cfg = ctx.stage_config or {}
        cfg = self._build_identity_config(stage_cfg, ctx)

        with IdentityStore(
            backend=cfg.backend, path=cfg.path, connection=cfg.connection,
        ) as store:
            summary = resolve_clusters(
                clusters=ctx.artifacts["clusters"],
                df=ctx.df,
                scored_pairs=ctx.artifacts.get("scored_pairs", []),
                matchkey_name=ctx.artifacts.get("matchkey_used"),
                store=store,
                run_name=ctx.run_id,
                dataset=cfg.dataset,
                source_pk_col=cfg.source_pk_column,
                emit_singletons=cfg.emit_singletons,
                weak_confidence_threshold=cfg.weak_confidence_threshold,
            )

        ctx.artifacts["identity_summary"] = summary.as_dict()
        ctx.artifacts["identity_store_path"] = cfg.path
        ctx.artifacts["conflicts"] = summary.conflicts_flagged
        return StageResult(status=StageStatus.SUCCESS)
```

### Config shape

Two reasonable paths. We pick (b):

(a) Carry `identity:` as a field on the *DedupeStage*'s `GoldenMatchConfig` and re-use it inside IdentityResolveStage. **Rejected:** couples two stages and forces every DedupeStage caller to know about IdentityConfig.

(b) IdentityResolveStage takes a dedicated `stage_config` matching `IdentityConfig`. **Chosen:** clean separation, each stage owns its own knob set, GoldenMatch's `dedupe_df()` can keep doing identity itself when called outside GoldenPipe.

YAML:

```yaml
stages:
  - use: goldencheck.scan
  - use: goldenflow.transform
  - use: goldenmatch.dedupe
    with:
      matchkeys:
        - { name: people, type: weighted, threshold: 0.85, fields: [...] }
      blocking:
        strategy: static
        keys: [{ fields: [zip] }]
  - use: goldenmatch.identity_resolve
    with:
      path: .goldenmatch/identity.db
      source_pk_column: id
      dataset: customers
      weak_confidence_threshold: 0.6
```

### CLI

```bash
goldenpipe run customers.csv \
  --stages goldencheck.scan,goldenflow.transform,goldenmatch.dedupe,goldenmatch.identity_resolve \
  --identity-path .goldenmatch/identity.db \
  --identity-dataset customers
```

The `--identity-path` and `--identity-dataset` shortcuts populate `stage_config` for `goldenmatch.identity_resolve` when no YAML is supplied; otherwise YAML wins.

### Decision logic

`goldenpipe/decisions.py` already has `decide_match` to skip dedupe when nothing matchable was found. Identity resolution should follow the same logic with one addition:

```python
def decide_identity(ctx) -> Decision:
    """Skip identity_resolve when dedupe was skipped or produced no clusters."""
    if not ctx.artifacts.get("clusters"):
        return Decision(skip=["goldenmatch.identity_resolve"],
                        reason="no clusters from dedupe")
    return Decision()
```

### Manifest persistence

`PipeResult.artifacts` already collects stage outputs. The identity stage adds three keys:

```python
result.artifacts["identity_summary"]      # dict from ResolveSummary.as_dict()
result.artifacts["identity_store_path"]   # str
result.artifacts["conflicts"]             # int -- conflicts_flagged count
```

The existing `PipeResult.reasoning` mechanism captures *why* each stage ran. We need one new key on the manifest itself (already a dict): `manifest.artifacts["identity_store_path"]` so downstream tasks know where to read the graph.

### Cross-stage data flow concern

The audit raised a real wrinkle: `DedupeStage` doesn't currently surface `scored_pairs` to the next stage. Two options:

1. **Extend DedupeStage** to push `scored_pairs` onto `ctx.artifacts`. Two-line change in `adapters/match.py`. Adds memory cost on large runs (~80 bytes/pair); the calling site can opt out via stage config.
2. **Let IdentityResolveStage re-run resolution** idempotently. Already safe per the v1.15 design (`has_run_event` guard on `(run_name, kind, entity_id)`).

**Chosen:** (1). Re-running resolution would double the wall-clock cost on the identity step and (more importantly) wouldn't have access to `scored_pairs` for edge-evidence richness. The 80 bytes/pair penalty is small relative to the 16 GB working set already established for 1M-row runs.

---

## Acceptance criteria

1. **`goldenmatch.identity_resolve` stage registered** via entry-point and discoverable from `goldenpipe stages list`.
2. **Adapter at `goldenpipe/adapters/identity.py`** takes a dict matching `IdentityConfig`, opens the store, calls `resolve_clusters`, populates `ctx.artifacts`.
3. **DedupeStage gains scored_pairs surfacing.** One additional `ctx.artifacts["scored_pairs"]` populated when the downstream pipeline declares it'll consume them (or unconditionally if cost-benefit pans out -- benchmark in the implementation PR).
4. **CLI accepts `--identity-path` and `--identity-dataset`** as convenience shortcuts; YAML stage config remains the canonical path.
5. **One production-shaped Airflow DAG** at `examples/airflow/golden_suite_identity_graph.py`: daily run, identity store on shared storage (S3 + sync or NFS), emits `identity_summary.conflicts_flagged` as an XCom for the existing `golden_suite_review_worker.py` DAG to consume.
6. **One persisted run manifest** that includes inputs, configs, GoldenCheck findings, GoldenFlow manifest, GoldenMatch clusters, identity_store_path, conflicts_flagged count, review-queue snapshot. Reuses `goldenpipe.engine.manifest.Manifest`.
7. **Determinism test:** `tests/test_pipeline_identity.py` with a small fixture; runs the chained pipeline twice; asserts the `entity_id` set is identical and the event-log shape matches (using a volatile-stripping helper modeled after `tests/identity/test_cross_surface_contract.py:_strip_volatile`).
8. **Docs:** new `docs/identity-graph.md` (in goldenpipe's docs dir) covering "when to use GoldenPipe's identity stage vs calling `dedupe_df(config=...identity:enabled...)` directly". Spoiler: GoldenPipe gives you the manifest + Airflow shape + multi-task XCom; direct call gives you embedded-library use.

---

## Out of scope

The implementation PR must explicitly NOT:

- Add new MCP / A2A / REST surface in GoldenPipe v1.2. The GoldenMatch surfaces already cover identity.
- Change the web UI. The "Identities" tab in the GoldenMatch workbench is sufficient.
- Add force-graph visualization or any v2-deferred identity feature.
- Open InferMap-as-identity-feeder (issue #206) -- that's a separate PR.
- Open the GoldenCheck identity preflight (issue #207) -- separate PR.
- Backfill DAG for retroactive entity_id stability (already deferred).
- Publish goldensuite-mcp (issue #205) -- separate concern.

---

## Risks

1. **scored_pairs memory cost on large runs.** At 1M records / 10k clusters / ~30 pairs/cluster, that's ~24 MB of tuples on the artifacts dict. Acceptable, but worth a config flag to opt out (`stage_config: { surface_scored_pairs: false }` on DedupeStage) for very large fixed-config Airflow runs that don't use the identity stage.
2. **Shared-storage requirement.** The SQLite identity store has to be readable+writable by every Airflow task that touches the pipeline. Existing pattern: NFS or rsync-to-S3-after-each-run. Documented in the Airflow example, not enforced by GoldenPipe.
3. **Postgres backend story.** `IdentityConfig.backend="postgres"` works today via the connection DSN. The Airflow example should show one Postgres-backed and one SQLite-backed variant -- different scaling envelopes.
4. **Stage-skip behavior on partial failure.** If `goldenmatch.dedupe` produces clusters but errors during golden roll-up, the cluster artifact is set but `unique`/`golden` may not be. IdentityResolveStage should validate that `clusters` is non-empty and emit `Decision(skip=...)` otherwise. Already covered in the design via `decide_identity`.

---

## Test plan

In addition to acceptance criterion 7:

- `tests/test_identity_stage.py` -- unit tests on the adapter (no end-to-end pipeline)
- `tests/test_pipeline_identity.py` -- determinism test, two runs
- `tests/test_cli_identity.py` -- `--identity-path` flag wiring
- Existing parity: `tests/test_pipeline.py` must continue to pass with the new stage *omitted* from the default pipeline (we're not changing the default; the new stage is opt-in)

---

## Release

GoldenPipe `1.1.0 -> 1.2.0`. CHANGELOG entry describes:
- New `goldenmatch.identity_resolve` stage
- New `IdentityConfig` propagation through `stage_config`
- New CLI flags `--identity-path` / `--identity-dataset`
- New Airflow DAG example
- `DedupeStage.ctx.artifacts["scored_pairs"]` is now populated (backwards compatible -- nothing in v1.1 consumed it)

No other-package version bumps required. Requires `goldenmatch>=1.15.0` (already the minimum after PR #201).

---

## Implementation phasing

Three small PRs in this order:

1. **PR A: adapter scaffold + stage registry + tests** -- the `goldenmatch.identity_resolve` stage works in isolation, run via direct `Pipeline(stages=[...])` API, with the determinism test green. No CLI, no DAG yet.
2. **PR B: CLI shortcuts + decisions + Airflow DAG** -- builds on (A). Adds `--identity-path` flag, `decide_identity`, and `examples/airflow/golden_suite_identity_graph.py`.
3. **PR C: docs + 1.2.0 release** -- new `docs/identity-graph.md`, CHANGELOG entry, version bump, tag, PyPI publish.

Each PR is small enough to review in one sitting. Bundling them is fine if appetite is there; I'd default to three sequential merges.

---

## Open questions for the implementer

1. **Should `decide_identity` be auto-inserted** when the pipeline includes both `goldenmatch.dedupe` and `goldenmatch.identity_resolve`, or always inserted as long as identity is in the stage list? Default: auto-insert.
2. **Where does the `run_id` for `resolve_clusters` come from?** Today the framework generates one per pipeline run; reuse it.
3. **PostflightReport integration:** should `identity_summary` flow back into the controller-telemetry blob that the autoconfig surface already serializes? Probably no -- they're different concerns. Add a separate `identity:` section on `PostflightReport` if needed in v1.3.
