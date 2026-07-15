import polars as pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.probabilistic import _sample_blocked_pairs, _sample_pairs


def _person_df():
    # several soundex-distributed surnames so blocks are non-trivial
    import itertools
    first = ["ann","bob","cara","dan","eve","fay","gus","hal"]
    sur = ["lee","kim","ng","ono","poe","qua","rae","sol"]
    rows = []
    for i, (f, s) in enumerate(itertools.product(first, sur)):
        rows.append({"first_name": f, "surname": s, "dob": f"19{50+(i%40):02d}-01-01"})
        rows.append({"first_name": f, "surname": s, "dob": f"19{50+(i%40):02d}-01-01"})  # a dup
    return pl.DataFrame({"__row_id__": list(range(len(rows))),
                         "first_name": [r["first_name"] for r in rows],
                         "surname": [r["surname"] for r in rows],
                         "dob": [r["dob"] for r in rows]})

def test_sample_blocked_pairs_is_order_independent():
    df = _person_df()
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["surname"])])
    blocks = build_blocks(df.lazy(), cfg)
    import random
    # same blocks, two DIFFERENT input orders (simulating non-deterministic construction)
    b1 = list(blocks)
    b2 = list(blocks); random.Random(1).shuffle(b2)
    b3 = list(blocks); random.Random(2).shuffle(b3)
    s1 = _sample_blocked_pairs(b1, n_pairs=200, seed=42)
    s2 = _sample_blocked_pairs(b2, n_pairs=200, seed=42)
    s3 = _sample_blocked_pairs(b3, n_pairs=200, seed=42)
    assert set(s1) == set(s2) == set(s3), "sample must be invariant to block input order"
    assert len(s1) > 0


def test_sample_pairs_linkage_enumerates_only_cross_table_pairs():
    df = pl.DataFrame({"__row_id__": [0, 1, 2, 3, 4]})
    target_ids = {0, 1, 2}

    pairs = _sample_pairs(df, n_pairs=100, seed=42, target_ids=target_ids)

    assert set(pairs) == {
        (0, 3), (0, 4),
        (1, 3), (1, 4),
        (2, 3), (2, 4),
    }


def test_sample_pairs_linkage_is_bounded_and_deterministic_when_imbalanced():
    df = pl.DataFrame({"__row_id__": list(range(102))})
    target_ids = set(range(100))

    first = _sample_pairs(df, n_pairs=50, seed=7, target_ids=target_ids)
    second = _sample_pairs(df, n_pairs=50, seed=7, target_ids=target_ids)

    assert first == second
    assert len(first) == 50
    assert all((a in target_ids) != (b in target_ids) for a, b in first)


def test_sample_blocked_pairs_linkage_drops_same_side_pairs():
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "surname": ["Smith"] * 6,
    })
    cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["surname"])])
    blocks = build_blocks(df.lazy(), cfg)
    target_ids = {0, 1, 2}

    pairs = _sample_blocked_pairs(
        blocks,
        n_pairs=100,
        seed=42,
        target_ids=target_ids,
    )

    assert len(pairs) == 9
    assert all((a in target_ids) != (b in target_ids) for a, b in pairs)
