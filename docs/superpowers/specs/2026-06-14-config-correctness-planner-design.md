# Config-Correctness Planner Design

**Status:** Draft for review
**Date:** 2026-06-14
**Author:** brainstormed with Claude

## Problem

GoldenMatch configs can be technically valid but operationally wrong for the
specific `(data shape × hardware × pipeline stage)` combination. The recurring,
expensive failure is a config that *runs* but routes a stage onto the slow path:

- Forcing distributed WCC (`GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD=0`) when
  the scored-pair set fits driver RAM. At 100M rows the edge set is ~1.76 GB
  (~110M edges) and the in-memory scipy WCC runs in ~60s; the forced distributed
  path is a multi-hour gs://-checkpoint tail.
- The inverse: running a stage in-memory when it will not fit, OOM-killing the
  whole job.

Today the v3 planner (`core/autoconfig_planner.py` + `core/autoconfig_planner_rules.py`,
7 rules over `n_rows × estimated_pair_count × RAM`) owns only the **backend**
decision and emits an `ExecutionPlan`. Distributed routing is a *parallel,
ad-hoc* set of env-var thresholds the planner never sees:

| Env var | Default | Read at |
|---------|---------|---------|
| `GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD` | 50M | `distributed/clustering.py:131` |
| `GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD` | 5M | `distributed/golden.py:23` |
| `GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS` | 2 | scoring MapBatches |
| `GOLDENMATCH_DISTRIBUTED_PIPELINE` | unset | phase gate |

Because these live outside the planner, no component can: (a) decide routing
correct-by-construction from data + hardware, (b) explain *why* a stage is
routed a given way, or (c) lint a user-supplied config and warn that an override
forces a slow path. A human or an agent sets `CLUSTERING_THRESHOLD=0` and gets a
multi-hour run with zero warning.

## Goal

Make the planner **documentation-, hardware-, and methodology-aware** so configs
are correct by construction, and expose that knowledge through the MCP. One
decision trace drives runtime routing, the `explain` rendering, and the `lint`
diff. Env vars become first-class, linted *overrides* rather than an invisible
second brain.

## Non-Goals

- Not redesigning the 7 backend rules or the scoring/clustering kernels.
- Not auto-provisioning clusters. The planner *reads* hardware; it does not
  create it.
- Not parsing `tuning.mdx` prose at runtime. Doc-awareness comes from structured
  rule metadata (Section 7), not NLP over Markdown.

## Architecture

The planner gains a **distributed-routing rule layer** that runs after the
backend rules, keyed off a new **cluster-aware profile**. Each pipeline stage
(scoring, clustering/WCC, golden) gets its own routing decision recorded on the
`ExecutionPlan` plus a `DistributedRoutingDecision` trace entry. The MCP
`plan`/`explain`/`lint` tools are a read-projection of that single trace.

```
data sample ─┐
             ├─► ComplexityProfile (estimated_pair_count, extrapolate_to)
RuntimeProfile (driver RAM/CPU/disk) ─┐
ClusterProfile (probe-or-descriptor) ─┤
                                      ▼
                          backend rules (existing 7)
                                      ▼
                    distributed-routing rule layer (NEW)
                                      ▼
        ExecutionPlan{ backend, ..., scoring_distributed,
                       clustering_strategy, golden_distributed }
                       + decision trace
                                      ▼
                       serialize_telemetry(execution_plan)
                                      ▼
                 MCP plan / explain / lint  (read-projection)
```

### Section 1 — Cluster-aware profile (probe-or-descriptor)

New `ClusterProfile` dataclass and an extension to `RuntimeProfile`
(`core/runtime_profile.py`).

```python
@dataclass(frozen=True)
class ClusterProfile:
    present: bool                 # False => single box
    num_nodes: int
    total_cpus: int
    cluster_mem_gb: float
    driver_mem_gb: float          # head/driver node usable RAM
    source: str                   # "probe" | "descriptor" | "single_box"
```

`capture_cluster_profile(descriptor: dict | None = None) -> ClusterProfile`:

1. If a live Ray context is connected, **probe**: `ray.cluster_resources()` for
   CPUs/memory, node count from `ray.nodes()`; `source="probe"`.
2. Else if `descriptor` is supplied (CLI/MCP), build from it; `source="descriptor"`.
3. Else single box: `present=False`, fields from `RuntimeProfile`;
   `source="single_box"`.

**Load-bearing invariant:** every per-stage memory projection keys off the
**driver** RAM budget, *never* `cluster_mem_gb`, because each materializing stage
(WCC edge set + labels, golden survivorship working set) runs on the driver.
Cluster total only gates whether *scoring* (embarrassingly parallel) distributes.

