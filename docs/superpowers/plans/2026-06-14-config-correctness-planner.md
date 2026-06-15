# Config-Correctness Planner Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v3 planner own per-stage distributed routing (correct-by-construction, keyed off driver RAM) and project that one decision trace through new MCP `plan_routing` / `explain_routing` / `lint_routing` tools, so the 100M case routes to in-memory WCC and slow-path overrides are flagged (and refused at scale).

**Architecture:** A new cluster-aware profile (`ClusterProfile`, probe-or-descriptor) plus a post-pass routing rule layer that runs after the existing 7 backend rules. The routing layer decides scoring/clustering/golden independently on a per-stage fits-in-driver-RAM projection, writes the result onto new `ExecutionPlan` fields, and emits a `DistributedRoutingDecision` trace. Telemetry and three pure MCP tools are read-projections of that trace. Legacy env-var thresholds become linted overrides; a slow-path override at scale raises unless acked.

**Tech Stack:** Python 3.12, frozen dataclasses, pydantic v2 (`GoldenMatchConfig`), raw MCP SDK (`mcp.types.Tool`), pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-06-14-config-correctness-planner-design.md`

---

## Environment / runner notes (read before running any step)

- **Worktree:** `D:\show_case\gm-ccp`, branch `feat/config-correctness-planner`. All paths below are relative to the package dir `packages/python/goldenmatch` unless absolute.
- **Run tests from the package dir** (local CWD convention): `cd packages/python/goldenmatch` first.
- **Windows polars WMI hang:** prefix every pytest command with `POLARS_SKIP_CPU_CHECK=1` (and `PYTHONIOENCODING=utf-8` if a test prints non-ASCII). The new core modules (`cluster_profile`, `distributed_routing_rules`, `execution_plan`) do NOT import polars, so their unit tests are light; the controller/schema tests do.
- **Do NOT run the full pytest suite locally** (xdist OOMs this box). Run only the targeted files named in each step. The final regression guard (Task 11) names the specific benchmark files to run.
- **Commit after every task** (frequent commits). Do not push; the user merges via PR.

## File Structure (what each new/changed file owns)

| File | Responsibility |
|------|----------------|
| `core/cluster_profile.py` (new) | `ClusterProfile` dataclass + `capture_cluster_profile()` (probe Ray, else descriptor, else single-box). Owns the "what hardware is available" signal. |
| `core/execution_plan.py` (modify) | Add `DistributedRoutingDecision` dataclass + new `ExecutionPlan` routing fields (`scoring_distributed`, `golden_distributed`, extend `clustering_strategy`, `routing_decisions`). Owns the plan output shape. |
| `core/distributed_routing_rules.py` (new) | The routing rule layer: per-stage projection, `apply_distributed_routing()`, the env/config override resolution, `SlowPathRefusedError` + `enforce_routing()`, and `ROUTING_DOC_ANCHORS`. Owns the routing DECISION. |
| `config/schemas.py` (modify) | `DistributedRoutingConfig` sub-model + `distributed_routing` / `allow_slow_path` fields on `GoldenMatchConfig`. Owns the user-facing override surface. |
| `core/autoconfig_controller.py` (modify) | Wire `capture_cluster_profile()` + `apply_distributed_routing()` into the planner hook (line ~1246). Owns integration. |
| `web/controller_telemetry.py` (modify) | Extend `_execution_plan()` to serialize the routing trace. Owns the machine-readable projection. |
| `distributed/clustering.py` (modify) | Accept an optional explicit routing override; fall back to today's env threshold when absent. Owns the runtime cutover. |
| `mcp/agent_tools.py` + `mcp/server.py` (modify) | `plan_routing` / `explain_routing` / `lint_routing` tools + handlers. Owns the agent-facing surface. |
| `docs-site/goldenmatch/tuning.mdx` (modify) | Add anchors the rule metadata links to. |
| `tests/test_*.py` (new) | One test file per task (named below). |

---

### Task 1: ClusterProfile + capture_cluster_profile (probe-or-descriptor)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/cluster_profile.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_profile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cluster_profile.py
from goldenmatch.core.cluster_profile import ClusterProfile, capture_cluster_profile


def test_single_box_when_no_ray_no_descriptor():
    p = capture_cluster_profile(descriptor=None, ray_module=None)
    assert p.present is False
    assert p.source == "single_box"
    assert p.num_nodes == 1


def test_descriptor_path():
    desc = {"num_nodes": 4, "total_cpus": 80, "cluster_mem_gb": 256.0, "driver_mem_gb": 48.0}
    p = capture_cluster_profile(descriptor=desc, ray_module=None)
    assert p.present is True
    assert p.source == "descriptor"
    assert p.num_nodes == 4
    assert p.total_cpus == 80
    assert p.driver_mem_gb == 48.0


def test_probe_path_uses_ray_resources():
    class _FakeRay:
        @staticmethod
        def is_initialized():
            return True

        @staticmethod
        def cluster_resources():
            return {"CPU": 80.0, "memory": 256 * 1024 ** 3}

        @staticmethod
        def nodes():
            return [{"Alive": True}, {"Alive": True}, {"Alive": True}]

    p = capture_cluster_profile(descriptor=None, ray_module=_FakeRay)
    assert p.present is True
    assert p.source == "probe"
    assert p.total_cpus == 80
    assert p.num_nodes == 3
    assert p.cluster_mem_gb == 256.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_cluster_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'goldenmatch.core.cluster_profile'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/cluster_profile.py
"""ClusterProfile -- the "what hardware is available" signal for the
distributed-routing rule layer.

Probe-or-descriptor: probe a live Ray context when connected, else accept a
caller-supplied descriptor (CLI / MCP), else single box. The in-memory-vs-
distributed decision for materializing stages keys off DRIVER RAM
(RuntimeProfile.available_ram_gb), NOT cluster_mem_gb -- see the routing rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ClusterProfile:
    present: bool
    num_nodes: int
    total_cpus: int
    cluster_mem_gb: float
    driver_mem_gb: float
    source: str  # "probe" | "descriptor" | "single_box"


_SINGLE_BOX = ClusterProfile(
    present=False, num_nodes=1, total_cpus=0,
    cluster_mem_gb=0.0, driver_mem_gb=0.0, source="single_box",
)


def _probe(ray_module: Any) -> ClusterProfile | None:
    try:
        if not ray_module.is_initialized():
            return None
        res = ray_module.cluster_resources()
        nodes = [n for n in ray_module.nodes() if n.get("Alive", True)]
    except Exception:
        return None
    cpus = int(res.get("CPU", 0))
    mem_gb = float(res.get("memory", 0)) / (1024 ** 3)
    return ClusterProfile(
        present=True, num_nodes=max(1, len(nodes)), total_cpus=cpus,
        cluster_mem_gb=mem_gb, driver_mem_gb=mem_gb / max(1, len(nodes)),
        source="probe",
    )


def _from_descriptor(d: Mapping[str, Any]) -> ClusterProfile:
    return ClusterProfile(
        present=True,
        num_nodes=int(d.get("num_nodes", 1)),
        total_cpus=int(d.get("total_cpus", 0)),
        cluster_mem_gb=float(d.get("cluster_mem_gb", 0.0)),
        driver_mem_gb=float(d.get("driver_mem_gb", 0.0)),
        source="descriptor",
    )


def capture_cluster_profile(
    *,
    descriptor: Mapping[str, Any] | None = None,
    ray_module: Any | None = "auto",
) -> ClusterProfile:
    """Resolve the cluster context. ``ray_module="auto"`` imports ray lazily;
    pass an explicit module (or ``None``) in tests to control the probe."""
    if ray_module == "auto":
        try:
            import ray as ray_module  # type: ignore
        except Exception:
            ray_module = None
    if ray_module is not None:
        probed = _probe(ray_module)
        if probed is not None:
            return probed
    if descriptor is not None:
        return _from_descriptor(descriptor)
    return _SINGLE_BOX
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_cluster_profile.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/cluster_profile.py packages/python/goldenmatch/tests/test_cluster_profile.py
git commit -m "feat(routing): ClusterProfile probe-or-descriptor"
```

