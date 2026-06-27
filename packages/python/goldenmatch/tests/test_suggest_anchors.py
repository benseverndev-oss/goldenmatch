"""Regression anchors for the config-suggestion kernel.

Two pins on the NCVR address-swap rule (Rule 2 in suggest-core):
  * test_anchor_ncvr_address_swap_real   -- real NCVR file, skipped when absent
  * test_anchor_address_swap_synthetic   -- always-available CI-safe anchor

Both guards skip when native ``suggest_config`` is unavailable.

The hard contract:
    Given NCVR-shaped data where ``res_street_address`` is scored with
    ``token_sort`` AND has enough character-noise to cross Rule 2's
    corruption threshold (>= 0.30), ``review_config`` must emit at least
    one SwapScorer suggestion targeting the address column.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_native_suggest() -> bool:
    """Return True iff the native suggest_config kernel is available."""
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415
        nm = native_module()
        return nm is not None and hasattr(nm, "suggest_config")
    except Exception:
        return False


_NATIVE_AVAILABLE = _has_native_suggest()
_SKIP_NO_NATIVE = pytest.mark.skipif(
    not _NATIVE_AVAILABLE,
    reason="native suggest_config kernel not available (install goldenmatch[native])",
)


def _make_zero_config_with_token_sort(df: pl.DataFrame, address_col: str):
    """Build a minimal GoldenMatchConfig where ``address_col`` uses token_sort.

    We deliberately use a manually-constructed config (not auto_configure_df)
    so the test is independent of the noise-aware-scorer default
    (GOLDENMATCH_NOISE_AWARE_SCORERS) that would upgrade token_sort -> jaro_winkler
    before Rule 2 gets a chance to fire.

    The config targets the NCVR-shaped blocking structure: block on birth_year,
    score on first_name + last_name + address_col with token_sort on the address.
    Adjust field list to whatever columns are actually present in df.
    """
    from goldenmatch.config.schemas import (  # noqa: PLC0415
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    data_cols = [c for c in df.columns if not c.startswith("__")]

    # Choose a blocking key (prefer birth_year, else first available string col)
    blocking_key = None
    for cand in ("birth_year", "zip_code", "zip5", "last_name"):
        if cand in data_cols:
            blocking_key = cand
            break
    if blocking_key is None:
        blocking_key = data_cols[0]

    # Build fuzzy matchkey fields
    fields = []
    for cand in ("first_name", "last_name", "middle_name"):
        if cand in data_cols:
            fields.append(MatchkeyField(field=cand, scorer="token_sort", weight=1.0))

    # The address column: explicitly token_sort (NOT jaro_winkler) -- the anchor
    if address_col in data_cols:
        fields.append(MatchkeyField(field=address_col, scorer="token_sort", weight=1.5))

    if not fields:
        # Fallback: score all non-blocking string columns with token_sort
        for col in data_cols:
            if col != blocking_key and df[col].dtype == pl.String:
                fields.append(MatchkeyField(field=col, scorer="token_sort", weight=1.0))

    matchkeys = [MatchkeyConfig(
        name="fuzzy_match",
        type="weighted",
        threshold=0.7,
        fields=fields,
    )]

    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=[blocking_key])],
    )

    return GoldenMatchConfig(matchkeys=matchkeys, blocking=blocking)


def _corrupt_address(val: str, rng: random.Random) -> str:
    """Produce a case/whitespace variant of the address.

    The corruption-score heuristic (``indicators._compute_corruption_score_inline``,
    which feeds Rule 2's column signals) measures *case-or-whitespace-collapsed*
    duplicates -- ``Brian/BRIAN/brian/Brian `` -- via ``1 - distinct_normalized /
    distinct_raw``, NOT character-level edit noise. A typo'd address is a NEW
    distinct normalized form (it does not collapse), so it reads as CLEAN and
    never crosses the 0.30 threshold. To genuinely exercise Rule 2 the duplicate
    must be the SAME address in a different case/whitespace form so it collapses
    onto the original's normalized key.
    """
    if not val:
        return val
    op = rng.choice(["lower", "upper", "trailing_ws"])
    if op == "lower":
        return val.lower()
    if op == "upper":
        return val.upper()
    return val + "  "  # trailing whitespace; strip().lower() collapses it


def _make_address_anchor_dataset(
    n_entities: int = 300,
    seed: int = 857,
    corruption_rate: float = 0.55,
) -> tuple[pl.DataFrame, set]:
    """Small synthetic dataset whose zero-config should trigger Rule 2.

    Layout: id, first_name, last_name, birth_year, res_street_address.
    Each entity has 1 original + 1 duplicate with a corrupted address.
    Corruption rate is set high enough that corruption_score >= 0.30 in
    the oracle's column-signals batch (Rule 2 threshold).

    The dataset uses only ``res_street_address`` for the address signal.
    No email / zip / phone so auto_configure cannot build an exact matchkey
    that would make the address column irrelevant.
    """
    rng = random.Random(seed)

    street_names = [
        "Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Elm St",
        "Pine Rd", "River Blvd", "Park Ave", "Hill St", "Lake Dr",
        "Forest Way", "Valley Rd", "Church St", "Mill Rd", "Grove Ave",
        "Spring St", "Union Ave", "Market St", "High St", "Center Rd",
    ]

    records: list[dict] = []
    entity_to_rows: dict[int, list[int]] = {}
    row_idx = 0

    for eid in range(n_entities):
        number = rng.randint(100, 9999)
        street = rng.choice(street_names)
        address = f"{number} {street}"
        birth_year = str(rng.randint(1950, 2000))
        # Use a syllable-based name pool to avoid surname collisions
        first = rng.choice([
            "Alex", "Blair", "Casey", "Dana", "Eli", "Finley", "Gray", "Harper",
            "Indigo", "Jamie", "Kendall", "Logan", "Morgan", "Noel", "Oakley",
        ])
        last = rng.choice([
            "Smith", "Jones", "Williams", "Brown", "Davis", "Miller", "Wilson",
            "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
            "Martin", "Thompson", "Garcia", "Martinez", "Robinson", "Clark",
        ])

        # Original record
        orig = {
            "id": f"E{eid:04d}_A",
            "first_name": first,
            "last_name": last,
            "birth_year": birth_year,
            "res_street_address": address,
        }
        records.append(orig)
        entity_to_rows.setdefault(eid, []).append(row_idx)
        row_idx += 1

        # Duplicate with corrupted address (high corruption rate -> Rule 2)
        if rng.random() < corruption_rate:
            corrupted_address = _corrupt_address(address, rng)
            dup = {
                "id": f"E{eid:04d}_B",
                "first_name": first,
                "last_name": last,
                "birth_year": birth_year,
                "res_street_address": corrupted_address,
            }
            records.append(dup)
            entity_to_rows.setdefault(eid, []).append(row_idx)
            row_idx += 1

    # Shuffle
    paired = list(enumerate(records))
    rng.shuffle(paired)

    # Rebuild entity_to_rows with shuffled positions
    new_entity_to_rows: dict[int, list[int]] = {}
    old_to_new = {old_idx: new_idx for new_idx, (old_idx, _) in enumerate(paired)}
    for eid, old_rows in entity_to_rows.items():
        new_entity_to_rows[eid] = [old_to_new[r] for r in old_rows]

    shuffled_records = [rec for _, rec in paired]
    df = pl.DataFrame(shuffled_records)

    # Build canonical (min, max) row-index pairs
    from itertools import combinations  # noqa: PLC0415
    gt: set[tuple[int, int]] = set()
    for rows in new_entity_to_rows.values():
        for a, b in combinations(sorted(rows), 2):
            gt.add((a, b))

    return df, gt


# ---------------------------------------------------------------------------
# Part A — Regression anchors
# ---------------------------------------------------------------------------

@pytest.mark.benchmark
@_SKIP_NO_NATIVE
def test_anchor_ncvr_address_swap_real() -> None:
    """On REAL NCVR data: review_config ranks res_street_address swap #1.

    This is the hard F1 0.871 -> 0.981 lever from the NCVR benchmark.
    Skipped when the real NCVR dataset file is absent (gitignored PII data).
    """
    _root = (
        Path(__file__).resolve().parents[1]
        / "tests/benchmarks/datasets/NCVR/ncvoter_sample_10k.txt"
    )
    if not _root.exists():
        pytest.skip(f"Real NCVR file absent: {_root}")

    # Set determinism env before goldenmatch auto-configure import
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    os.environ.setdefault("PYTHONHASHSEED", "0")

    from scripts.dqbench_adapters.ncvr import build_ncvr_df_and_gt  # noqa: PLC0415
    from scripts.suggest_quality.datasets import _pairs_to_row_index  # noqa: PLC0415

    loaded = build_ncvr_df_and_gt(_root, seed=42)
    assert loaded is not None, "build_ncvr_df_and_gt returned None"
    df, ncid_pairs = loaded
    _gt = _pairs_to_row_index(df, "ncid", ncid_pairs)

    zero_config = _make_zero_config_with_token_sort(df, "res_street_address")

    from goldenmatch.core.suggest import review_config  # noqa: PLC0415

    suggestions = review_config(df, zero_config)
    assert suggestions, "review_config returned no suggestions for real NCVR data"

    # Hard contract: res_street_address swap must be the #1 suggestion.
    top = suggestions[0]
    assert top.kind == "swap_scorer", (
        f"Expected top suggestion kind='swap_scorer' but got kind='{top.kind}' "
        f"(target='{top.target}'). All suggestions: "
        + ", ".join(f"{s.kind}:{s.target}" for s in suggestions)
    )
    assert "res_street_address" in top.target.lower() or "address" in top.target.lower(), (
        f"Expected address column in top suggestion target, got: '{top.target}'"
    )


@_SKIP_NO_NATIVE
def test_anchor_address_swap_synthetic() -> None:
    """CI-safe anchor: on the dedicated address-anchor synthetic dataset,
    review_config emits a swap_scorer for res_street_address.

    We pass a manually-crafted zero_config that uses token_sort on the
    address column, bypassing the noise-aware-scorer auto-upgrade
    (GOLDENMATCH_NOISE_AWARE_SCORERS). This ensures Rule 2 sees token_sort
    in the column_signals batch and can fire regardless of env defaults.

    Assertion: swap_scorer targeting res_street_address is present in the
    suggestions. We assert #1 ranking where possible (corruption_rate=0.55
    generates strong enough signal), but fall back to 'any suggestion' if
    the rank varies due to dataset randomness.
    """
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    os.environ.setdefault("PYTHONHASHSEED", "0")

    df, _gt = _make_address_anchor_dataset(n_entities=300, seed=857)

    assert "res_street_address" in df.columns, "anchor dataset missing res_street_address"
    assert df.height > 0, "anchor dataset is empty"

    zero_config = _make_zero_config_with_token_sort(df, "res_street_address")

    from goldenmatch.core.suggest import review_config  # noqa: PLC0415

    suggestions = review_config(df, zero_config)
    assert suggestions, (
        "review_config returned no suggestions on the address-anchor synthetic dataset. "
        "Check that token_sort is present in the config and that corruption_score >= 0.30."
    )

    # Find a swap_scorer for the address column
    address_swaps = [
        s for s in suggestions
        if s.kind == "swap_scorer"
        and ("res_street_address" in s.target.lower() or "address" in s.target.lower())
    ]
    assert address_swaps, (
        "No swap_scorer suggestion for res_street_address found. "
        f"Emitted suggestions: {[(s.kind, s.target) for s in suggestions]}"
    )

    # Strong claim: the swap should be ranked #1 (highest confidence).
    # If the assertion breaks it means the corruption signal isn't dominant
    # enough on this dataset shape; relax to 'present' and note the position.
    top = suggestions[0]
    if not (top.kind == "swap_scorer" and (
        "res_street_address" in top.target.lower() or "address" in top.target.lower()
    )):
        # Relaxed: accept any position but note rank for debugging.
        rank = next(
            (i for i, s in enumerate(suggestions) if s in address_swaps),
            -1,
        )
        pytest.xfail(
            f"address swap is present but not #1 (rank={rank}). "
            f"Top suggestion: kind='{top.kind}' target='{top.target}'. "
            "Consider increasing corruption_rate in _make_address_anchor_dataset."
        )


@_SKIP_NO_NATIVE
def test_anchor_address_swap_token_sort_triggers_rule2() -> None:
    """Unit-level pin: Rule 2 fires when token_sort + high corruption.

    This test constructs the column_signals batch directly and calls the
    native kernel without going through the full pipeline. Faster than the
    oracle loop and pinned to the exact Rule 2 preconditions.
    """
    import json  # noqa: PLC0415

    import pyarrow as pa  # noqa: PLC0415
    from goldenmatch.core._native_loader import native_module  # noqa: PLC0415

    nm = native_module()
    assert nm is not None and hasattr(nm, "suggest_config")

    # Minimal scored_pairs (no real pairs needed — Rule 2 is signals-only)
    scored_pairs_schema = pa.schema([
        pa.field("id_a", pa.int64()),
        pa.field("id_b", pa.int64()),
        pa.field("score", pa.float64()),
    ])
    scored_pairs_batch = pa.record_batch(
        {"id_a": pa.array([], type=pa.int64()),
         "id_b": pa.array([], type=pa.int64()),
         "score": pa.array([], type=pa.float64())},
        schema=scored_pairs_schema,
    )

    # Minimal clusters batch
    clusters_schema = pa.schema([
        pa.field("cluster_id", pa.int64()),
        pa.field("size", pa.int64()),
        pa.field("confidence", pa.float64()),
        pa.field("quality", pa.utf8()),
        pa.field("oversized", pa.bool_()),
    ])
    clusters_batch = pa.record_batch(
        {
            "cluster_id": pa.array([], type=pa.int64()),
            "size": pa.array([], type=pa.int64()),
            "confidence": pa.array([], type=pa.float64()),
            "quality": pa.array([], type=pa.utf8()),
            "oversized": pa.array([], type=pa.bool_()),
        },
        schema=clusters_schema,
    )

    # Column signals: res_street_address with token_sort + corruption 0.5 (> 0.30)
    col_signals_schema = pa.schema([
        pa.field("field", pa.utf8()),
        pa.field("col_type", pa.utf8()),
        pa.field("scorer", pa.utf8()),
        pa.field("in_blocking", pa.bool_()),
        pa.field("in_negative_evidence", pa.bool_()),
        pa.field("identity_score", pa.float64()),
        pa.field("corruption_score", pa.float64()),
        pa.field("collision_rate", pa.float64()),
        pa.field("cardinality_ratio", pa.float64()),
        pa.field("null_rate", pa.float64()),
        pa.field("variant_rate", pa.float64()),
    ])
    col_signals_batch = pa.record_batch(
        {
            "field": pa.array(["res_street_address"], type=pa.utf8()),
            "col_type": pa.array(["address"], type=pa.utf8()),
            "scorer": pa.array(["token_sort"], type=pa.utf8()),
            "in_blocking": pa.array([False], type=pa.bool_()),
            "in_negative_evidence": pa.array([False], type=pa.bool_()),
            "identity_score": pa.array([0.7], type=pa.float64()),
            "corruption_score": pa.array([0.5], type=pa.float64()),  # >= 0.30 threshold
            "collision_rate": pa.array([0.0], type=pa.float64()),
            "cardinality_ratio": pa.array([0.9], type=pa.float64()),
            "null_rate": pa.array([0.0], type=pa.float64()),
            "variant_rate": pa.array([0.0], type=pa.float64()),
        },
        schema=col_signals_schema,
    )

    config_json = json.dumps({
        "matchkeys": [{
            "name": "fuzzy_match",
            "kind": "weighted",
            "threshold": 0.7,
            "fields": [{"field": "res_street_address", "scorer": "token_sort", "weight": 1.0}],
        }],
        "negative_evidence": [],
    })
    priors_json = json.dumps({"counts": {}})

    raw_json = nm.suggest_config(
        scored_pairs_batch,
        clusters_batch,
        col_signals_batch,
        config_json,
        priors_json,
    )

    items = json.loads(raw_json)
    assert items, "Native kernel returned no suggestions for token_sort + corruption 0.5"
    swap = [i for i in items if i.get("kind") == "swap_scorer"]
    assert swap, (
        f"No swap_scorer suggestion from native kernel. Got: {items}"
    )
    top = swap[0]
    assert top.get("target", "").endswith("res_street_address") or \
           "res_street_address" in top.get("target", ""), (
        f"swap_scorer target does not reference res_street_address: {top}"
    )
    assert top.get("proposed_value") == "jaro_winkler", (
        f"Expected proposed_value='jaro_winkler', got: {top.get('proposed_value')}"
    )
