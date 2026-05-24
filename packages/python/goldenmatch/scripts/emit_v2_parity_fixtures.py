"""Emit TypeScript parity fixtures for the v2.0 core-algorithm port wave.

Generates Python reference outputs for the four parity gaps closed in the
TS v2.0 release plus the broadened heavy-algorithm harness:

  1. Continuous-EM probabilistic (`train_em_continuous`,
     `score_probabilistic_continuous`).
  4. Auto-config planner rules (`apply_planner_rules` over `DEFAULT_RULES`).
  +  Discrete EM (`train_em`, `score_probabilistic`).
  +  Domain software/biblio extractors (`extract_software_features`,
     `extract_biblio_features`).
  +  Blocker output (`build_blocks`-equivalent static blocking).
  +  Clustering output (`build_clusters`).
  +  Golden/survivorship merge (`merge_field`).

Determinism contract:

  - All EM fixtures use datasets small enough that ``_sample_pairs`` /
    ``_sample_blocked_pairs`` enumerate **all** pairs (no RNG sampling),
    so the comparison matrix is identical to the TS enumeration and the
    EM iterations match within float tolerance.
  - Scorers used are limited to ``exact``/``jaro_winkler``/``levenshtein``,
    which are byte-parity between rapidfuzz (Python) and the pure-JS TS
    port (see tests/parity/scorer-ground-truth.test.ts). ``token_sort`` is
    avoided in numeric EM fixtures because rapidfuzz's tokenizer diverges
    from the TS implementation at the collision-signal margin.

Usage::

    uv run python scripts/emit_v2_parity_fixtures.py \
        --out ../../typescript/goldenmatch/tests/parity/v2-fixtures.json

The output JSON is committed to the TS package so CI does not need a
Python interpreter to verify parity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

_PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PKG))

from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_planner import apply_planner_rules  # noqa: E402
from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES  # noqa: E402
from goldenmatch.core.complexity_profile import (  # noqa: E402
    BlockingProfile,
    ComplexityProfile,
)
from goldenmatch.core.domain import (  # noqa: E402
    extract_biblio_features,
    extract_software_features,
)
from goldenmatch.core.probabilistic import (  # noqa: E402
    score_probabilistic,
    score_probabilistic_continuous,
    train_em,
    train_em_continuous,
)
from goldenmatch.core.runtime_profile import RuntimeProfile  # noqa: E402


# ---------------------------------------------------------------------------
# Gap 1 + discrete EM: probabilistic fixtures
# ---------------------------------------------------------------------------

def _prob_rows_small() -> list[dict]:
    """8 records, 28 pairs — all enumerated (no RNG)."""
    return [
        {"__row_id__": 0, "name": "Alice Smith", "city": "New York"},
        {"__row_id__": 1, "name": "Alice Smith", "city": "New York"},
        {"__row_id__": 2, "name": "Alise Smith", "city": "New York"},
        {"__row_id__": 3, "name": "Bob Jones", "city": "Los Angeles"},
        {"__row_id__": 4, "name": "Bob Jones", "city": "Los Angeles"},
        {"__row_id__": 5, "name": "Robert Jones", "city": "LA"},
        {"__row_id__": 6, "name": "Carol Davis", "city": "Chicago"},
        {"__row_id__": 7, "name": "Karol Davis", "city": "Chicago"},
    ]


def _prob_rows_levels3() -> list[dict]:
    return [
        {"__row_id__": 0, "title": "Database Systems", "year": "2001"},
        {"__row_id__": 1, "title": "Database Systems", "year": "2001"},
        {"__row_id__": 2, "title": "Database System", "year": "2002"},
        {"__row_id__": 3, "title": "Operating Systems", "year": "1999"},
        {"__row_id__": 4, "title": "Operating System", "year": "1999"},
        {"__row_id__": 5, "title": "Networks", "year": "2010"},
    ]


def _mk_prob_2level() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="prob_2level",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", transforms=["lowercase", "strip"],
                          scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="city", transforms=["lowercase", "strip"],
                          scorer="exact", levels=2, partial_threshold=0.7),
        ],
        em_iterations=20,
        convergence_threshold=0.001,
        link_threshold=0.0,
    )


def _mk_prob_3level() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="prob_3level",
        type="probabilistic",
        fields=[
            MatchkeyField(field="title", transforms=["lowercase", "strip"],
                          scorer="jaro_winkler", levels=3, partial_threshold=0.7),
            MatchkeyField(field="year", transforms=["strip"],
                          scorer="exact", levels=2, partial_threshold=0.7),
        ],
        em_iterations=20,
        convergence_threshold=0.001,
        link_threshold=0.0,
    )


def _serialize_em(em) -> dict[str, Any]:
    return {
        "m_probs": {k: [round(x, 6) for x in v] for k, v in em.m_probs.items()},
        "u_probs": {k: [round(x, 6) for x in v] for k, v in em.u_probs.items()},
        "match_weights": {k: [round(x, 6) for x in v] for k, v in em.match_weights.items()},
        "proportion_matched": round(em.proportion_matched, 6),
        "iterations": em.iterations,
        "converged": em.converged,
    }


def _serialize_cont_em(em) -> dict[str, Any]:
    return {
        "m_mean": {k: round(v, 6) for k, v in em.m_mean.items()},
        "m_var": {k: round(v, 6) for k, v in em.m_var.items()},
        "u_mean": {k: round(v, 6) for k, v in em.u_mean.items()},
        "u_var": {k: round(v, 6) for k, v in em.u_var.items()},
        "proportion_matched": round(em.proportion_matched, 6),
        "iterations": em.iterations,
        "converged": em.converged,
    }


def _serialize_pairs(pairs) -> list[dict[str, Any]]:
    return [{"a": a, "b": b, "score": round(float(s), 4)} for (a, b, s) in pairs]


def _run_probabilistic() -> dict[str, Any]:
    out: dict[str, Any] = {}

    # --- discrete EM, 2-level ---
    rows2 = _prob_rows_small()
    mk2 = _mk_prob_2level()
    df2 = pl.DataFrame(rows2)
    em2 = train_em(df2, mk2, n_sample_pairs=10000, seed=42)
    pairs2 = score_probabilistic(df2, mk2, em2)
    out["discrete_2level"] = {
        "input_rows": rows2,
        "matchkey": _mk_dict(mk2),
        "expected_em": _serialize_em(em2),
        "expected_pairs": _serialize_pairs(pairs2),
    }

    # --- discrete EM, 3-level ---
    rows3 = _prob_rows_levels3()
    mk3 = _mk_prob_3level()
    df3 = pl.DataFrame(rows3)
    em3 = train_em(df3, mk3, n_sample_pairs=10000, seed=42)
    pairs3 = score_probabilistic(df3, mk3, em3)
    out["discrete_3level"] = {
        "input_rows": rows3,
        "matchkey": _mk_dict(mk3),
        "expected_em": _serialize_em(em3),
        "expected_pairs": _serialize_pairs(pairs3),
    }

    # --- continuous EM (Gap 1) ---
    # Continuous-score datasets are deliberately "compressed" (scores stay in a
    # moderate band) so the Gaussian log-likelihood ratio never blows past
    # math.exp's overflow point. Python's score_probabilistic_continuous raises
    # OverflowError on extreme ratios (var_u floors at 1e-6); the TS port's
    # Math.exp returns Infinity -> sigmoid 0 with no error. Avoiding overflow
    # keeps both sides on the same finite branch so the goldens match.
    rowsc = _prob_rows_continuous()
    mkc = _mk_prob_continuous()
    dfc = pl.DataFrame(rowsc)
    contc = train_em_continuous(dfc, mkc, n_sample_pairs=10000, seed=42)
    cont_pairs = score_probabilistic_continuous(dfc, mkc, contc, threshold=0.0)
    out["continuous_2field"] = {
        "input_rows": rowsc,
        "matchkey": _mk_dict(mkc),
        "expected_em": _serialize_cont_em(contc),
        "expected_pairs": _serialize_pairs(cont_pairs),
    }

    return out


def _prob_rows_continuous() -> list[dict]:
    """Continuous-EM dataset: closely-spaced name variants so the Gaussian
    log-likelihood ratio stays in math.exp's finite range (no overflow)."""
    return [
        {"__row_id__": 0, "name": "Jon Smith", "city": "Boston"},
        {"__row_id__": 1, "name": "John Smith", "city": "Boston"},
        {"__row_id__": 2, "name": "Jonn Smith", "city": "Boston"},
        {"__row_id__": 3, "name": "Jane Smith", "city": "Boston"},
        {"__row_id__": 4, "name": "Jayne Smith", "city": "Boston"},
        {"__row_id__": 5, "name": "Jenny Smith", "city": "Boston"},
    ]