---

### Task 2: DistributedRoutingDecision + ExecutionPlan routing fields

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/execution_plan.py:14-32`
- Test: `packages/python/goldenmatch/tests/test_execution_plan_routing_fields.py`

Defaults must preserve today's behavior (all in-memory / in-process). `clustering_strategy` keeps `"in_memory"` as default; we ADD `"distributed_wcc"` to the literal. `DistributedRoutingDecision` lives in this module (not the rules module) so `ExecutionPlan` can hold a tuple of them without a circular import.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_plan_routing_fields.py
from goldenmatch.core.execution_plan import DistributedRoutingDecision, ExecutionPlan


def test_default_plan_is_all_in_memory():
    p = ExecutionPlan()
    assert p.scoring_distributed is False
    assert p.golden_distributed is False
    assert p.clustering_strategy == "in_memory"
    assert p.routing_decisions == ()


def test_routing_decision_shape():
    d = DistributedRoutingDecision(
        stage="clustering", mode="in_memory", rule_name="cluster_present",
        reason="edge set 1.8GB <= budget 28.8GB", projected_bytes=1_800_000_000,
        budget_bytes=28_800_000_000, overridden=False, override_source=None,
    )
    assert d.stage == "clustering"
    assert d.overridden is False


def test_plan_carries_distributed_wcc():
    p = ExecutionPlan(clustering_strategy="distributed_wcc", scoring_distributed=True)
    assert p.clustering_strategy == "distributed_wcc"
    assert p.scoring_distributed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_execution_plan_routing_fields.py -v`
Expected: FAIL with `ImportError: cannot import name 'DistributedRoutingDecision'`

- [ ] **Step 3: Write minimal implementation**

Change the `ClusteringStrategy` literal (line 15) to add `"distributed_wcc"`:

```python
ClusteringStrategy = Literal[
    "in_memory", "partitioned_union_find", "streaming_cc", "distributed_wcc"
]
```

Add the decision dataclass above `ExecutionPlan` (after line 16):

```python
@dataclass(frozen=True)
class DistributedRoutingDecision:
    """One per-stage routing decision + the projection that drove it.

    ``mode`` is the normalized vocabulary "distributed" | "in_memory".
    ``projected_bytes`` is the stage's working-set estimate; ``budget_bytes``
    is the driver-RAM budget it was compared against. ``overridden`` marks a
    user/env override that the linter surfaces.
    """
    stage: str            # "scoring" | "clustering" | "golden"
    mode: str             # "distributed" | "in_memory"
    rule_name: str        # "user_override" | "single_box" | "cluster_present"
    reason: str
    projected_bytes: int
    budget_bytes: int
    overridden: bool
    override_source: str | None
```

Add fields to `ExecutionPlan` (after `rule_name`, line 32):

```python
    scoring_distributed: bool = False
    golden_distributed: bool = False
    routing_decisions: tuple[DistributedRoutingDecision, ...] = ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_execution_plan_routing_fields.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/execution_plan.py packages/python/goldenmatch/tests/test_execution_plan_routing_fields.py
git commit -m "feat(routing): DistributedRoutingDecision + ExecutionPlan routing fields"
```

---

### Task 3: Routing rule layer (projection + apply_distributed_routing)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/distributed_routing_rules.py`
- Test: `packages/python/goldenmatch/tests/test_distributed_routing_rules.py`

The load-bearing math. `driver_avail_ram` resolves to `runtime.available_ram_gb` when a runtime profile is present (controller path), else `cluster.driver_mem_gb` (descriptor path). One `SAFETY` headroom factor. Per-stage, independently resolved. Overrides honored from (a) `DistributedRoutingConfig` pins and (b) the clustering env-threshold footgun.

Per the spec invariant, **all three stages compare against the SAME driver-RAM budget**: scoring/golden against the row-frame estimate (`n_rows × BYTES_PER_ROW`), clustering against the edge-set estimate (`estimated_pair_count × BYTES_PER_EDGE`). This makes routing hardware-relative: the same 100M data distributes scoring on a 48 GB-driver cluster but keeps it in-memory on a 256 GB single box. No per-node math — the driver budget is the single yardstick.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_distributed_routing_rules.py
from dataclasses import replace

from goldenmatch.core.cluster_profile import ClusterProfile, capture_cluster_profile
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.distributed_routing_rules import (
    BYTES_PER_EDGE, apply_distributed_routing,
)
from goldenmatch.core.runtime_profile import RuntimeProfile


