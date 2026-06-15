# #956: memory-aware distributed clustering routing — design

**Goal:** Phase-5 distributed clustering should default to the fast in-memory connected-components path whenever the scored pair set fits in driver RAM, instead of routing to the multi-hour distributed WCC at a fixed 50M-pair threshold. Plus: regression-test the #955 materialize fix, and document the "only scoring needs distribution at scale" rule.

## Problem (from the issue)
At 100M rows the scored pair set is ~110M edges (~1.76 GB raw) — fits one node comfortably — but `build_clusters_distributed` routes it to the distributed randomized-contraction WCC (multi-hour) because `pair_count (110M) >= threshold (50M)`. An end user only escapes by knowing to raise `GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD`. The decision should be memory-aware: distribute only when the pairs genuinely don't fit driver RAM.

## Current code
`goldenmatch/distributed/clustering.py:230-231`:
```python
threshold = _label_prop_threshold()            # 50M default, env-overridable
pair_count = pairs_ds.count()
use_label_prop = force_label_propagation or pair_count >= threshold
```
`_label_prop_threshold()` (clustering.py:164-173) returns `int(GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD)` if set, else `_LABEL_PROP_PAIR_THRESHOLD = 50_000_000`.

The in-memory path = `_build_clusters_scipy_fallback` (clustering.py:371-433): collects pairs via pyarrow, builds a CSR matrix, `scipy.csgraph.connected_components`. The distributed path = `randomized_contraction_wcc` (forced by `_phase5_cluster` via `algorithm="randomized_contraction"`; the `algorithm` kwarg only selects WHICH distributed algo, it does NOT force distribution — the `use_label_prop` gate still decides).

`#955`'s materialize fix is on main at `pipeline.py:166-167` (`raw_pairs_ds = raw_pairs_ds.materialize()` after `score_blocks_distributed`, before `_phase5_cluster`).

`psutil>=5.9` is a dep; `core/runtime_profile.py:38-40` already uses `psutil.virtual_memory().available`.

## Design

### 1. Memory-aware routing (the core fix)
New helper in `clustering.py`:
```python
_PAIR_PEAK_BYTES = 96  # ~24 B/pair (id_a,id_b,score int64/int64/f64) x ~4 peak
                       # (arrow table + numpy arrays + CSR + labels live at once)

def _route_distributed(pair_count: int) -> bool:
    """True => distributed WCC; False => in-memory scipy CC.

    Explicit GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD wins (back-compat,
    power users forcing the boundary). Unset (default) => memory-aware:
    in-memory when the estimated peak pair-set bytes fit available driver RAM.
    """
    raw = os.environ.get("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD")
    if raw is not None:
        try:
            return pair_count >= int(raw)
        except ValueError:
            pass
    import psutil
    available = psutil.virtual_memory().available
    return pair_count * _PAIR_PEAK_BYTES > available
```
Decision line becomes:
```python
use_label_prop = force_label_propagation or _route_distributed(pair_count)
```
Log the decision (est bytes vs available) at INFO so the route is visible (ties into the sibling #956/#957 "never silently take the slow path" theme).

**Precedence chosen:** explicit env var overrides; default is memory-aware. This preserves every existing `GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD` user's behavior while fixing the default. `force_label_propagation=True` still always distributes (unchanged).

`_label_prop_threshold()` stays (other callers/tests reference it) but the routing no longer calls it directly when the env var is unset.

### 2. Regression test for the #955 materialize fix
Assert `_run_phase5_pipeline` materializes the scored pair dataset exactly once before clustering (scoring DAG not re-executed per WCC round). Test via a fake `Dataset` whose `.materialize()` increments a counter and whose downstream `_phase5_cluster` is monkeypatched to a no-op; assert `materialize` called once and the dataset passed to `_phase5_cluster` is the materialized one. No real Ray required (monkeypatch `score_blocks_distributed` to return the fake dataset).

### 3. Doc
Add a short "Only scoring needs distribution at this scale" note to `docs-site/goldenmatch/tuning.mdx` Distributed section: clustering routes in-memory automatically when the pair set fits driver RAM; `GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD` is the manual override.

## Tests
- `tests/test_distributed_clustering.py`: add `test_route_distributed_memory_aware_default` (monkeypatch psutil available RAM high → big pair_count routes in-memory; low → routes distributed), `test_route_distributed_env_override_wins` (env set → pair-count threshold honored regardless of RAM), `test_route_distributed_force_label_prop` (force flag always distributes). Pure-function tests, no Ray.
- `tests/test_phase5_distributed_pipeline.py` (or test_phase5_cluster_routing.py): add `test_phase5_materializes_pairs_once` regression test.

## Out of scope (separate issues)
- The slow-path WARNING when distributed runs on a fits-in-memory set (#956's sibling comment) — a follow-up; this issue makes the DEFAULT correct, which is the higher-leverage half.
- `_score` cluster saturation / column projection (#957).

## Risk
Low. The change only widens when the in-memory path is taken (it was already the path below 50M; now also above 50M when RAM allows). The scipy path is the verified-correct reference. Existing quality-invariance tests cover both routes producing identical components.