def _mk_prob_continuous() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="prob_continuous",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", transforms=["lowercase", "strip"],
                          scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="city", transforms=["lowercase", "strip"],
                          scorer="jaro_winkler", levels=2, partial_threshold=0.85),
        ],
        em_iterations=20,
        convergence_threshold=0.001,
        link_threshold=0.0,
    )


def _mk_dict(mk: MatchkeyConfig) -> dict[str, Any]:
    return {
        "name": mk.name,
        "type": mk.type,
        "fields": [
            {
                "field": f.field,
                "transforms": list(f.transforms or []),
                "scorer": f.scorer,
                "weight": 1.0,
                "levels": f.levels,
                "partial_threshold": f.partial_threshold,
            }
            for f in mk.fields
        ],
    }


# ---------------------------------------------------------------------------
# Gap 3: domain software / biblio extractors
# ---------------------------------------------------------------------------

_SOFTWARE_INPUTS = [
    "Adobe Photoshop CS3 Professional for Windows",
    "Microsoft Office 2007 Home and Student",
    "Norton AntiVirus 2006 Upgrade (Win/Mac)",
    "Adobe Illustrator CC 2024 Standard",
    "QuickBooks Pro 2005 1234567",
    "macOS Photo Editor v5.0 Lite for Mac",
    "Linux Server Edition 18.04",
    "",
    "the complete software package",
]