def _runtime(gb):
    return RuntimeProfile(available_ram_gb=gb, cpu_count=16, disk_free_gb=500.0)


def _cluster():
    return ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                          cluster_mem_gb=256.0, driver_mem_gb=48.0, source="descriptor")


def test_single_box_keeps_everything_in_memory():
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0),
        cluster=capture_cluster_profile(descriptor=None, ray_module=None),
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
    )
    assert plan.clustering_strategy == "in_memory"
    assert plan.scoring_distributed is False
    assert plan.golden_distributed is False
    assert all(d.rule_name == "single_box" for d in plan.routing_decisions)


def test_100m_edge_set_fits_driver_ram_clustering_in_memory():
    # 110M edges * 16B = 1.76GB << 48GB driver * 0.6 budget => in-memory.
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0), cluster=_cluster(),
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
    )
    assert plan.clustering_strategy == "in_memory"
    assert plan.scoring_distributed is True  # 100M-row frame ~51GB > 48GB driver budget


def test_clustering_distributes_only_when_edges_exceed_driver_budget():
    # Force the edge set above budget: 5B edges * 16B = 80GB > 28.8GB budget.
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0), cluster=_cluster(),
        n_rows_full=100_000_000, estimated_pair_count=5_000_000_000,
    )
    assert plan.clustering_strategy == "distributed_wcc"


def test_clustering_uses_driver_ram_not_cluster_total():
    # Cluster has 256GB total, but driver only 4GB: a 10GB edge set must
    # still distribute because it can't materialize on the driver.
    small_driver = ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                                  cluster_mem_gb=256.0, driver_mem_gb=4.0, source="descriptor")
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=None, cluster=small_driver,
        n_rows_full=10_000_000, estimated_pair_count=700_000_000,  # ~11.2GB
    )
    assert plan.clustering_strategy == "distributed_wcc"


def test_clustering_env_threshold_zero_is_recorded_as_override():
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0), cluster=_cluster(),
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"},
    )
    clu = [d for d in plan.routing_decisions if d.stage == "clustering"][0]
    assert plan.clustering_strategy == "distributed_wcc"  # override honored
    assert clu.overridden is True
    assert "CLUSTERING_THRESHOLD=0" in clu.override_source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_distributed_routing_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'goldenmatch.core.distributed_routing_rules'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/distributed_routing_rules.py
"""Distributed-routing rule layer: a post-pass over the 7 backend rules that
decides scoring / clustering / golden routing per stage, keyed off DRIVER RAM.

Single source of truth for distributed routing. Env-var thresholds and config
pins are honored but recorded as overrides so the MCP linter can flag the ones
that force a slow path at scale.
"""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Mapping

from goldenmatch.core.cluster_profile import ClusterProfile
from goldenmatch.core.execution_plan import DistributedRoutingDecision, ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile

_GIB = 1024 ** 3
# Measured against the validated 100M run (docs/quality-invariant-scale.md):
# an edge is two int32 ids + a float32 score, padded -> 16 bytes.
BYTES_PER_EDGE = 16
# Per-record materialized working set (raw fields + normalized blocking columns
# + scoring scratch) for the scoring frame and the golden survivorship build.
# Calibrated against the validated 100M runs: 100M x 512B = 51GB exceeds a 48GB
# cluster driver (so scoring/golden distribute there) but fits a 256GB single
# box (so the single-box 100M run stays in-memory). Hardware-relative by design.
BYTES_PER_ROW = 512
# Headroom: never plan to fill more than this fraction of driver RAM.
SAFETY = 0.6

ROUTING_DOC_ANCHORS: dict[str, str] = {
    "single_box": "routing-single-box",
    "cluster_present": "routing-driver-ram-projection",
    "user_override": "routing-overrides",
}

# DistributedRoutingConfig pin values -> normalized internal mode.
_PIN_TO_MODE = {
    "distributed": "distributed", "in_process": "in_memory",
    "distributed_wcc": "distributed", "in_memory_scipy": "in_memory",
    "auto": None, None: None,
}


def _driver_avail_ram_gb(runtime: RuntimeProfile | None, cluster: ClusterProfile) -> float:
    if runtime is not None:
        return runtime.available_ram_gb
    return cluster.driver_mem_gb


def _human(n: int) -> str:
    return f"{n / 1e9:.2f}GB"


def _config_pin(routing_config, stage: str):
    """Return (mode, source) for an explicit DistributedRoutingConfig pin."""
    if routing_config is None:
        return None, None
    raw = getattr(routing_config, stage, "auto")
    mode = _PIN_TO_MODE.get(raw)
    if mode is None:
        return None, None
    return mode, f"config:distributed_routing.{stage}={raw}"


def _clustering_env_override(env: Mapping[str, str], pairs: int, projection_distribute: bool):
    raw = env.get("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD")
    if raw is None:
        return None, None
    try:
        thr = int(raw)
    except ValueError:
        return None, None
    env_distribute = pairs >= thr
    if env_distribute == projection_distribute:
        return None, None  # env agrees with the projection; not an override
    mode = "distributed" if env_distribute else "in_memory"
    return mode, f"env:GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD={raw}"


def _decide(stage, *, projected_bytes, budget_bytes, cluster, override_mode, override_source):
    if override_mode is not None:
        return DistributedRoutingDecision(
            stage=stage, mode=override_mode, rule_name="user_override",
            reason=f"{stage} pinned to {override_mode} via {override_source}",
            projected_bytes=projected_bytes, budget_bytes=budget_bytes,
            overridden=True, override_source=override_source,
        )
    if not cluster.present:
        return DistributedRoutingDecision(
            stage=stage, mode="in_memory", rule_name="single_box",
            reason=f"{stage} in-memory: no cluster present",
            projected_bytes=projected_bytes, budget_bytes=budget_bytes,
            overridden=False, override_source=None,
        )
    distribute = projected_bytes > budget_bytes
    mode = "distributed" if distribute else "in_memory"
    op = ">" if distribute else "<="
    return DistributedRoutingDecision(
        stage=stage, mode=mode, rule_name="cluster_present",
        reason=(f"{stage} {mode}: projected {_human(projected_bytes)} {op} "
                f"driver budget {_human(budget_bytes)}"),
        projected_bytes=projected_bytes, budget_bytes=budget_bytes,
        overridden=False, override_source=None,
    )


