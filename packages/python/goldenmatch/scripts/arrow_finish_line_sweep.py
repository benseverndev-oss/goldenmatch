"""Stage-0 Arrow finish-line bench sweep.

Runs each phase's existing kill-criterion bench at the kill scale on the
realistic_person fixture and classifies it PASS / CLOSE / BLOCKED. See
docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md.

At-scale inputs (5M/25M) are generated on the bench box via the Railway
goldenmatch-bench-gen service (scripts/trigger_bench_gen.py) or the
generate-bench-dataset.yml workflow; the sweep consumes the generated parquet,
not a locally-built fixture. Do not build >=5M locally.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

CritKind = Literal["ratio_le", "speedup_ge", "abs_le", "bool_true"]


@dataclass(frozen=True)
class Criterion:
    name: str
    kind: CritKind
    target: float | bool


@dataclass
class PhaseVerdict:
    verdict: Literal["PASS", "CLOSE", "BLOCKED"]
    details: list[str] = field(default_factory=list)


def _ratio(m: dict) -> float | None:
    if not m or "new" not in m or "legacy" not in m or not m["legacy"]:
        return None
    return m["new"] / m["legacy"]


def classify_phase(crits: list[Criterion], metrics: dict) -> PhaseVerdict:
    details: list[str] = []
    any_close = False
    for c in crits:
        val = metrics.get(c.name)
        if c.kind == "bool_true":
            if val is not True:
                details.append(f"{c.name}: assertion not met -> BLOCKED")
                return PhaseVerdict("BLOCKED", details)
            details.append(f"{c.name}: OK")
            continue
        if val is None:
            details.append(f"{c.name}: metric missing -> BLOCKED")
            return PhaseVerdict("BLOCKED", details)
        if c.kind == "ratio_le":
            r = _ratio(val)
            if r is None:
                return PhaseVerdict("BLOCKED", details + [f"{c.name}: no ratio -> BLOCKED"])
            if r <= c.target:
                details.append(f"{c.name}: ratio {r:.2f} <= {c.target} PASS")
            elif r < 1.0:
                any_close = True
                details.append(f"{c.name}: ratio {r:.2f} beats legacy, misses {c.target} CLOSE")
            else:
                any_close = True
                details.append(f"{c.name}: ratio {r:.2f} >= 1.0 (no win) CLOSE")
        elif c.kind == "speedup_ge":
            r = _ratio(val)
            if r is None or r <= 0:
                return PhaseVerdict("BLOCKED", details + [f"{c.name}: no speedup -> BLOCKED"])
            speedup = 1.0 / r
            if speedup >= c.target:
                details.append(f"{c.name}: {speedup:.2f}x >= {c.target}x PASS")
            else:
                any_close = True
                details.append(f"{c.name}: {speedup:.2f}x < {c.target}x CLOSE")
        elif c.kind == "abs_le":
            if float(val) <= float(c.target):
                details.append(f"{c.name}: {val} <= {c.target} PASS")
            else:
                any_close = True
                details.append(f"{c.name}: {val} > {c.target} CLOSE")
    return PhaseVerdict("CLOSE" if any_close else "PASS", details)


PHASE_CRITERIA: dict[str, list[Criterion]] = {
    "phase1": [
        Criterion("wall", "ratio_le", 0.50),
        Criterion("rss", "ratio_le", 0.25),
        Criterion("parity", "bool_true", True),
    ],
    "phase2": [
        Criterion("rss", "ratio_le", 0.70),
        Criterion("wall", "ratio_le", 1.10),
        Criterion("materialize_cluster_dict_retired", "bool_true", True),
        Criterion("parity", "bool_true", True),
    ],
    "phase3": [
        Criterion("dedup", "speedup_ge", 5.0),
        Criterion("build_clusters", "speedup_ge", 2.0),
        Criterion("fingerprints", "speedup_ge", 3.0),
        Criterion("parity", "bool_true", True),
    ],
    "phase4": [
        Criterion("golden_wall_s", "abs_le", 60.0),
        Criterion("rss", "ratio_le", 0.60),
        Criterion("materialize_cluster_dict_removed", "bool_true", True),
        Criterion("parity", "bool_true", True),
    ],
    "phase5": [
        Criterion("wall", "ratio_le", 0.50),
        Criterion("driver_rss", "ratio_le", 0.10),
        Criterion("parity", "bool_true", True),
    ],
    "phase6": [
        Criterion("apply_standardization_s", "abs_le", 20.0),
        Criterion("zero_full_df_map_elements", "bool_true", True),
    ],
}

PHASE_BENCH_SCALE: dict[str, int] = {
    "phase1": 5_000_000,
    "phase2": 25_000_000,
    "phase3": 5_000_000,
    "phase4": 25_000_000,
    "phase5": 25_000_000,
    "phase6": 10_000_000,
}

_MARK = "__BENCH_JSON__"


def parse_bench_json(stdout: str) -> dict | None:
    last = None
    for line in stdout.splitlines():
        i = line.find(_MARK)
        if i != -1:
            last = line[i + len(_MARK):]
    if last is None:
        return None
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return None


def parse_native_speedup(stdout: str, label: str) -> float | None:
    """Native kernel benches print a human table, not JSON:
    e.g. `native(Vec) speedup vs python : 2.41x`. Pull the multiple on the
    line containing `label`. Returns None if absent."""
    for line in stdout.splitlines():
        if label in line:
            m = re.search(r"([0-9]+\.?[0-9]*)\s*x", line)
            if m:
                return float(m.group(1))
    return None


def render_markdown_table(rows: dict) -> str:
    out = ["| Phase | Verdict | Detail |", "|---|---|---|"]
    for phase, v in rows.items():
        out.append(f"| {phase} | {v.verdict} | {'; '.join(v.details)} |")
    return "\n".join(out)