_BIBLIO_INPUTS = [
    "A Theory for Record Linkage 1969",
    "Deep Learning for NLP 10.1145/1234567.890 (2018)",
    "The Art of Computer Programming",
    "On the Origin of Species 1859",
    "doi:10.1038/nature12373 Genomic analysis",
    "",
]


def _run_domain() -> dict[str, Any]:
    sw_cases = []
    for text in _SOFTWARE_INPUTS:
        r = extract_software_features(text)
        sw_cases.append({
            "input": text,
            "expected": {
                "name_normalized": r.name_normalized,
                "version": r.version,
                "edition": r.edition,
                "platform": r.platform,
                "part_number": r.part_number,
                "is_upgrade": r.is_upgrade,
                "confidence": round(r.confidence, 6),
            },
        })

    biblio_cases = []
    for text in _BIBLIO_INPUTS:
        r = extract_biblio_features(text)
        biblio_cases.append({
            "input": text,
            "expected": {
                "year": r.get("year"),
                "doi": r.get("doi"),
                "title_key": r.get("title_key"),
            },
        })

    return {"software": sw_cases, "biblio": biblio_cases}


# ---------------------------------------------------------------------------
# Gap 4: planner rule outcomes
# ---------------------------------------------------------------------------

def _planner_case(
    name: str,
    *,
    n_rows: int,
    pair_count: int,
    ram_gb: float,
    cpu_count: int,
    disk_gb: float = 500.0,
    user_backend: str | None = None,
) -> dict[str, Any]:
    profile = ComplexityProfile(
        blocking=BlockingProfile(total_comparisons=pair_count),
    )
    runtime = RuntimeProfile(
        available_ram_gb=ram_gb,
        cpu_count=cpu_count,
        disk_free_gb=disk_gb,
    )
    plan = apply_planner_rules(
        profile=profile,
        runtime=runtime,
        n_rows_full=n_rows,
        rules=DEFAULT_RULES,
        context={"user_backend": user_backend},
    )
    return {
        "name": name,
        "input": {
            "n_rows": n_rows,
            "pair_count": pair_count,
            "ram_gb": ram_gb,
            "cpu_count": cpu_count,
            "disk_gb": disk_gb,
            "user_backend": user_backend,
        },
        "expected_plan": {
            "backend": plan.backend,
            "chunk_size": plan.chunk_size,
            "max_workers": plan.max_workers,
            "pair_spill_threshold": plan.pair_spill_threshold,
            "clustering_strategy": plan.clustering_strategy,
            "rule_name": plan.rule_name,
        },
    }