def apply_distributed_routing(
    plan: ExecutionPlan,
    *,
    runtime: RuntimeProfile | None,
    cluster: ClusterProfile,
    n_rows_full: int,
    estimated_pair_count: int,
    routing_config=None,
    env: Mapping[str, str] | None = None,
) -> ExecutionPlan:
    """Return a new ExecutionPlan with per-stage routing populated."""
    env = os.environ if env is None else env
    budget = int(_driver_avail_ram_gb(runtime, cluster) * _GIB * SAFETY)
    row_bytes = n_rows_full * BYTES_PER_ROW
    edge_bytes = estimated_pair_count * BYTES_PER_EDGE

    # scoring
    s_mode, s_src = _config_pin(routing_config, "scoring")
    scoring = _decide("scoring", projected_bytes=row_bytes, budget_bytes=budget,
                      cluster=cluster, override_mode=s_mode, override_source=s_src)

    # clustering (config pin wins; else env-threshold footgun; else projection)
    c_mode, c_src = _config_pin(routing_config, "clustering")
    if c_mode is None:
        c_mode, c_src = _clustering_env_override(
            env, estimated_pair_count,
            projection_distribute=(cluster.present and edge_bytes > budget))
    clustering = _decide("clustering", projected_bytes=edge_bytes, budget_bytes=budget,
                         cluster=cluster, override_mode=c_mode, override_source=c_src)

    # golden
    g_mode, g_src = _config_pin(routing_config, "golden")
    golden = _decide("golden", projected_bytes=row_bytes, budget_bytes=budget,
                     cluster=cluster, override_mode=g_mode, override_source=g_src)

    return replace(
        plan,
        scoring_distributed=(scoring.mode == "distributed"),
        clustering_strategy=("distributed_wcc" if clustering.mode == "distributed" else "in_memory"),
        golden_distributed=(golden.mode == "distributed"),
        routing_decisions=(scoring, clustering, golden),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_distributed_routing_rules.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/distributed_routing_rules.py packages/python/goldenmatch/tests/test_distributed_routing_rules.py
git commit -m "feat(routing): per-stage distributed routing rule layer (driver-RAM projection)"
```

---

### Task 4: SlowPathRefusedError + enforce_routing (blocking at scale)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/distributed_routing_rules.py` (append)
- Test: `packages/python/goldenmatch/tests/test_routing_enforcement.py`

Reuse the existing scale gate `REFUSE_AT_N = 100_000` (lazy import to avoid a circular import with the controller). A "slow-path override" is an overridden decision whose mode contradicts its own projection.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routing_enforcement.py
import pytest

from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.distributed_routing_rules import (
    SlowPathRefusedError, apply_distributed_routing, enforce_routing,
)
from goldenmatch.core.cluster_profile import ClusterProfile
from goldenmatch.core.runtime_profile import RuntimeProfile


def _plan_with_threshold_zero():
    cluster = ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                             cluster_mem_gb=256.0, driver_mem_gb=48.0, source="descriptor")
    return apply_distributed_routing(
        ExecutionPlan(), runtime=RuntimeProfile(48.0, 16, 500.0), cluster=cluster,
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"},
    )


def test_refuses_slow_override_at_scale():
    plan = _plan_with_threshold_zero()
    with pytest.raises(SlowPathRefusedError):
        enforce_routing(plan, n_rows=100_000_000, allow_slow_path=False)


def test_advisory_below_scale():
    plan = _plan_with_threshold_zero()
    enforce_routing(plan, n_rows=1_000, allow_slow_path=False)  # no raise


def test_allow_slow_path_acks():
    plan = _plan_with_threshold_zero()
    enforce_routing(plan, n_rows=100_000_000, allow_slow_path=True)  # no raise


def test_clean_plan_never_refuses():
    cluster = ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                             cluster_mem_gb=256.0, driver_mem_gb=48.0, source="descriptor")
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=RuntimeProfile(48.0, 16, 500.0), cluster=cluster,
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
    )
    enforce_routing(plan, n_rows=100_000_000, allow_slow_path=False)  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_routing_enforcement.py -v`
Expected: FAIL with `ImportError: cannot import name 'SlowPathRefusedError'`

- [ ] **Step 3: Write minimal implementation** (append to `distributed_routing_rules.py`)

```python
def _projection_mode(d) -> str:
    return "distributed" if d.projected_bytes > d.budget_bytes else "in_memory"


def slow_path_overrides(plan: ExecutionPlan):
    """Overridden decisions whose mode contradicts their own projection."""
    return [d for d in plan.routing_decisions
            if d.overridden and d.mode != _projection_mode(d)]


class SlowPathRefusedError(Exception):
    """Raised when a slow-path override is present on a large input and the
    caller has not passed allow_slow_path=True."""

    def __init__(self, *, decisions, n_rows: int) -> None:
        self.decisions = decisions
        self.n_rows = n_rows
        joined = "; ".join(
            f"{d.stage} forced {d.mode} via {d.override_source} "
            f"(projection: {_projection_mode(d)})" for d in decisions)
        super().__init__(
            f"Refusing slow-path config at n_rows={n_rows:,}: {joined}. "
            f"Pass allow_slow_path=true to proceed anyway.")


