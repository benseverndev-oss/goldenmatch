"""GOLDENMATCH_FS_SIGNATURE_PRUNE: prune multi-pass candidates by pass signature."""
from __future__ import annotations

from itertools import combinations

import numpy as np
import polars as pl
import pytest
from goldenmatch.core import signature_prune as S

ENV = S.ENV


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


# ---- rule parsing --------------------------------------------------------------

@pytest.mark.parametrize("val", [None, "", "0", "off", "false", " OFF "])
def test_off_by_default(monkeypatch, val):
    if val is not None:
        monkeypatch.setenv(ENV, val)
    assert S.signature_prune_rule() is None


@pytest.mark.parametrize("val", ["1", "on", "true", " ON ", "dominant"])
def test_on_means_dominant(monkeypatch, val):
    monkeypatch.setenv(ENV, val)
    assert S.signature_prune_rule() == "dominant"


def test_multi_is_selectable(monkeypatch):
    monkeypatch.setenv(ENV, " MULTI ")
    assert S.signature_prune_rule() == "multi"


@pytest.mark.parametrize("val", ["2", "0.9", "abc"])
def test_unknown_values_stay_off(monkeypatch, val):
    monkeypatch.setenv(ENV, val)
    assert S.signature_prune_rule() is None


def test_unknown_rule_is_rejected_by_the_fitter():
    with pytest.raises(ValueError):
        S._fit_from_codes([np.array([0, 0]), np.array([0, 0])], np.arange(2), "count")


# ---- block keys ----------------------------------------------------------------

def test_pass_key_codes_treat_null_and_sentinels_as_invalid():
    from goldenmatch.config.schemas import BlockingKeyConfig
    from goldenmatch.core.frame import to_frame

    df = pl.DataFrame({"__row_id__": [0, 1, 2, 3, 4], "zip": ["a", "a", None, "null", "b"]})
    codes = S._pass_key_codes(to_frame(df), BlockingKeyConfig(fields=["zip"], transforms=[]))
    assert codes[0] == codes[1] >= 0
    assert codes[2] == -1 and codes[3] == -1
    assert codes[4] >= 0 and codes[4] != codes[0]


@pytest.mark.parametrize("backend", ["polars", "arrow"])
def test_pass_key_validity_matches_the_blockers_filter_valid_key(backend):
    """ENFORCES the docstring claim: a row the pruner treats as having an invalid
    key is exactly a row ``Frame.filter_valid_key`` drops before blocking. If the
    two drifted, the pruner would score co-firing for pairs no block contains
    (or miss ones it does). Both halves run on the same keys, on both frame
    backends, including the whitespace/case sentinel variants and the empty
    string the blocker deliberately keeps."""
    import pyarrow as pa
    from goldenmatch.config.schemas import BlockingKeyConfig
    from goldenmatch.core.frame import to_frame

    values = ["a", "A", None, "null", " NaN ", "None", "", "none ", "x", "nan"]
    ids = list(range(len(values)))
    if backend == "polars":
        fr = to_frame(pl.DataFrame({"__row_id__": ids, "k": values}))
    else:
        fr = to_frame(pa.table({"__row_id__": pa.array(ids, pa.int64()), "k": pa.array(values)}))
    key = BlockingKeyConfig(fields=["k"], transforms=["lowercase"])

    codes = S._pass_key_codes(fr, key)
    pruner_valid = {i for i, c in zip(ids, codes) if c >= 0}

    keyed = fr.with_column("__block_key__", fr.derive_block_key(["k"], ["lowercase"]))
    blocker_valid = set(keyed.filter_valid_key("__block_key__").column("__row_id__").to_list())

    assert pruner_valid == blocker_valid
    assert 6 in pruner_valid, "the empty string is a real key value for both"
    assert codes[0] == codes[1], "a lowercase transform must make 'a' and 'A' one block"


def test_invalid_keys_never_co_fire():
    codes = [np.array([-1, -1, 3]), np.array([0, 0, 0])]
    sig = S._row_signatures(codes, np.array([0]), np.array([1]))
    assert sig.tolist() == [0b10]


# ---- the distinct-pair estimator ---------------------------------------------

def _all_pair_signatures(codes):
    n_rows = len(codes[0])
    a, b = (np.array(x) for x in zip(*combinations(range(n_rows), 2)))
    sig = S._row_signatures(codes, a, b)
    cand = sig > 0
    return a[cand], b[cand], sig[cand]