def _run_planner() -> list[dict[str, Any]]:
    cases = [
        _planner_case("pathological_1row", n_rows=1, pair_count=0, ram_gb=8.0, cpu_count=4),
        _planner_case("simple_small", n_rows=5_000, pair_count=1_000_000, ram_gb=16.0, cpu_count=8),
        _planner_case("simple_cpu_cap2", n_rows=50_000, pair_count=2_000_000, ram_gb=16.0, cpu_count=2),
        _planner_case("fast_box", n_rows=500_000, pair_count=10_000_000, ram_gb=64.0, cpu_count=16),
        _planner_case("fast_box_cpu_cap8", n_rows=500_000, pair_count=10_000_000, ram_gb=64.0, cpu_count=8),
        _planner_case("chunked", n_rows=2_000_000, pair_count=100_000_000, ram_gb=32.0, cpu_count=16),
        _planner_case("low_ram_duckdb", n_rows=2_000_000, pair_count=100_000_000, ram_gb=8.0, cpu_count=8),
        _planner_case("huge_pairs_duckdb", n_rows=10_000_000, pair_count=6_000_000_000, ram_gb=64.0, cpu_count=16),
        _planner_case("user_override_chunked", n_rows=5_000, pair_count=1_000,
                      ram_gb=16.0, cpu_count=8, user_backend="chunked"),
        _planner_case("user_override_duckdb", n_rows=5_000, pair_count=1_000,
                      ram_gb=16.0, cpu_count=8, user_backend="duckdb"),
    ]
    return cases


# ---------------------------------------------------------------------------
# Broadened harness: blocker / clustering / golden
# ---------------------------------------------------------------------------

def _run_blocker() -> dict[str, Any]:
    from goldenmatch.core.blocker import build_blocks

    rows = [
        {"__row_id__": 0, "city": "NY", "name": "Alice"},
        {"__row_id__": 1, "city": "NY", "name": "Bob"},
        {"__row_id__": 2, "city": "NY", "name": "Carol"},
        {"__row_id__": 3, "city": "LA", "name": "Dan"},
        {"__row_id__": 4, "city": "LA", "name": "Eve"},
        {"__row_id__": 5, "city": "SF", "name": "Frank"},
    ]
    df = pl.DataFrame(rows)
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase", "strip"])],
    )
    blocks = build_blocks(df.lazy(), blocking)
    serialized = []
    for b in blocks:
        bdf = b.df.collect() if hasattr(b.df, "collect") else b.df
        ids = sorted(bdf["__row_id__"].to_list())
        serialized.append({"key": b.block_key, "row_ids": ids})
    serialized.sort(key=lambda x: x["key"])
    return {
        "input_rows": rows,
        "blocking": {"strategy": "static", "keys": [{"fields": ["city"],
                     "transforms": ["lowercase", "strip"]}]},
        "expected_blocks": serialized,
    }