To remove naming ambiguity, the projections in Section 2 read a single resolved
value `driver_avail_ram`, defined as: `RuntimeProfile.available_ram_gb` when a
live runtime profile exists (the common case, including the probe path), else
`ClusterProfile.driver_mem_gb` (the descriptor path, where there is no local
driver to introspect). The two stages must never silently read different fields;
`driver_avail_ram` is resolved once and passed to all three projections.
Encoding this directly is the fix for the 100M failure.

### Section 2 — Per-stage routing as ExecutionPlan fields

Extend `core/execution_plan.py::ExecutionPlan` with:

```python
scoring_distributed: bool
clustering_strategy: str          # "in_memory_scipy" | "distributed_wcc"
golden_distributed: bool
routing_decisions: tuple[DistributedRoutingDecision, ...]
```

Each stage decided independently on its own fits-in-memory projection (a single
`SAFETY` headroom factor, default 0.6, one named constant):

- **Scoring** distributes when `n_rows × BYTES_PER_ROW > driver_mem × SAFETY`
  *and* a cluster is present (rows do not fit one node). Otherwise in-process.
- **Clustering / WCC** uses `distributed_wcc` **only** when
  `estimated_pair_count × BYTES_PER_EDGE(=16) > driver_avail_ram × SAFETY`.
  Otherwise `in_memory_scipy`. At 100M: ~1.76 GB << driver budget => in-memory.
- **Golden** distributes when grouped record volume
  (`n_rows × BYTES_PER_ROW`, the survivorship working set) exceeds
  `driver_mem × SAFETY` *and* a cluster is present.

`BYTES_PER_ROW` and `BYTES_PER_EDGE` are named module constants with a comment
citing the 100M measurement (16 B/edge: two int32 ids + score float32, padded).

### Section 3 — Distributed-routing rule layer

New `core/distributed_routing_rules.py`, an ordered table analogous to
`autoconfig_planner_rules.py`. **Resolution is strictly per-stage:** the table is
evaluated independently for each of the three stages, and "first match wins"
applies within a stage, not globally. An override on `clustering` (rule 1) must
not suppress the `single_box` decision (rule 2) for `scoring`. Rules, in order:

1. `user_override` — an explicit `DistributedRoutingConfig` pin or a legacy env
   var is set for this stage. Honored, but recorded with `overridden=True` and
   the provenance so the linter sees it.
2. `single_box` — `ClusterProfile.present is False` => every stage in-memory /
   in-process.
3. `cluster_present` — per-stage memory projection (Section 2) decides each stage
   independently.

Each rule emits:

```python
@dataclass(frozen=True)
class DistributedRoutingDecision:
    stage: str                    # "scoring" | "clustering" | "golden"
    mode: str                     # "distributed" | "in_memory"
    rule_name: str
    reason: str                   # human sentence for explain
    projected_bytes: int
    budget_bytes: int
    overridden: bool
    override_source: str | None   # e.g. "env:GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD"
```

### Section 4 — DistributedRoutingConfig + env-var provenance

New optional sub-config on `GoldenMatchConfig` (`config/schemas.py`):

```python
class DistributedRoutingConfig(BaseModel):
    scoring: Literal["auto", "distributed", "in_process"] = "auto"
    clustering: Literal["auto", "distributed_wcc", "in_memory_scipy"] = "auto"
    golden: Literal["auto", "distributed", "in_process"] = "auto"
```

The legacy env vars map onto this config at load time and are recorded as
overrides with provenance (`override_source`). This makes today's invisible env
thresholds visible to both the rule layer (which honors them) and the linter
(which flags them). `auto` => the rule layer decides.

### Section 5 — Decision trace in telemetry

Extend `web/controller_telemetry.py::serialize_telemetry` so the `execution_plan`
dict carries the per-stage routing decisions:

```json
"execution_plan": {
  "backend": "...",
  "routing": [
    {"stage": "clustering", "mode": "in_memory",
     "rule_name": "cluster_present", "overridden": false,
     "projected_bytes": 1_760_000_000, "budget_bytes": 28_800_000_000,
     "reason": "edge set 1.76GB < driver budget 28.8GB"}
  ]
}
```

This is the single machine-readable artifact the MCP tools render. No second
source of truth.

### Section 6 — MCP plan / explain / lint

Three tools in the MCP surface (`mcp/agent_tools.py`, `mcp/server.py`):

- **`plan(sample, runtime_profile?, cluster_descriptor?)`** — returns the full
  `ExecutionPlan` including per-stage routing and the decision trace.
- **`explain(plan)`** — human rendering of the trace, e.g.
  *"clustering runs in-memory: projected edge set 1.76 GB < driver budget 28.8 GB
  (rule cluster_present)."* Each line links to the rule's `doc_anchor` (Section 7).