def test_sampled_counts_estimate_distinct_pairs_per_signature():
    """KNOWN-POSITIVE for the 1/popcount weighting: without it, signatures that
    co-fire in several passes are overcounted by their popcount."""
    rng = np.random.default_rng(0)
    base = rng.integers(0, 25, size=300)
    codes = [base, base // 2, rng.integers(0, 40, size=300)]
    _, _, sig = _all_pair_signatures(codes)
    uniq, counts = np.unique(sig, return_counts=True)
    exact = dict(zip(uniq.tolist(), counts.tolist()))
    est, _ = S._sample_signature_counts(codes, 400_000, np.random.default_rng(1))
    assert sum(est.values()) == pytest.approx(len(sig), rel=0.02)
    for s, count in exact.items():
        if count >= 200:
            assert est[s] == pytest.approx(count, rel=0.1), s


# ---- grouping ------------------------------------------------------------------

def test_correlated_passes_merge_into_one_group():
    masks = np.array([0b011, 0b100, 0b111])
    counts = np.array([50.0, 40.0, 10.0])
    assert S._groups(S._phi_matrix(masks, counts, 3), 0.5) == [[0, 1], [2]]


def test_identical_constant_passes_merge():
    """A pass firing on EVERY candidate has no variance, so phi is undefined.
    Two such passes carry the same information and must merge; leaving phi at 0
    kept them apart, which is how three copies of one pass dodged the
    all-correlated decline."""
    masks = np.array([0b111, 0b011])
    counts = np.array([30.0, 70.0])
    assert S._groups(S._phi_matrix(masks, counts, 3), 0.5) == [[0, 1], [2]]


def test_group_masks_fire_when_any_member_fires():
    got = S._to_group_masks(np.array([0b001, 0b010, 0b100, 0b000]), [[0, 1], [2]])
    assert got.tolist() == [0b01, 0b01, 0b10, 0b00]


# ---- the rules -----------------------------------------------------------------

def _world(seed=0, n_entities=900, dup_rate=0.5, n_names=40, n_vals=400):
    """Three passes are copies of one namesake-prone name; dob and zip are
    independent. A duplicate's second row corrupts name with p=0.3 and dob / zip
    with p=0.2 each. The name group fires on ~80% of candidates; dob and zip on
    ~11% each."""
    rng = np.random.default_rng(seed)
    name, dob, zipc, ent = [], [], [], []
    for e in range(n_entities):
        nm, d, z = (int(rng.integers(n_names)), int(rng.integers(n_vals)), int(rng.integers(n_vals)))
        copies = 2 if rng.random() < dup_rate else 1
        for c in range(copies):
            dup = c == 1
            name.append(int(rng.integers(n_names, 2 * n_names)) if dup and rng.random() < 0.3 else nm)
            dob.append(int(rng.integers(n_vals)) if dup and rng.random() < 0.2 else d)
            zipc.append(int(rng.integers(n_vals)) if dup and rng.random() < 0.2 else z)
            ent.append(e)
    name_arr = np.array(name)
    codes = [name_arr, name_arr, name_arr, np.array(dob), np.array(zipc)]
    return codes, np.arange(len(name_arr)), np.array(ent)


GROUPS = [[0, 1, 2], [3], [4]]
NAME_ONLY = 0b001


@pytest.fixture(scope="module")
def world():
    codes, rows, ent = _world()
    a, b, sig = _all_pair_signatures(codes)
    gsig = S._to_group_masks(sig, GROUPS)
    return codes, rows, ent, a, b, gsig


def test_fit_merges_the_correlated_name_passes(world):
    codes, rows, *_ = world
    pruner = S._fit_from_codes(codes, rows, "dominant")
    assert pruner is not None
    assert pruner.report["groups"] == [["pass0", "pass1", "pass2"], ["pass3"], ["pass4"]]
    assert pruner.report["fire_rates"][0] > S._DOMINANT_SHARE
    assert max(pruner.report["fire_rates"][1:]) < S._DOMINANT_SHARE


def test_dominant_drops_exactly_the_lone_name_group(world):
    """KNOWN-POSITIVE that the rule prunes: the lone dominant group is dropped
    and nothing else is."""
    codes, rows, _, a, b, gsig = world
    pruner = S._fit_from_codes(codes, rows, "dominant")
    assert pruner is not None
    keep = pruner.keep_mask(a, b)
    assert np.array_equal(keep, gsig != NAME_ONLY)
    assert 0.0 < keep.mean() < 0.5


def test_multi_keeps_only_pairs_firing_two_groups(world):
    codes, rows, _, a, b, gsig = world
    pruner = S._fit_from_codes(codes, rows, "multi")
    assert pruner is not None
    keep = pruner.keep_mask(a, b)
    assert np.array_equal(keep, S._popcount(gsig, 3) >= 2)


def test_dominant_keeps_more_duplicates_than_multi(world):
    codes, rows, ent, a, b, _ = world
    same = ent[a] == ent[b]
    dom = S._fit_from_codes(codes, rows, "dominant")
    mul = S._fit_from_codes(codes, rows, "multi")
    assert dom is not None and mul is not None
    assert dom.keep_mask(a, b)[same].mean() > mul.keep_mask(a, b)[same].mean()


def test_one_pass_declines():
    assert S._fit_from_codes([np.array([0, 0, 1])], np.arange(3), "dominant") is None


def test_all_passes_correlated_declines():
    name = np.repeat(np.arange(50), 4)
    assert S._fit_from_codes([name, name, name], np.arange(len(name)), "dominant") is None


def test_dominant_declines_without_a_dominant_group():
    """Four independent passes over near-unique keys: no group reaches the
    dominant share, so there is nothing for the rule to drop."""
    rng = np.random.default_rng(3)
    codes = [rng.integers(0, 300, size=400) for _ in range(4)]
    assert S._fit_from_codes(codes, np.arange(400), "dominant") is None


def test_unknown_rows_are_kept(world):
    codes, rows, *_ = world
    pruner = S._fit_from_codes(codes, rows, "multi")
    assert pruner is not None
    assert pruner.keep_mask(np.array([10**9]), np.array([0])).tolist() == [True]


def test_list_and_table_filters_agree(world):
    import pyarrow as pa

    codes, rows, _, a, b, _ = world
    pruner = S._fit_from_codes(codes, rows, "dominant")
    assert pruner is not None
    pairs = [(int(x), int(y), 0.5) for x, y in zip(a[:5000], b[:5000])]
    kept_list = pruner.filter_pairs(pairs)
    table = pa.table({
        "id_a": [p[0] for p in pairs], "id_b": [p[1] for p in pairs], "score": [p[2] for p in pairs],
    })
    kept_tbl = pruner.filter_table(table)
    assert [(x, y) for x, y, _ in kept_list] == list(
        zip(kept_tbl.column("id_a").to_pylist(), kept_tbl.column("id_b").to_pylist())
    )
    assert 0 < len(kept_list) < len(pairs)


def test_non_dense_row_ids_map_correctly(world):
    codes, rows, _, a, b, _ = world
    sparse_ids = rows * 1_000_003 + 7
    dense = S._fit_from_codes(codes, rows, "dominant")
    sparse = S._fit_from_codes(codes, sparse_ids, "dominant")
    assert dense is not None and sparse is not None
    assert dense.keep_mask(a, b).tolist() == sparse.keep_mask(sparse_ids[a], sparse_ids[b]).tolist()


def test_fit_signature_pruner_on_a_frame():
    """End to end through the Frame seam: keys derived from real columns."""
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    codes, rows, _ = _world(n_entities=300)
    df = pl.DataFrame({
        "__row_id__": rows,
        "name": [f"n{v}" for v in codes[0]],
        "dob": [f"d{v}" for v in codes[3]],
        "zip": [f"z{v}" for v in codes[4]],
    })
    cfg = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["name"], transforms=[]),
            BlockingKeyConfig(fields=["name"], transforms=["lowercase"]),
            BlockingKeyConfig(fields=["dob"], transforms=[]),
            BlockingKeyConfig(fields=["zip"], transforms=[]),
        ],
    )
    pruner = S.fit_signature_pruner(df, cfg, "dominant", n_instances=50_000)
    assert pruner is not None
    assert pruner.report["groups"] == [["name[raw]", "name[raw]"], ["dob[raw]"], ["zip[raw]"]]