def _run_clustering() -> dict[str, Any]:
    from goldenmatch.core.cluster import build_clusters

    # Scored pairs forming two clusters: {0,1,2} and {3,4}; 5 isolated.
    pairs = [
        (0, 1, 0.95),
        (1, 2, 0.90),
        (0, 2, 0.85),
        (3, 4, 0.92),
    ]
    clusters = build_clusters(pairs)
    serialized = []
    for cid, c in sorted(clusters.items()):
        serialized.append({
            "members": sorted(c["members"]),
            "size": c["size"],
            "oversized": bool(c["oversized"]),
            "confidence": round(float(c["confidence"]), 4),
            "cluster_quality": c.get("cluster_quality"),
        })
    serialized.sort(key=lambda x: x["members"])
    return {
        "input_pairs": [{"a": a, "b": b, "score": s} for (a, b, s) in pairs],
        "expected_clusters": serialized,
    }


def _run_golden() -> dict[str, Any]:
    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field

    # Only strategies the TS port implements: most_complete, majority_vote,
    # first_non_null (source_priority / most_recent need extra inputs).
    cases = [
        {"id": "most_complete", "strategy": "most_complete",
         "values": ["Acme", "Acme Corp", "Acme Corporation"]},
        {"id": "most_complete_tie", "strategy": "most_complete",
         "values": ["ABCD", "WXYZ", "AB"]},
        {"id": "majority_vote", "strategy": "majority_vote",
         "values": ["red", "blue", "red"]},
        {"id": "majority_vote_tie", "strategy": "majority_vote",
         "values": ["red", "blue", "green"]},
        {"id": "first_non_null", "strategy": "first_non_null",
         "values": [None, "x", "y"]},
        {"id": "most_complete_nulls", "strategy": "most_complete",
         "values": [None, None, "only"]},
        {"id": "all_identical", "strategy": "majority_vote",
         "values": ["same", "same", "same"]},
        {"id": "all_null", "strategy": "most_complete",
         "values": [None, None]},
    ]
    out = []
    for c in cases:
        rule = GoldenFieldRule(strategy=c["strategy"])
        value, confidence, source_idx = merge_field(c["values"], rule)
        out.append({
            "id": c["id"],
            "strategy": c["strategy"],
            "values": c["values"],
            "expected": {
                "value": value,
                "confidence": round(float(confidence), 6),
                "source_index": source_idx,
            },
        })
    return {"cases": out}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Gap 4: memory-backed tuners (ne_tuner + golden_strategy_tuner)
# ---------------------------------------------------------------------------

def _make_correction(
    *,
    cid: str,
    id_a: int,
    id_b: int,
    decision: str,
    trust: float,
    dataset: str,
    field_name: str | None = None,
    original_value: str | None = None,
    corrected_value: str | None = None,
):
    from goldenmatch.core.memory.store import Correction

    return Correction(
        id=cid,
        id_a=id_a,
        id_b=id_b,
        decision=decision,
        source="steward",
        trust=trust,
        field_hash="",
        record_hash="",
        original_score=trust,
        dataset=dataset,
        field_name=field_name,
        original_value=original_value,
        corrected_value=corrected_value,
    )


