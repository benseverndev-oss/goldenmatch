"""Opt-in compiler entry point: reuses the classic Runner (execution byte-identical)
and records the IR via a post-stage hook. Returns (results, CompiledPipeline)."""
from __future__ import annotations

from goldenpipe.compiler.capture import capture_stage
from goldenpipe.compiler.ir import lower
from goldenpipe.engine.runner import Runner


def compile_and_run(plan, ctx, registry):
    compiled = {"nodes": [], "edges": []}
    state = {"nid": 0}
    producer = {}  # artifact -> id of the last node that produced it (for edges)

    def hook(planned, ctx_, result):
        kind, concrete, resolved = capture_stage(planned, ctx_, result)
        nodes, state["nid"] = lower(planned.name, kind, concrete, state["nid"], resolved)
        if not nodes:
            return
        info = getattr(planned.stage, "info", None)
        for art in list(getattr(info, "consumes", []) or []):
            if art in producer:
                compiled["edges"].append([producer[art], nodes[0]["id"], art])
        for art in list(getattr(info, "produces", []) or []):
            producer[art] = nodes[-1]["id"]
        compiled["nodes"].extend(nodes)

    results = Runner(registry).run(plan, ctx, hook=hook)
    return results, compiled
