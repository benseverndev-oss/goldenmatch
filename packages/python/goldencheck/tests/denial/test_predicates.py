from datetime import date, datetime

import polars as pl
from goldencheck.denial.models import Op, Predicate
from goldencheck.denial.predicates import build_predicate_space, encode_columns, predicate_holds


def test_numeric_rank_is_order_preserving():
    enc = encode_columns(pl.DataFrame({"x": [30, 10, 20, 10]}))
    ids = enc["x"].ids
    assert ids[1] < ids[2] < ids[0]   # 10 < 20 < 30
    assert ids[1] == ids[3]           # both 10 -> same id


def test_categorical_is_first_seen():
    enc = encode_columns(pl.DataFrame({"s": ["b", "a", "b", None]}))
    assert enc["s"].ids[0] == enc["s"].ids[2]      # both "b"
    assert enc["s"].ids[0] != enc["s"].ids[1]
    assert enc["s"].ids[3] == 0                     # null sentinel
    assert enc["s"].nulls[3] is True


def test_null_operand_predicate_false():
    df = pl.DataFrame({"a": [1, None, 3], "b": [2, 2, 2]})
    enc = encode_columns(df)
    p = Predicate(kind="single", col_a="a", op=Op.LT, col_b="b", literal=None)
    assert predicate_holds(p, enc, 0, None) is True    # 1 < 2
    assert predicate_holds(p, enc, 1, None) is False   # a is null -> NOT satisfied (not "0<2")


def test_literal_gating_low_card_high_support_only():
    df = pl.DataFrame({"country": ["US"] * 80 + ["CA"] * 20, "id": list(range(100))})
    space = build_predicate_space(df)
    consts = [p for p in space.predicates if p.kind == "const"]
    assert any(p.col_a == "country" and p.literal == "US" for p in consts)
    assert not any(p.col_a == "id" for p in consts)   # high-cardinality -> no literals


def test_pass2_budget_doubles_single_tuple():
    # a wide-ish frame; assert the accounting identity holds and capping is reported when needed
    df = pl.DataFrame({f"c{i}": list(range(20)) for i in range(6)})
    space = build_predicate_space(df)
    assert space.pass2_effective == 2 * space.n_single + space.n_cross
    if space.pass2_effective > 64 or space.n_single > 64:
        assert space.capped is True


def test_date_and_datetime_are_separate_dtype_domains():
    # Date and Datetime are different concrete dtypes: encoding must not crash by
    # pooling date + datetime into one sorted() domain, and they must NOT pair.
    df = pl.DataFrame(
        {
            "d": [date(2020, 1, 1), date(2020, 1, 3), date(2020, 1, 2)],
            "ts": [
                datetime(2020, 1, 1, 9),
                datetime(2020, 1, 1, 8),
                datetime(2020, 1, 1, 10),
            ],
        }
    )
    enc = encode_columns(df)  # must not raise
    assert enc["d"].dtype != enc["ts"].dtype
    # each column stays order-preserving within its own dtype domain
    assert enc["d"].ids[0] < enc["d"].ids[2] < enc["d"].ids[1]     # 1 < 2 < 3
    assert enc["ts"].ids[1] < enc["ts"].ids[0] < enc["ts"].ids[2]  # 8 < 9 < 10

    space = build_predicate_space(df)
    for p in space.predicates:
        if p.kind in ("single", "cross"):
            assert {p.col_a, p.col_b} != {"d", "ts"}  # different dtypes never pair