def _run_tuners() -> dict[str, Any]:
    from goldenmatch.core.autoconfig_golden_strategy_tuner import tune_field_strategy
    from goldenmatch.core.autoconfig_ne_tuner import tune_ne_field
    from goldenmatch.core.memory.store import MemoryStore

    out: dict[str, Any] = {}

    # ── NE tuner ── Python keys "match" via decision == "match" (the Decision
    # enum never produces "match", so we store the string literal directly; the
    # TS port maps its own "approve" decision to the same truth). 60 corrections
    # (>= MIN_CORRECTIONS=50): 40 matches with high trust, 20 non-matches low.
    store_ne = MemoryStore(backend="sqlite", path=":memory:")
    # Interleave labels in id order (cNNN) so the deterministic id-sorted 90/10
    # split lands both matches and rejects in the held-out tail -> exercises the
    # "tuned" branch (heldout F1 tracks train F1) rather than overfit_guard.
    ne_corrections = []
    for i in range(60):
        if i % 3 == 0:
            ne_corrections.append(_make_correction(
                cid=f"c{i:03d}", id_a=1000 + 2 * i, id_b=1001 + 2 * i,
                decision="reject", trust=0.2, dataset="ds_ne",
            ))
        else:
            ne_corrections.append(_make_correction(
                cid=f"c{i:03d}", id_a=2 * i, id_b=2 * i + 1,
                decision="match", trust=0.9, dataset="ds_ne",
            ))
    for c in ne_corrections:
        store_ne.add_correction(c)
    ne = tune_ne_field(store_ne, "ds_ne", "email")
    store_ne.close()
    out["ne_tuner"] = {
        # is_match abstracts the Python "match" / TS "approve" split.
        "corrections": [
            {"id": c.id, "is_match": c.decision == "match", "trust": c.trust}
            for c in ne_corrections
        ],
        "min_corrections": 50,
        "expected": {
            "penalty": ne.penalty,
            "threshold": ne.threshold,
            "n_corrections": ne.n_corrections,
            "train_f1": ne.train_f1,
            "heldout_f1": ne.heldout_f1,
            "reason": ne.reason,
        },
    }

    # ── Golden-strategy tuner ── 60 field-level corrections on "address1":
    # 45 no-edits (reviewer kept original) + 15 edits to a LONGER value
    # (favors longest_value). Deterministic id-sorted 90/10 split.
    store_gs = MemoryStore(backend="sqlite", path=":memory:")
    gs_corrections = []
    for i in range(45):
        gs_corrections.append(_make_correction(
            cid=f"k{i:03d}", id_a=i, id_b=0,
            decision="field_correct", trust=1.0, dataset="ds_gs",
            field_name="address1", original_value="123 Main St",
            corrected_value="123 Main St",
        ))
    for i in range(15):
        gs_corrections.append(_make_correction(
            cid=f"e{i:03d}", id_a=100 + i, id_b=0,
            decision="field_correct", trust=1.0, dataset="ds_gs",
            field_name="address1", original_value="Apt 1",
            corrected_value="Apartment Number 1 Long",
        ))
    for c in gs_corrections:
        store_gs.add_correction(c)
    gs = tune_field_strategy(store_gs, "ds_gs", "address1")
    store_gs.close()
    out["golden_strategy_tuner"] = {
        "field": "address1",
        "corrections": [
            {
                "id": c.id,
                "decision": c.decision,
                "trust": c.trust,
                "field_name": c.field_name,
                "original_value": c.original_value,
                "corrected_value": c.corrected_value,
            }
            for c in gs_corrections
        ],
        "candidates": list(
            __import__("goldenmatch.core.autoconfig_golden_strategy_tuner",
                       fromlist=["DEFAULT_CANDIDATE_STRATEGIES"]).DEFAULT_CANDIDATE_STRATEGIES
        ),
        "min_corrections": 50,
        "expected": {
            "field": gs.field,
            "strategy": gs.strategy,
            "n_corrections": gs.n_corrections,
            "train_hit_rate": gs.train_hit_rate,
            "heldout_hit_rate": gs.heldout_hit_rate,
            "reason": gs.reason,
        },
    }

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    payload: dict[str, Any] = {
        "probabilistic": _run_probabilistic(),
        "domain": _run_domain(),
        "planner": _run_planner(),
        "tuners": _run_tuners(),
        "blocker": _run_blocker(),
        "clustering": _run_clustering(),
        "golden": _run_golden(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
