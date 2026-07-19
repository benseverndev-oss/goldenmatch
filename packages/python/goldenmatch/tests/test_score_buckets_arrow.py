"""B1 of the FS Arrow-native pair-stream cutover (design doc
2026-07-18-fs-arrow-pair-stream-design.md).

score_buckets returns list[tuple[int,int,float]] + mutates a matched_pairs set
(~16 GB at 66M pairs). score_buckets_arrow returns the SAME emitted pairs as a
PAIR_STREAM_SCHEMA pa.Table (id_a/id_b int64, score float64) so the FS clustering
path can go Arrow-native (build_clusters_arrow_native). B1 = the seam + schema +
parity; the memory win lands in B2 when the caller consumes the table directly.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.backends.score_buckets import (
    pairs_to_pair_stream,
    score_buckets,
    score_buckets_arrow,
)
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.frame import PAIR_STREAM_SCHEMA_SPEC
from goldenmatch.core.probabilistic import train_em


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "first_name": ["John", "Jon", "Jonn", "Jane", "Janet",
                       "Bob", "Rob", "Bobby", "Robert", "Zoe"],
        "last_name": ["Smith", "Smith", "Smyth", "Doe", "Doe",
                      "Jones", "Jones", "Jones", "Jones", "Xu"],
        "zip": ["90210", "90210", "90210", "10001", "10001",
                "60601", "60601", "60601", "60601", "77777"],
    })


def _table_to_tuples(tbl) -> list[tuple[int, int, float]]:
    d = tbl.to_pydict()
    return list(zip(d["id_a"], d["id_b"], d["score"]))


def _canon(pairs) -> list[tuple[int, int, float]]:
    return sorted((min(a, b), max(a, b), round(float(s), 4)) for a, b, s in pairs)


# ── pairs_to_pair_stream: schema + faithful round-trip ────────────────────────

def test_pair_stream_schema_matches_spec():
    tbl = pairs_to_pair_stream([(1, 2, 0.9), (3, 4, 0.5)])
    assert tbl.column_names == list(PAIR_STREAM_SCHEMA_SPEC)  # id_a, id_b, score
    assert tbl.schema.field("id_a").type == pa.int64()
    assert tbl.schema.field("id_b").type == pa.int64()
    assert tbl.schema.field("score").type == pa.float64()


def test_pair_stream_empty_is_zero_row_typed_table():
    tbl = pairs_to_pair_stream([])
    assert tbl.num_rows == 0
    assert tbl.column_names == list(PAIR_STREAM_SCHEMA_SPEC)
    assert tbl.schema.field("score").type == pa.float64()


def test_pair_stream_preserves_order_and_duplicates():
    # Cross-pass duplicate edges must survive verbatim (Union-Find collapses
    # them downstream; dropping them here would silently change the contract).
    pairs = [(1, 2, 0.9), (2, 1, 0.9), (3, 4, 0.5), (1, 2, 0.9)]
    assert _table_to_tuples(pairs_to_pair_stream(pairs)) == pairs


# ── score_buckets_arrow == score_buckets (byte-faithful delegation) ───────────

@pytest.mark.parametrize(
    "blocking",
    [
        BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
        # multi_pass exercises cross-pass duplicate emission (zip + last_name
        # both surface the 60601/Jones pairs) — the contract pairs_to_pair_stream
        # must preserve.
        BlockingConfig(strategy="multi_pass", passes=[
            BlockingKeyConfig(fields=["zip"]),
            BlockingKeyConfig(fields=["last_name"]),
        ]),
    ],
    ids=["static", "multi_pass"],
)
def test_score_buckets_arrow_equals_list(blocking):
    df = _df()
    mk = _mk()
    em = train_em(df, mk, n_sample_pairs=200)

    list_pairs = score_buckets(df, blocking, mk, set(), em_result=em)
    arrow_tbl = score_buckets_arrow(df, blocking, mk, set(), em_result=em)

    # Same emitted edge set (canonicalized, score-rounded), duplicates included.
    assert _canon(_table_to_tuples(arrow_tbl)) == _canon(list_pairs)
    assert arrow_tbl.column_names == list(PAIR_STREAM_SCHEMA_SPEC)


def test_score_buckets_arrow_skips_matched_pairs_by_design():
    # The memory win: list mode mutates matched_pairs in place (~8 GB at 66M
    # pairs); arrow mode SKIPS it (duplicate edges collapse in Union-Find, so the
    # cross-pass exclude is a perf optimization, not correctness). The FS caller
    # routes to arrow only when no later pass consumes the exclude set.
    df, mk = _df(), _mk()
    em = train_em(df, mk, n_sample_pairs=200)
    blocking = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])])

    mp_list: set = set()
    score_buckets(df, blocking, mk, mp_list, em_result=em)
    assert len(mp_list) > 0  # list mode populates the exclude set

    mp_arrow: set = set()
    score_buckets_arrow(df, blocking, mk, mp_arrow, em_result=em)
    assert mp_arrow == set()  # arrow mode leaves it untouched (by design)


# ── B2b: pipeline FS bucket path Arrow route (GOLDENMATCH_FS_ARROW_STREAM) ─────

def _person_dupe_df() -> pl.DataFrame:
    return pl.DataFrame({
        "first_name": ["John", "Jon", "Mary", "Marie", "Robert", "Bob", "Linda", "Lynda"],
        "surname": ["Smith", "Smith", "Jones", "Jones", "Brown", "Brown", "Davis", "Davis"],
        "dob": ["1980-01-01", "1980-01-01", "1975-05-05", "1975-05-05",
                "1990-09-09", "1990-09-09", "1985-03-03", "1985-03-03"],
        "zip": ["11111", "11111", "22222", "22222", "33333", "33333", "44444", "44444"],
    })


def _memberships(res) -> list[tuple[int, ...]]:
    return sorted(tuple(sorted(c["members"])) for c in res.clusters.values())


def test_fs_arrow_stream_eligibility():
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.pipeline import _fs_arrow_stream_eligible

    cfg = GoldenMatchConfig(
        matchkeys=[_mk()],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    one = [_mk()]
    assert _fs_arrow_stream_eligible(cfg, one, across_files_only=False, bench_dump=False)
    # ineligible: two matchkeys / across-files / bench-dump each disqualify.
    assert not _fs_arrow_stream_eligible(cfg, [_mk(), _mk()], False, False)
    assert not _fs_arrow_stream_eligible(cfg, one, True, False)
    assert not _fs_arrow_stream_eligible(cfg, one, False, True)


def test_fs_arrow_stream_pipeline_parity(monkeypatch):
    # Clusters byte-identical (membership) with the Arrow route ON vs OFF, and a
    # spy proves the Arrow path actually engaged (guards against a vacuous pass).
    import goldenmatch.backends.score_buckets as sb_mod
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    monkeypatch.setenv("GOLDENMATCH_FS_AUTOCONFIG_V2", "1")
    df = _person_dupe_df()
    cfg = auto_configure_probabilistic_df(df)

    monkeypatch.setenv("GOLDENMATCH_FS_ARROW_STREAM", "0")
    off = dedupe_df(df, config=cfg, confidence_required=False)

    calls = {"n": 0}
    real_arrow = sb_mod.score_buckets_arrow

    def _spy(*a, **k):
        calls["n"] += 1
        return real_arrow(*a, **k)

    monkeypatch.setattr(sb_mod, "score_buckets_arrow", _spy)
    monkeypatch.setenv("GOLDENMATCH_FS_ARROW_STREAM", "1")
    on = dedupe_df(df, config=cfg, confidence_required=False)

    assert calls["n"] >= 1, "Arrow FS pair-stream path did not engage"
    assert _memberships(on) == _memberships(off)
    assert any(len(m) > 1 for m in _memberships(off)), "fixture must form a real cluster"