def enforce_routing(plan: ExecutionPlan, *, n_rows: int, allow_slow_path: bool) -> None:
    if allow_slow_path:
        return
    from goldenmatch.core.autoconfig_controller import REFUSE_AT_N
    if n_rows < REFUSE_AT_N:
        return
    bad = slow_path_overrides(plan)
    if bad:
        raise SlowPathRefusedError(decisions=bad, n_rows=n_rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_routing_enforcement.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/distributed_routing_rules.py packages/python/goldenmatch/tests/test_routing_enforcement.py
git commit -m "feat(routing): SlowPathRefusedError + enforce_routing (blocking at scale)"
```

---

### Task 5: DistributedRoutingConfig + allow_slow_path on GoldenMatchConfig

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (near the nested-config pattern ~620-645 and `GoldenMatchConfig` ~728-763)
- Test: `packages/python/goldenmatch/tests/test_distributed_routing_config.py`

Mirror the `LLMScorerConfig` / `BudgetConfig` nesting pattern (pydantic v2). Defaults = `auto` / `False` so existing configs round-trip unchanged (this protects the frozen-config drift test).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_distributed_routing_config.py
from goldenmatch.config.schemas import DistributedRoutingConfig, GoldenMatchConfig


def test_defaults_are_auto_and_no_slow_path():
    cfg = GoldenMatchConfig()
    assert cfg.allow_slow_path is False
    assert cfg.distributed_routing is None


def test_nested_routing_config_parses():
    cfg = GoldenMatchConfig.model_validate({
        "allow_slow_path": True,
        "distributed_routing": {"clustering": "in_memory_scipy", "scoring": "distributed"},
    })
    assert cfg.allow_slow_path is True
    assert cfg.distributed_routing.clustering == "in_memory_scipy"
    assert cfg.distributed_routing.scoring == "distributed"
    assert cfg.distributed_routing.golden == "auto"


def test_rejects_bad_enum():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DistributedRoutingConfig(clustering="turbo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_distributed_routing_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'DistributedRoutingConfig'`

- [ ] **Step 3: Write minimal implementation**

Add the sub-model near the other nested configs (e.g. before `GoldenMatchConfig`):

```python
class DistributedRoutingConfig(BaseModel):
    """Per-stage distributed-routing pins. ``auto`` lets the planner decide;
    an explicit value pins the stage and is surfaced by the linter."""
    scoring: Literal["auto", "distributed", "in_process"] = "auto"
    clustering: Literal["auto", "distributed_wcc", "in_memory_scipy"] = "auto"
    golden: Literal["auto", "distributed", "in_process"] = "auto"
```

Add fields to `GoldenMatchConfig` (beside `backend` / `mode`):

```python
    distributed_routing: DistributedRoutingConfig | None = None
    allow_slow_path: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_distributed_routing_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Guard the frozen-config drift test still passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_qis_gen_parity.py -v`
Expected: PASS (defaults keep the frozen config's `model_dump()` unchanged)

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_distributed_routing_config.py
git commit -m "feat(routing): DistributedRoutingConfig + allow_slow_path on GoldenMatchConfig"
```

---

### Task 6: Wire routing into the controller

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py:1191-1254`
- Test: `packages/python/goldenmatch/tests/test_controller_routing_integration.py`

Insert cluster capture + routing right after `apply_planner_rules` returns. Single-box by default (no Ray), so default behavior is unchanged; the plan simply gains a populated `routing_decisions` trace. Pull the override config + `allow_slow_path` from `committed_config`, and call `enforce_routing` so a slow-path override at scale refuses here too.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_controller_routing_integration.py
"""On a small single-box input the controller's ExecutionPlan must carry a
routing trace with all stages in-memory (no behavior change)."""
import polars as pl

from goldenmatch.core.autoconfig_controller import AutoConfigController


def test_controller_populates_routing_trace():
    df = pl.DataFrame({
        "id": list(range(40)),
        "name": ["Alice", "Bob", "Carol", "Dave"] * 10,
        "email": [f"u{i % 8}@x.com" for i in range(40)],
    })
    controller = AutoConfigController()
    # run() takes a Polars DataFrame and returns (committed_config, profile, history)
    # -- see tests/test_autoconfig_controller.py for the canonical call.
    _config, _profile, history = controller.run(df)
    plan = history.execution_plan
    assert plan.routing_decisions != ()
    assert plan.clustering_strategy == "in_memory"
    assert {d.stage for d in plan.routing_decisions} == {"scoring", "clustering", "golden"}
    assert all(d.rule_name == "single_box" for d in plan.routing_decisions)
```

NOTE to implementer: confirm the exact `run()` call against `tests/test_autoconfig_controller.py` (it may need extra constructor args or a `constraints` kwarg). The contract is `config, profile, history = controller.run(df)` with a DataFrame input. Keep the four assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_controller_routing_integration.py -v`
Expected: FAIL (`routing_decisions == ()`)

- [ ] **Step 3: Write minimal implementation**

In `autoconfig_controller.py`, extend the imports block (line ~1191-1193) and the planner call (line ~1246-1254):

```python
        from goldenmatch.core.autoconfig_planner import apply_planner_rules
        from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
        from goldenmatch.core.runtime_profile import capture_runtime_profile
        from goldenmatch.core.cluster_profile import capture_cluster_profile
        from goldenmatch.core.distributed_routing_rules import (
            apply_distributed_routing, enforce_routing,
        )

        runtime = capture_runtime_profile()
        ...
        plan = apply_planner_rules(
            profile=profile_for_planner,
            runtime=runtime,
            n_rows_full=n_rows,
            rules=DEFAULT_RULES,
            context={"user_backend": None},
        )
        # Phase: distributed-routing post-pass. Single-box by default (no Ray),
        # so this is behavior-preserving; it populates the routing trace.
        cluster = capture_cluster_profile()
        plan = apply_distributed_routing(
            plan,
            runtime=runtime,
            cluster=cluster,
            n_rows_full=n_rows,
            estimated_pair_count=profile_for_planner.blocking.estimated_pair_count,
            routing_config=getattr(committed_config, "distributed_routing", None),
        )
        enforce_routing(
            plan, n_rows=n_rows,
            allow_slow_path=getattr(committed_config, "allow_slow_path", False),
        )
        plan.apply_to(committed_config)
        history.execution_plan = plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_controller_routing_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py packages/python/goldenmatch/tests/test_controller_routing_integration.py
git commit -m "feat(routing): wire cluster capture + routing post-pass into the controller"
```

---

### Task 7: Telemetry extension (routing trace in execution_plan)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/web/controller_telemetry.py:296-315`
- Test: `packages/python/goldenmatch/tests/test_telemetry_routing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telemetry_routing.py
from types import SimpleNamespace

from goldenmatch.core.execution_plan import DistributedRoutingDecision, ExecutionPlan
from goldenmatch.web.controller_telemetry import serialize_telemetry


def test_execution_plan_includes_routing():
    dec = DistributedRoutingDecision(
        stage="clustering", mode="in_memory", rule_name="cluster_present",
        reason="edge set 1.76GB <= budget 28.8GB", projected_bytes=1_760_000_000,
        budget_bytes=28_800_000_000, overridden=False, override_source=None)
    plan = ExecutionPlan(scoring_distributed=True, routing_decisions=(dec,))
    history = SimpleNamespace(execution_plan=plan)
    body = serialize_telemetry(
        profile=None, history=history, committed_config=None,
        source=None, run_name=None, recorded_at=None)
    ep = body["execution_plan"]
    assert ep["scoring_distributed"] is True
    assert ep["routing"][0]["stage"] == "clustering"
    assert ep["routing"][0]["reason"].startswith("edge set")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_telemetry_routing.py -v`
Expected: FAIL with `KeyError: 'routing'`

- [ ] **Step 3: Write minimal implementation**

In `_execution_plan(history)`, extend the returned dict (use `getattr` defaults so pre-routing plans still serialize):

```python
    from dataclasses import asdict
    return {
        "rule_name": plan.rule_name,
        "backend": plan.backend,
        "chunk_size": plan.chunk_size,
        "max_workers": plan.max_workers,
        "pair_spill_threshold": plan.pair_spill_threshold,
        "clustering_strategy": plan.clustering_strategy,
        "scoring_distributed": getattr(plan, "scoring_distributed", False),
        "golden_distributed": getattr(plan, "golden_distributed", False),
        "routing": [asdict(d) for d in getattr(plan, "routing_decisions", ())],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_telemetry_routing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/web/controller_telemetry.py packages/python/goldenmatch/tests/test_telemetry_routing.py
git commit -m "feat(routing): serialize routing trace in controller telemetry"
```

---

### Task 8: Distributed clustering call-site cutover (planner decision wins, env fallback)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/distributed/clustering.py` (the `use_label_prop` decision + `_label_prop_threshold` ~line 132/167)
- Test: `packages/python/goldenmatch/tests/test_clustering_routing_override.py`

The distributed clustering entry currently decides `use_label_prop` purely from the env threshold. Add an optional `clustering_strategy: str | None = None` parameter to that entry function: when `"in_memory"` or `"distributed_wcc"` is passed (from the planner's ExecutionPlan), it overrides the env threshold; when `None`, today's env logic runs unchanged. This is the behavior-preserving cutover the spec's Rollout step 5 calls for.

NOTE to implementer: locate the exact public entry that computes `use_label_prop` (grep `use_label_prop` in `distributed/clustering.py`). Thread the new optional arg from its caller in `distributed/pipeline.py` where the ExecutionPlan/clustering_strategy is available. Keep the env path as the default.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clustering_routing_override.py
"""The clustering decision must honor an explicit strategy over the env
threshold, and fall back to the env threshold when no strategy is given."""
from goldenmatch.distributed import clustering


def test_explicit_in_memory_overrides_env_threshold_zero(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
    # With strategy="in_memory", threshold=0 must NOT force label-prop.
    decided = clustering._resolve_use_label_prop(
        pair_count=1000, clustering_strategy="in_memory")
    assert decided is False


def test_env_fallback_when_no_strategy(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
    decided = clustering._resolve_use_label_prop(
        pair_count=1000, clustering_strategy=None)
    assert decided is True  # threshold 0 => always distribute (today's behavior)


def test_explicit_distributed_forces_label_prop(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", raising=False)
    decided = clustering._resolve_use_label_prop(
        pair_count=10, clustering_strategy="distributed_wcc")
    assert decided is True
```

NOTE to implementer: introduce a small `_resolve_use_label_prop(pair_count, clustering_strategy=None, *, force_label_propagation=False)` helper that encapsulates the decision, and call it from the existing `use_label_prop = ...` site. This makes the override unit-testable without standing up Ray. Preserve the existing `force_label_propagation` semantics.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_clustering_routing_override.py -v`
Expected: FAIL (`_resolve_use_label_prop` does not exist)

- [ ] **Step 3: Write minimal implementation**

```python
# in distributed/clustering.py, near _label_prop_threshold
def _resolve_use_label_prop(
    pair_count: int,
    clustering_strategy: str | None = None,
    *,
    force_label_propagation: bool = False,
) -> bool:
    """Decide distributed (label-prop) vs in-memory WCC. An explicit planner
    strategy wins; otherwise fall back to the env threshold (today's path)."""
    if force_label_propagation:
        return True
    if clustering_strategy == "in_memory":
        return False
    if clustering_strategy == "distributed_wcc":
        return True
    return pair_count >= _label_prop_threshold()
```

Then replace the existing `use_label_prop = force_label_propagation or pair_count >= _label_prop_threshold()` site with a call to `_resolve_use_label_prop(pair_count, clustering_strategy, force_label_propagation=force_label_propagation)`, threading `clustering_strategy` down from the pipeline caller (default `None`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_clustering_routing_override.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/distributed/clustering.py packages/python/goldenmatch/tests/test_clustering_routing_override.py
git commit -m "feat(routing): clustering honors planner strategy over env threshold (env fallback preserved)"
```

---

### Task 9: MCP plan_routing / explain_routing / lint_routing tools

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/mcp/routing_tools.py` (pure helpers + Tool defs)
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` (register the three Tools into the exported list)
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/server.py` (dispatch the three tool names)
- Test: `packages/python/goldenmatch/tests/test_mcp_routing_tools.py`

These three tools are PURE functions over the routing layer (inputs: `n_rows`, `estimated_pair_count`, optional `driver_mem_gb`, optional `cluster` descriptor, optional `config` / `env`). They do not re-run the controller — `auto_configure` already returns the full plan + telemetry (now incl. routing from Task 7); these expose the routing decision/explanation/lint standalone. (Reconciliation with the spec's "plan/explain/lint": `plan_routing` is the spec's `plan` as a pure projection; the heavy sample→profile path stays in `auto_configure`.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_routing_tools.py
from goldenmatch.mcp.routing_tools import (
    run_plan_routing, run_explain_routing, run_lint_routing,
)


def _cluster():
    return {"num_nodes": 4, "total_cpus": 80, "cluster_mem_gb": 256.0, "driver_mem_gb": 48.0}


def test_plan_routing_100m_in_memory_clustering():
    out = run_plan_routing(n_rows=100_000_000, estimated_pair_count=110_000_000,
                           cluster=_cluster())
    assert out["clustering_strategy"] == "in_memory"
    assert out["scoring_distributed"] is True


def test_explain_routing_is_human_readable():
    out = run_explain_routing(n_rows=100_000_000, estimated_pair_count=110_000_000,
                              cluster=_cluster())
    text = out["explanation"]
    assert "clustering" in text and "driver budget" in text


def test_lint_flags_threshold_zero_as_error_at_scale():
    out = run_lint_routing(
        n_rows=100_000_000, estimated_pair_count=110_000_000, cluster=_cluster(),
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"})
    errors = [f for f in out["findings"] if f["severity"] == "ERROR"]
    assert errors and errors[0]["stage"] == "clustering"
    assert out["would_refuse"] is True


def test_lint_threshold_zero_is_advisory_below_scale():
    out = run_lint_routing(
        n_rows=1_000, estimated_pair_count=1100, cluster=_cluster(),
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"})
    assert out["would_refuse"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_mcp_routing_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'goldenmatch.mcp.routing_tools'`

- [ ] **Step 3: Write minimal implementation**

```python
# mcp/routing_tools.py
"""Pure routing-projection helpers behind the plan/explain/lint MCP tools."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from mcp.types import Tool

from goldenmatch.core.cluster_profile import capture_cluster_profile
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.distributed_routing_rules import (
    apply_distributed_routing, slow_path_overrides, _projection_mode,
)
from goldenmatch.core.runtime_profile import RuntimeProfile

_REFUSE_AT_N = 100_000  # mirrors core.autoconfig_controller.REFUSE_AT_N (asserted in tests)


def _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env):
    cluster_profile = capture_cluster_profile(descriptor=cluster, ray_module=None)
    runtime = RuntimeProfile(available_ram_gb=driver_mem_gb, cpu_count=1, disk_free_gb=0.0) \
        if driver_mem_gb is not None else None
    return apply_distributed_routing(
        ExecutionPlan(), runtime=runtime, cluster=cluster_profile,
        n_rows_full=n_rows, estimated_pair_count=estimated_pair_count,
        routing_config=config, env=env or {})


def run_plan_routing(*, n_rows, estimated_pair_count, cluster=None,
                     driver_mem_gb=None, config=None, env=None) -> dict[str, Any]:
    plan = _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env)
    return {
        "clustering_strategy": plan.clustering_strategy,
        "scoring_distributed": plan.scoring_distributed,
        "golden_distributed": plan.golden_distributed,
        "routing": [asdict(d) for d in plan.routing_decisions],
    }


def run_explain_routing(*, n_rows, estimated_pair_count, cluster=None,
                        driver_mem_gb=None, config=None, env=None) -> dict[str, Any]:
    plan = _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env)
    lines = [f"- {d.reason} (rule {d.rule_name})" for d in plan.routing_decisions]
    return {"explanation": "\n".join(lines), "routing": [asdict(d) for d in plan.routing_decisions]}


def run_lint_routing(*, n_rows, estimated_pair_count, cluster=None,
                     driver_mem_gb=None, config=None, env=None) -> dict[str, Any]:
    plan = _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env)
    at_scale = n_rows >= _REFUSE_AT_N
    findings = []
    for d in plan.routing_decisions:
        if not d.overridden:
            continue
        slow = d.mode != _projection_mode(d)
        sev = ("ERROR" if (slow and at_scale) else "WARN" if slow else "INFO")
        findings.append({
            "stage": d.stage, "severity": sev, "mode": d.mode,
            "projection": _projection_mode(d), "source": d.override_source,
            "message": (f"{d.stage} forced {d.mode} via {d.override_source}; "
                        f"projection says {_projection_mode(d)}."),
        })
    would_refuse = at_scale and bool(slow_path_overrides(plan))
    return {"findings": findings, "would_refuse": would_refuse}


_CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "num_nodes": {"type": "integer"}, "total_cpus": {"type": "integer"},
        "cluster_mem_gb": {"type": "number"}, "driver_mem_gb": {"type": "number"},
    },
}
_BASE_PROPS = {
    "n_rows": {"type": "integer"},
    "estimated_pair_count": {"type": "integer"},
    "cluster": _CLUSTER_SCHEMA,
    "driver_mem_gb": {"type": "number"},
}

ROUTING_TOOLS = [
    Tool(name="plan_routing",
         description="Project per-stage distributed routing (scoring/clustering/golden) "
                     "for a given data shape + cluster. Pure; no controller run.",
         inputSchema={"type": "object", "properties": _BASE_PROPS,
                      "required": ["n_rows", "estimated_pair_count"]}),
    Tool(name="explain_routing",
         description="Human-readable explanation of why each stage is routed the way it is, "
                     "with the driver-RAM projection that drove it.",
         inputSchema={"type": "object", "properties": _BASE_PROPS,
                      "required": ["n_rows", "estimated_pair_count"]}),
    Tool(name="lint_routing",
         description="Flag config/env overrides that force a slow path (e.g. "
                     "CLUSTERING_THRESHOLD=0 when the edge set fits driver RAM). "
                     "ERROR at scale; would_refuse mirrors the runtime guard.",
         inputSchema={"type": "object",
                      "properties": {**_BASE_PROPS, "env": {"type": "object"}},
                      "required": ["n_rows", "estimated_pair_count"]}),
]

_DISPATCH = {
    "plan_routing": run_plan_routing,
    "explain_routing": run_explain_routing,
    "lint_routing": run_lint_routing,
}


def handle_routing_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _DISPATCH[name](**arguments)
```

Then: (a) in `agent_tools.py`, import `ROUTING_TOOLS` and extend the exported tool list with it; (b) in `server.py`, add a dispatch branch routing the three names to `handle_routing_tool`. **Dispatch caution:** `server.py` has two return conventions — `_AGENT_TOOL_NAMES` tools return pre-wrapped `list[TextContent]` via `handle_agent_tool`, while base tools return a dict that the `else` branch wraps with `json.dumps(...)`. `handle_routing_tool` returns a plain dict, so route the three names through the **json-wrapping (base-tool) path** — do NOT add them to `_AGENT_TOOL_NAMES`, or you double-wrap. Match the exact list name + dispatch convention already in those files.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_mcp_routing_tools.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Assert the mirrored constant matches**

Add to `tests/test_mcp_routing_tools.py`:

```python
def test_refuse_constant_mirrors_controller():
    from goldenmatch.core.autoconfig_controller import REFUSE_AT_N
    from goldenmatch.mcp.routing_tools import _REFUSE_AT_N
    assert _REFUSE_AT_N == REFUSE_AT_N
```

Run the file again; expected PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/mcp/routing_tools.py packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py packages/python/goldenmatch/goldenmatch/mcp/server.py packages/python/goldenmatch/tests/test_mcp_routing_tools.py
git commit -m "feat(routing): MCP plan_routing/explain_routing/lint_routing tools"
```

---

### Task 10: Documentation-awareness (rule metadata + doc-drift test)

**Files:**
- Modify: `docs-site/goldenmatch/tuning.mdx` (add the anchors named in `ROUTING_DOC_ANCHORS`)
- Test: `packages/python/goldenmatch/tests/test_routing_doc_drift.py`

Model on the frozen-config drift test (`test_qis_gen_parity.py`). Anchor `tuning.mdx` from `__file__` (CWD differs local vs CI). Each `ROUTING_DOC_ANCHORS` value must appear in `tuning.mdx`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routing_doc_drift.py
from pathlib import Path

from goldenmatch.core.distributed_routing_rules import ROUTING_DOC_ANCHORS

_TUNING = Path(__file__).resolve().parents[4] / "docs-site" / "goldenmatch" / "tuning.mdx"


def test_tuning_doc_exists():
    assert _TUNING.is_file(), f"missing {_TUNING}"


def test_every_routing_rule_has_a_doc_anchor():
    text = _TUNING.read_text(encoding="utf-8")
    missing = [a for a in ROUTING_DOC_ANCHORS.values() if a not in text]
    assert not missing, (
        f"tuning.mdx is missing anchors for routing rules: {missing}. "
        f"Add a section per anchor so explain/lint links resolve.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_routing_doc_drift.py -v`
Expected: FAIL (`test_every_routing_rule_has_a_doc_anchor` — anchors absent)

- [ ] **Step 3: Add the anchors to `tuning.mdx`**

Append a "Distributed routing" section to `docs-site/goldenmatch/tuning.mdx` with a subsection per anchor id (`routing-single-box`, `routing-driver-ram-projection`, `routing-overrides`). Each subsection explains: the planner routes scoring/clustering/golden per stage; clustering distributes only when the projected edge set exceeds driver RAM; env-var thresholds are linted overrides. Include the literal anchor strings so the drift test resolves them, e.g.:

```mdx
## Distributed routing

### Single box {#routing-single-box}
With no Ray cluster connected, every stage runs in-memory / in-process.

### Driver-RAM projection {#routing-driver-ram-projection}
Clustering distributes only when the projected edge set
(`estimated_pair_count × 16 bytes`) exceeds the driver-RAM budget. At 100M rows
the ~1.76 GB edge set fits, so WCC runs in-memory (scipy).

### Overrides {#routing-overrides}
`distributed_routing.*` pins and the legacy `GOLDENMATCH_DISTRIBUTED_*`
thresholds are honored but linted. Forcing a slow path at scale refuses unless
`allow_slow_path=true`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest tests/test_routing_doc_drift.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add docs-site/goldenmatch/tuning.mdx packages/python/goldenmatch/tests/test_routing_doc_drift.py
git commit -m "docs(routing): tuning.mdx routing anchors + doc-drift test"
```

---

### Task 11: Regression guard + final review

**Files:** none new — verification only.

- [ ] **Step 1: Run the #491/#715 backend-rule benchmark tests (must not regress)**

Run:
```bash
cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest \
  tests/test_autoconfig_491_levers.py \
  tests/test_autoconfig_blocking_cost_715.py -v
```
Expected: PASS (same counts as on `origin/main` before this branch). The routing layer runs AFTER the backend rules and never touches `backend`, so these must be unchanged. If any fail, the routing post-pass leaked into backend selection — fix before proceeding.

- [ ] **Step 2: Run the full new-test set together**

Run:
```bash
cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 python -m pytest \
  tests/test_cluster_profile.py tests/test_execution_plan_routing_fields.py \
  tests/test_distributed_routing_rules.py tests/test_routing_enforcement.py \
  tests/test_distributed_routing_config.py tests/test_controller_routing_integration.py \
  tests/test_telemetry_routing.py tests/test_clustering_routing_override.py \
  tests/test_mcp_routing_tools.py tests/test_routing_doc_drift.py \
  tests/test_qis_gen_parity.py -v
```
Expected: all PASS.

- [ ] **Step 3: Dispatch the final code-reviewer subagent** for the whole branch diff (`git diff origin/main...HEAD`), checking spec compliance + the regression guard. Address any blocking findings.

- [ ] **Step 4: Finish the branch** via superpowers:finishing-a-development-branch (open the PR; do NOT push to `main`). The user merges on green per the branch/merge SOP.

---

## Notes for the implementer

- **YAGNI calls baked in (flag if you disagree):** (1) env-var provenance is wired only for the proven `CLUSTERING_THRESHOLD` footgun + the explicit `DistributedRoutingConfig` pins; the golden/scoring env vars (`_GOLDEN_THRESHOLD`, `_SCORE_NUM_CPUS`) are left on their existing defaults and noted as a follow-up. (2) The MCP tools consume numeric profile inputs rather than re-running the controller; `auto_configure` remains the sample→plan entry.
- **`allow_slow_path` ack surface (spec §6 lists three entry points; this plan ships one):** the supported ack is the **`GoldenMatchConfig.allow_slow_path` config field** (Task 5), enforced by the controller (Task 6). The dedicated `dedupe_df`/`match_df` kwarg and an MCP ack arg are **deferred** — `lint_routing` reports `would_refuse` but does not accept an ack. This is a deliberate reduction (config-field ack covers every caller that passes a config); the Task 11 reviewer should read it as scoped, not a silent miss.
- **Behavior preservation is the contract:** the single-box default path (no Ray) must produce byte-identical plans except for the added `routing_decisions` trace. Tasks 6 and 11 guard this.
- **Circular-import care:** `DistributedRoutingDecision` lives in `execution_plan.py`; `enforce_routing` lazy-imports `REFUSE_AT_N`. Do not move these.