- **`lint(config, runtime_profile?, cluster_descriptor?)`** — diffs a
  *user-supplied* config (including env vars) against what the planner would
  choose. Each override gets a severity. Example: `CLUSTERING_THRESHOLD=0` at
  100M =>
  > **ERROR**: forces `distributed_wcc`; projected edge set 1.76 GB fits driver
  > RAM (budget 28.8 GB). Expect a multi-hour gs://-checkpoint tail vs ~60s
  > in-memory scipy. Set `clustering=auto` or pass `allow_slow_path=true` to ack.

**Enforcement model: advisory below scale, blocking at scale.** This mirrors the
existing controller precedent (`ControllerNotConfidentError` raised at
`df.height >= 100_000`; `allow_red_config` refuse-on-RED from #723; the
no-silent-fallback memory). Concretely:

- Below the scale threshold (`n_rows < 100_000`): lint findings are advisory
  (returned as warnings; the run proceeds).
- At or above it: a slow-path override (an override whose linted severity is
  ERROR) **raises** at config-resolution time unless the caller passes
  `allow_slow_path=True`, which records an explicit acknowledgement in telemetry.

The threshold reuses the existing controller-confidence scale gate; it is not a
new tunable.

**`allow_slow_path` entry points (so blocking never becomes a dead end at scale):**
the ack is reachable from every surface that can trigger a run -
(a) a `GoldenMatchConfig.allow_slow_path: bool = False` field (Section 4 sits
beside it), (b) a `dedupe_df` / `match_df` keyword argument that overrides the
config field for a single call, and (c) an `allow_slow_path` argument on the MCP
`lint`/`plan` tools. All three resolve to the same flag and record the
acknowledgement (with its source) in telemetry.

### Section 7 — Documentation-awareness (rules are the catalog)

Each backend rule and routing rule carries structured metadata:

```python
rationale: str        # one-sentence "why this rule exists"
doc_anchor: str       # anchor into tuning.mdx, e.g. "#clustering-in-memory-vs-distributed"
```

`explain` and `lint` link findings to the anchor. A **drift test**
(`tests/.../test_routing_doc_drift.py`, modeled on the frozen-config drift test)
asserts every rule's `doc_anchor` resolves to a real anchor in
`docs-site/goldenmatch/tuning.mdx`, and that every routing knob documented in
`tuning.mdx` maps to a rule or config field. Doc and code cannot diverge. This
is the "documentation-aware" requirement done correct-by-construction, not by
scraping prose.

### Section 8 — Testing

- **Unit (projection boundaries):** at the just-fits / just-exceeds driver-RAM
  boundary, each stage flips mode exactly once. Table-driven over
  `(n_rows, estimated_pair_count, driver_mem_gb, cluster_present)`.
- **Parity (100M):** the planner's routing at 100M reproduces the validated PASS
  shape — `clustering_strategy == "in_memory_scipy"`, `scoring_distributed == True` —
  matching the recorded `docs/quality-invariant-scale.md` run.
- **Lint severity:** `CLUSTERING_THRESHOLD=0` is ERROR at 100M, advisory at 1M;
  `allow_slow_path=True` downgrades the ERROR to an acknowledged warning.
- **Probe-or-descriptor:** `capture_cluster_profile` returns `source="probe"`
  under a faked Ray context, `source="descriptor"` from a dict, `source="single_box"`
  otherwise; the WCC decision uses driver RAM in all three.
- **Drift:** every rule `doc_anchor` exists in `tuning.mdx` and vice-versa.
- **Regression guard:** the #491/#715 backend-rule benchmark tests are unchanged
  (the routing layer runs *after* and does not alter `backend`).

## Risks / Open Questions

- **`BYTES_PER_ROW` realism.** The survivorship working set is config-dependent
  (field count, golden strategy). Start with the measured 100M constant; if the
  golden-stage projection mispredicts, refine to a per-config estimate from the
  sample. Tracked, not v1-blocking.
- **Probe accuracy under autoscaling.** A probed cluster mid-scale-up reports
  fewer nodes than it will have. The descriptor path is the escape hatch; probe
  is best-effort and the projection has `SAFETY` headroom.
- **Override ergonomics.** `allow_slow_path` must be reachable from every surface
  (CLI flag, config field, MCP arg) so the blocking behavior never becomes a
  dead end at scale.

## Rollout

1. `ClusterProfile` + `capture_cluster_profile` (probe-or-descriptor), unit-tested.
2. `ExecutionPlan` routing fields + per-stage projection constants.
3. `distributed_routing_rules.py` rule layer + decision trace.
4. `DistributedRoutingConfig` + env-var provenance mapping.
5. Wire routing decisions into the distributed pipeline call sites (replace the
   raw env-var reads with the resolved `ExecutionPlan` fields).
6. `serialize_telemetry` extension.
7. MCP `plan` / `explain` / `lint` tools + enforcement gate.
8. `tuning.mdx` rule-metadata + drift test.

Each step lands behind the existing default behavior until step 5 flips the call
sites; the planner's default routing must reproduce today's correct outcomes
(verified by the 100M parity test) before the env-var reads are removed.
