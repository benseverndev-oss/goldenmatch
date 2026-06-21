"""Serialize/deserialize helpers between live Python dataclasses and the
native (Rust) JSON contract for the auto-config kernels.

Two kernels are bridged here:
  - ``autoconfig_decide_plan(json_str) -> json_str``  (Layer 1: planner)
  - ``autoconfig_classify_columns(json_str) -> json_str``  (Layer 2: classifier)

Both shims speak JSON in / JSON out -- no PyDict marshalling of nested
structs. This module handles the Python side of that contract.

The native path is gated on ``native_enabled("autoconfig")`` which is
intentionally NOT in ``_GATED_ON`` yet, so the default env routes to pure
Python. Cutover = Task F1 (add ``"autoconfig"`` to ``_GATED_ON``).
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from goldenmatch.core.autoconfig import ColumnProfile
    from goldenmatch.core.complexity_profile import ComplexityProfile
    from goldenmatch.core.execution_plan import ExecutionPlan
    from goldenmatch.core.runtime_profile import RuntimeProfile


# ── Capability assembly ────────────────────────────────────────────────────

def build_planner_capabilities(context: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the ``Capabilities`` dict the Rust planner needs.

    Mirrors the live env probes that the Python planner rules perform:
      - ``bucket_available``: whether the native bucket scorer is on (same
        check as ``_scoring_backend()`` in ``autoconfig_planner_rules``).
      - ``ray_available``: ``_has_ray()`` from ``autoconfig_planner_rules``.
      - ``ray_auto_select``: ``_ray_auto_select_enabled()``.
      - ``user_backend``: explicit user preference from ``context``, or None.
    """
    from goldenmatch.core._native_loader import native_enabled
    from goldenmatch.core.autoconfig_planner_rules import (
        _BUCKET_OPT_OUT,
        _has_ray,
        _ray_auto_select_enabled,
    )

    bucket_available = (
        os.environ.get("GOLDENMATCH_PLANNER_BUCKET", "1").strip().lower()
        not in _BUCKET_OPT_OUT
        and native_enabled("block_scoring")
    )
    ray_available = _has_ray()
    ray_auto_select = _ray_auto_select_enabled()
    user_backend: str | None = (context or {}).get("user_backend") or None

    return {
        "bucket_available": bucket_available,
        "ray_available": ray_available,
        "ray_auto_select": ray_auto_select,
        "user_backend": user_backend,
    }


# ── Layer 1: Planner ───────────────────────────────────────────────────────

def plan_input_to_json(
    profile: ComplexityProfile,
    runtime: RuntimeProfile,
    n_rows_full: int,
    caps: dict[str, Any],
) -> str:
    """Serialize the planner inputs to the ``PlannerInput`` JSON the Rust shim
    expects.

    Shape exactly:
    ``{"n_rows_full": int, "estimated_pair_count": int,
       "runtime": {"available_ram_gb": float, "cpu_count": int, "disk_free_gb": float},
       "caps": caps_dict}``

    ``n_rows_full`` must be the EXTRAPOLATED full-row count (the same value the
    controller passes to ``apply_planner_rules``), NOT the sample height.
    """
    payload: dict[str, Any] = {
        "n_rows_full": int(n_rows_full),
        "estimated_pair_count": int(profile.blocking.estimated_pair_count),
        "runtime": {
            "available_ram_gb": float(runtime.available_ram_gb),
            "cpu_count": int(runtime.cpu_count),
            "disk_free_gb": float(runtime.disk_free_gb),
        },
        "caps": caps,
    }
    return json.dumps(payload)


def plan_from_json(s: str) -> ExecutionPlan:
    """Reconstruct a Python ``ExecutionPlan`` from the Rust shim's JSON output.

    JSON ``null`` → Python ``None`` for ``chunk_size`` / ``pair_spill_threshold``
    (``json.loads`` already does this).
    """
    from goldenmatch.core.execution_plan import ExecutionPlan

    data = json.loads(s)
    return ExecutionPlan(
        backend=data["backend"],
        chunk_size=data.get("chunk_size"),           # null -> None
        max_workers=int(data["max_workers"]),
        pair_spill_threshold=data.get("pair_spill_threshold"),  # null -> None
        clustering_strategy=data["clustering_strategy"],
        rule_name=data["rule_name"],  # Rust always emits a non-null rule_name string
    )


# ── Layer 2: Classifier ────────────────────────────────────────────────────

def column_stats_to_json(
    stats_list: list[dict[str, Any]],
) -> str:
    """Serialize a list of per-column stats dicts to JSON for ``autoconfig_classify_columns``.

    Each dict must have keys: ``name``, ``dtype``, ``sample_values`` (FULL non-null
    list), ``null_rate``, ``cardinality_ratio``, ``avg_len``.
    """
    return json.dumps(stats_list)


def column_profiles_from_json(
    s: str,
    names_to_sample_values: dict[str, list[str]],
) -> list[ColumnProfile]:
    """Reconstruct Python ``ColumnProfile`` objects from the Rust classifier's JSON output.

    The native output is a list of dicts with keys:
      name, dtype, col_type, confidence, null_rate, cardinality_ratio, avg_len,
      needs_llm_escalation

    ``sample_values`` is not in the native output (it only passes through stats,
    not the full value list). We reconstruct it from ``names_to_sample_values``
    truncated to 5 entries, matching the Python truncation in ``profile_columns``.

    The ``needs_llm_escalation`` field is not on the Python ``ColumnProfile``
    dataclass; we ignore it here (the Python LLM pass runs AFTER native classify
    and uses its own ``_llm_classify_columns`` filter, unchanged).
    """
    # Lazy import to avoid a top-level cycle (autoconfig.py imports us via the
    # dispatch block; importing ColumnProfile at module top would create a cycle
    # because autoconfig.py is in the same package).
    from goldenmatch.core.autoconfig import ColumnProfile  # noqa: PLC0415

    out = json.loads(s)
    profiles: list[ColumnProfile] = []
    for item in out:
        name = item["name"]
        sample_values = names_to_sample_values.get(name, [])[:5]
        profiles.append(ColumnProfile(
            name=name,
            dtype=item["dtype"],
            col_type=item["col_type"],
            confidence=float(item["confidence"]),
            sample_values=sample_values,
            null_rate=float(item["null_rate"]),
            cardinality_ratio=float(item["cardinality_ratio"]),
            avg_len=float(item["avg_len"]),
        ))
    return profiles
