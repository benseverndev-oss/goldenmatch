"""Parity harness for the score_blocks matched_pairs de-Python work.

``matched_pairs`` is the cross-matchkey-pass exclude set (a
``set[tuple[int, int]]`` of canonical ``(min, max)`` row-id pairs). Its
construction inside ``score_blocks_columnar`` / ``score_blocks_parallel`` is a
SIDE EFFECT — the functions return the pairs (DataFrame / list); they *also*
mutate ``matched_pairs`` so the NEXT matchkey pass can exclude already-found
pairs. So a change to how ``matched_pairs`` is built can only alter OUTPUT via a
LATER pass's exclusion. Every end-to-end parity assertion therefore uses a
MULTI-matchkey config and compares an exclusion-sensitive snapshot (final
clusters + scored pairs), never the record-level ``dupes`` table.

Profiled baseline (run 27227186114, columnar @ 1M / 131M pairs, 351s wall):
``set.add`` 38.0s (131,291,589 calls), ``builtins.min`` 37.3s,
``builtins.max`` 29.0s == ~104s / 30% of wall, all in the
``matched_pairs.add((min(a, b), max(a, b)))`` loops. The per-block scorer is
already vectorized; only this bookkeeping is per-pair Python.

Run locally (targeted file only — never the full suite on Windows):
``POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
  PYTHONPATH=<worktree>/packages/python/goldenmatch \
  .venv/Scripts/python.exe -m pytest <this file> -v``
"""
from __future__ import annotations

import random

import polars as pl

from goldenmatch._api import DedupeResult
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

_FIRST = ["John", "Jon", "Jane", "Janet", "Bob", "Rob", "Mary", "Mari", "Bill", "Will"]
_LAST = ["Smith", "Smyth", "Jones", "Jonas", "Brown", "Braun", "Clark", "Clarke"]
_CITY = ["Springfield", "Springfeld", "Columbus", "Columbia"]


def _two_pass_person_df(n: int = 200) -> pl.DataFrame:
    """Person rows with two blockable/scorable fields (name, city) and
    deliberate near-duplicates so both matchkey passes find OVERLAPPING pairs
    (the overlap is what makes cross-pass exclusion observable)."""
    rng = random.Random(7)
    rows = []
    for _ in range(n):
        rows.append({
            "name": f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
            "city": rng.choice(_CITY),
        })
    # No __row_id__ — the pipeline assigns one (providing it duplicates the col).
    return pl.DataFrame(rows)


def _two_matchkey_config() -> GoldenMatchConfig:
    """Two weighted matchkeys (name, then city) over a shared city block.

    Both passes compare within the same blocks but score different fields, so a
    pair similar on BOTH name and city is found by pass 1 (name), added to
    ``matched_pairs``, then excluded by pass 2 (city). That exclusion is exactly
    what the de-Python work must preserve byte-for-byte.
    """
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="mk_name", type="weighted", threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0,
                                      transforms=["lowercase", "strip"])],
            ),
            MatchkeyConfig(
                name="mk_city", type="weighted", threshold=0.85,
                fields=[MatchkeyField(field="city", scorer="jaro_winkler", weight=1.0,
                                      transforms=["lowercase", "strip"])],
            ),
        ],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
        ),
    )


def _snapshot(res: DedupeResult) -> tuple:
    """Exclusion-sensitive, order-independent fingerprint of a DedupeResult.

    - clusters: frozenset of frozenset-of-members — numbering-independent, and
      sensitive to which edges (pairs) survived cross-pass exclusion.
    - scored_pairs: the canonical (min, max, score) set — the MOST direct signal
      of exclusion (a multiply-found pair keeps the first pass's score under
      exclusion vs the max-across-passes score without it). Included when the
      pipeline populates it; clusters alone already bite.
    """
    clusters = frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in res.clusters.values()
    )
    scored = tuple(sorted(
        (int(a), int(b), round(float(s), 9))
        for a, b, s in (res.scored_pairs or [])
    ))
    return (res.total_records, len(res.clusters), clusters, scored)


def _run(df: pl.DataFrame, cfg: GoldenMatchConfig) -> tuple:
    """Run a full dedupe and return the exclusion-sensitive snapshot.

    Explicit config (no auto-config → no HuggingFace model download); single-
    field weighted matchkeys keep rerank off, so this stays offline + fast.
    """
    from goldenmatch import dedupe_df
    res = dedupe_df(df, config=cfg)
    return _snapshot(res)


def test_two_matchkey_dedupe_is_deterministic_baseline():
    """Determinism baseline — the snapshot every later stage must preserve.

    Also implicitly asserts the snapshot is STABLE (no set-ordering flakiness).
    """
    df = _two_pass_person_df()
    cfg = _two_matchkey_config()
    a = _run(df, cfg)
    b = _run(df, cfg)
    assert a == b, "dedupe must be deterministic (this is the parity baseline)"
    # Sanity: the config actually produced clusters (the test exercises real
    # scoring + clustering, not an empty no-op).
    assert a[1] > 0, "expected at least one cluster from the synthetic near-duplicates"
