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


# ── Stage 1: the columnar guard ──────────────────────────────────────
# score_blocks_columnar is only ever called by the pipeline on the
# single-weighted-matchkey columnar path (_is_columnar_eligible requires
# len(matchkeys) == 1), so its matched_pairs side effect is STRUCTURALLY
# DEAD — nothing consumes it. `track_matched=False` lets the caller skip
# building it (the profiled ~104s). These unit tests prove the guard skips
# the build WITHOUT changing the returned pair stream.

from goldenmatch.core.blocker import BlockResult  # noqa: E402
from goldenmatch.core.scorer import (  # noqa: E402
    pairs_df_to_list,
    score_blocks_columnar,
    score_blocks_parallel,
)


def _block(records: list[tuple[int, str]], block_key: str = "k") -> BlockResult:
    df = pl.DataFrame({
        "__row_id__": [r[0] for r in records],
        "__source__": ["fixture"] * len(records),
        "name": [r[1] for r in records],
    })
    return BlockResult(block_key=block_key, df=df.lazy())


def _name_mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="test", type="weighted", threshold=0.85,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def test_columnar_guard_sequential_branch_skips_set_output_identical():
    """<=2 blocks (sequential branch): track_matched=False skips the
    matched_pairs build; the returned DataFrame is byte-identical."""
    b = _block([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])
    mk = _name_mk()

    tracked: set = set()
    out_tracked = score_blocks_columnar([b], mk, tracked, track_matched=True)
    skipped: set = set()
    out_skipped = score_blocks_columnar([b], mk, skipped, track_matched=False)

    assert pairs_df_to_list(out_tracked.sort(["id_a", "id_b"])) == \
        pairs_df_to_list(out_skipped.sort(["id_a", "id_b"])), \
        "returned pair stream must NOT depend on whether matched_pairs is built"
    assert len(skipped) == 0, "track_matched=False must leave matched_pairs untouched"
    assert len(tracked) > 0, "track_matched=True must still populate the exclude set"


def test_columnar_guard_parallel_branch_skips_set_output_identical():
    """>2 blocks (ThreadPoolExecutor branch): same guard, same parity."""
    blocks = [
        _block([(1, "John Smith"), (2, "Jon Smith")], "b1"),
        _block([(10, "Alice Anderson"), (11, "Alice Andersen")], "b2"),
        _block([(20, "Bob Brown"), (21, "Bob Browne")], "b3"),
    ]
    mk = _name_mk()

    tracked: set = set()
    out_tracked = score_blocks_columnar(blocks, mk, tracked, track_matched=True)
    skipped: set = set()
    out_skipped = score_blocks_columnar(blocks, mk, skipped, track_matched=False)

    assert sorted(pairs_df_to_list(out_tracked)) == sorted(pairs_df_to_list(out_skipped))
    assert len(skipped) == 0
    assert len(tracked) > 0


def test_columnar_guard_default_preserves_side_effect():
    """Default (track_matched omitted) must still populate matched_pairs —
    the existing contract every non-pipeline caller relies on."""
    b = _block([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])
    matched: set = set()
    score_blocks_columnar([b], _name_mk(), matched)
    assert len(matched) > 0, "default behavior must keep building matched_pairs"


# ── Stage 2: the list-path guard ─────────────────────────────────────
# score_blocks_parallel is the DEFAULT scorer (columnar is opt-in). The 1M
# profile measured ~100s of matched_pairs min/max/set.add on this path, and
# for a single-matchkey list config matched_pairs is DEAD (never consumed) —
# same situation as columnar. So the same guard eliminates ~17% of the
# default-path wall for the common single-matchkey case. The pipeline passes
# track_matched=False ONLY when no later pass consumes the set; these tests
# prove (a) the guard skips the build without changing the returned pairs and
# (b) it is LOAD-BEARING — skipping on a CONSUMED pass changes the next pass.


def test_parallel_guard_sequential_branch_skips_set_output_identical():
    """<=2 blocks: track_matched=False skips the matched_pairs build; the
    returned pair LIST is identical."""
    b = _block([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])
    mk = _name_mk()

    tracked: set = set()
    out_tracked = score_blocks_parallel([b], mk, tracked, track_matched=True)
    skipped: set = set()
    out_skipped = score_blocks_parallel([b], mk, skipped, track_matched=False)

    assert sorted(out_tracked) == sorted(out_skipped), \
        "returned pair list must NOT depend on whether matched_pairs is built"
    assert len(skipped) == 0, "track_matched=False must leave matched_pairs untouched"
    assert len(tracked) > 0, "track_matched=True must still populate the exclude set"


def test_parallel_guard_parallel_branch_skips_set_output_identical():
    """>2 blocks (ThreadPoolExecutor branch): same guard, same parity."""
    blocks = [
        _block([(1, "John Smith"), (2, "Jon Smith")], "b1"),
        _block([(10, "Alice Anderson"), (11, "Alice Andersen")], "b2"),
        _block([(20, "Bob Brown"), (21, "Bob Browne")], "b3"),
    ]
    mk = _name_mk()

    tracked: set = set()
    out_tracked = score_blocks_parallel(blocks, mk, tracked, track_matched=True)
    skipped: set = set()
    out_skipped = score_blocks_parallel(blocks, mk, skipped, track_matched=False)

    assert sorted(out_tracked) == sorted(out_skipped)
    assert len(skipped) == 0
    assert len(tracked) > 0


def test_parallel_guard_default_preserves_side_effect():
    """Default must still populate matched_pairs (the cross-pass contract)."""
    b = _block([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])
    matched: set = set()
    score_blocks_parallel([b], _name_mk(), matched)
    assert len(matched) > 0


def test_parallel_guard_is_load_bearing_on_a_consumed_pass():
    """The guard MUST stay on (track_matched=True) for a pass whose set a
    LATER pass consumes. Proof: skipping the build on pass 1 makes pass 2 see
    an empty exclude and re-emit pairs pass 1 already found -> different output.
    This is why the pipeline computes track_matched from 'is there a later
    consuming pass', and never blanket-False."""
    b = _block([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])
    mk = _name_mk()

    # Correct: pass 1 records its pairs; pass 2 excludes them.
    correct: set = set()
    score_blocks_parallel([b], mk, correct, track_matched=True)
    p2_correct = score_blocks_parallel([b], mk, correct)

    # Wrong: pass 1 skips recording; pass 2 sees an empty exclude and re-emits.
    wrong: set = set()
    score_blocks_parallel([b], mk, wrong, track_matched=False)
    p2_wrong = score_blocks_parallel([b], mk, wrong)

    assert sorted(p2_correct) != sorted(p2_wrong), (
        "guard is not load-bearing — exclusion semantics broken; "
        f"correct_pass2={sorted(p2_correct)}, wrong_pass2={sorted(p2_wrong)}"
    )
