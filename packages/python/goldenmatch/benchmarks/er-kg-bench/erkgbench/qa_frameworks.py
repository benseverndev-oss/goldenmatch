"""Opt-in real-framework KG baselines for the QA fact-completeness eval (SP6 Task 7).

Reuses the harness's existing real-framework resolvers (`adapters/real/`, the same
in-process neo4j-graphrag / LlamaIndex / Graphiti decision code the ER table
scores). They produce a partition, which the QA eval turns into a KG + scores for
fact-completeness exactly like goldengraph and the exact-match floor.

Best-effort: `available_real_adapters()` only returns the frameworks whose deps
import, so this is empty on a plain venv. NEVER part of the CI gate.
"""

from __future__ import annotations


def framework_adapters() -> list:
    """The real-framework adapters whose libraries are importable (possibly none)."""
    try:
        from erkgbench.adapters.real import available_real_adapters
    except Exception:  # noqa: BLE001 - real adapters are optional
        return []
    return list(available_real_adapters())
