"""Pipeline resolver -- dependency-DAG planner (spec §3.1). Config-order-authoritative
with a virtual `df` seed, `needs` + guarded sole-producer edges, stable Kahn sort."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import PipelineConfig, StageSpec


class WiringError(Exception):
    """A consumed artifact no stage (nor the `df` seed) produces (spec: MissingProducer).
    Name retained for back-compat with existing `except WiringError` sites. Carries
    optional structured attrs for the parity helper; `str(e)` still works."""

    def __init__(self, message: str, *, stage: str | None = None, artifact: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.artifact = artifact
        self.missing = artifact  # legacy alias (old attr name)


class AmbiguousProducerError(Exception):
    def __init__(self, artifact: str, producers: list[str]) -> None:
        super().__init__(f"Artifact '{artifact}' has ambiguous producers: {producers}")
        self.artifact = artifact
        self.producers = producers


class CycleError(Exception):
    def __init__(self, stages: list[str]) -> None:
        super().__init__(f"Dependency cycle among stages: {stages}")
        self.stages = stages


class UnknownNeedError(Exception):
    def __init__(self, stage: str, needs: list[str]) -> None:
        super().__init__(f"Stage '{stage}' needs unknown stage(s): {needs}")
        self.stage = stage
        self.needs = needs


@dataclass
class PlannedStage:
    """A resolved stage ready for execution."""
    name: str
    stage: Any
    spec: StageSpec
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Ordered list of stages to execute."""
    stages: list[PlannedStage] = field(default_factory=list)


@dataclass
class _Node:
    pname: str
    use: str
    produces: list[str]
    consumes: list[str]
    needs: list[str]
    planned: PlannedStage


class Resolver:
    """Builds and validates an ExecutionPlan from config + registry (DAG contract)."""

    @staticmethod
    def resolve(config: PipelineConfig, registry: StageRegistry) -> ExecutionPlan:
        # 1. Build the ordered node list (load prepended; KeyError on bad `use`).
        nodes: list[_Node] = []
        has_load = True
        try:
            load = registry.get("load")
        except KeyError:
            has_load = False
        if has_load:
            nodes.append(_Node(
                pname="load", use="load", produces=list(load.info.produces), consumes=[],
                needs=[], planned=PlannedStage(name="load", stage=load, spec=StageSpec(use="load")),
            ))
        for raw in config.stages:
            spec = StageSpec(use=raw) if isinstance(raw, str) else raw
            stage_obj = registry.get(spec.use)  # KeyError -> unknown_stage (mapped by the shim)
            pname = spec.name or stage_obj.info.name
            nodes.append(_Node(
                pname=pname, use=spec.use,
                produces=list(stage_obj.info.produces), consumes=list(stage_obj.info.consumes),
                needs=list(spec.needs),
                planned=PlannedStage(name=pname, stage=stage_obj, spec=spec, config=spec.config),
            ))
        n = len(nodes)
        seed_df = not has_load

        def key_to_idx(k: str) -> int | None:
            for idx, nd in enumerate(nodes):
                if nd.use == k:
                    return idx
            return None

        edges: set[tuple[int, int]] = set()

        # 2. needs edges (reported before missing/ambiguous).
        for i, nd in enumerate(nodes):
            for need in nd.needs:
                j = key_to_idx(need)
                if j is None:
                    raise UnknownNeedError(nd.pname, [need])
                edges.add((j, i))  # self-edge -> Cycle in step 4

        # 3. Guarded sole-producer edges. First violation (by index, then consumes order) wins.
        def produced_before(i: int, x: str) -> bool:
            if seed_df and x == "df":
                return True
            return any(x in nodes[j].produces for j in range(i))

        for i in range(n):
            for dep in nodes[i].consumes:
                if produced_before(i, dep):
                    continue
                later = [j for j in range(i + 1, n) if dep in nodes[j].produces]
                if len(later) == 0:
                    raise WiringError(
                        f"Stage '{nodes[i].pname}' consumes '{dep}' but no prior stage produces it.",
                        stage=nodes[i].pname, artifact=dep,
                    )
                if len(later) == 1:
                    edges.add((later[0], i))
                else:
                    # pinned by ANY must-precede edge (needs OR an earlier-consumes sole-producer
                    # edge): exactly one -> deterministic binding; else AmbiguousProducer.
                    pinned = sum(1 for j in later if (j, i) in edges)
                    if pinned != 1:
                        raise AmbiguousProducerError(dep, [nodes[j].use for j in later])

        # 4. Stable Kahn topo-sort keyed by config index (min-heap).
        indeg = [0] * n
        adj: list[list[int]] = [[] for _ in range(n)]
        for a, b in edges:
            if a == b:
                indeg[b] += 1  # self-edge -> stuck node, reported by the ascending-index fall-through
                continue
            adj[a].append(b)
            indeg[b] += 1
        heap = [i for i in range(n) if indeg[i] == 0]
        heapq.heapify(heap)
        order: list[int] = []
        while heap:
            u = heapq.heappop(heap)
            order.append(u)
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    heapq.heappush(heap, v)
        if len(order) != n:
            raise CycleError([nodes[i].pname for i in range(n) if indeg[i] > 0])

        # 5. Emit in sorted order.
        return ExecutionPlan(stages=[nodes[i].planned for i in order])
