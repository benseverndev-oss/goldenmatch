import polars as pl
from goldencheck.denial.models import Op, Predicate
from goldencheck.denial.validate import is_single_tuple, validate_cross_tuple, validate_single_tuple


def test_is_single_tuple():
    p1 = Predicate(kind="const", col_a="s", op=Op.EQ, col_b=None, literal="x")
    p2 = Predicate(kind="single", col_a="a", op=Op.LT, col_b="b", literal=None)
    p3 = Predicate(kind="cross", col_a="a", op=Op.LT, col_b="a", literal=None)
    assert is_single_tuple([p1, p2]) is True
    assert is_single_tuple([p1, p3]) is False


def test_validate_single_tuple_exact_rows():
    # DC ¬(status=shipped ∧ ship < order): violated by rows where BOTH hold.
    df = pl.DataFrame({
        "status": ["shipped", "shipped", "pending", "shipped"],
        "ship":   [1, 5, 1, 2],
        "order":  [3, 4, 9, 8],   # row0 ship<order (1<3) & shipped -> VIOLATION; row1 5<4 false; row3 2<8 & shipped -> VIOLATION
    })
    preds = [
        Predicate(kind="const", col_a="status", op=Op.EQ, col_b=None, literal="shipped"),
        Predicate(kind="single", col_a="ship", op=Op.LT, col_b="order", literal=None),
    ]
    g1, rows = validate_single_tuple(preds, df)
    assert sorted(rows) == [0, 3]
    assert abs(g1 - 2/4) < 1e-9


def test_validate_cross_tuple_estimates_and_examples():
    # DC ¬(tα.a < tβ.a): violated by any ordered pair where a[α] < a[β].
    df = pl.DataFrame({"a": [1, 2, 3]})
    preds = [Predicate(kind="cross", col_a="a", op=Op.LT, col_b="a", literal=None)]
    g1, pairs = validate_cross_tuple(preds, df, sample=3)
    # ordered pairs (α,β) α≠β = 6; a[α]<a[β] holds for (0,1),(0,2),(1,2) = 3 -> g1=0.5
    assert abs(g1 - 0.5) < 1e-9
    assert len(pairs) >= 1 and all(df["a"][i] < df["a"][j] for i, j in pairs)


def test_validate_single_tuple_no_violation():
    df = pl.DataFrame({"a": [1, 2], "b": [5, 6]})
    preds = [Predicate(kind="single", col_a="a", op=Op.GT, col_b="b", literal=None)]  # a>b never
    g1, rows = validate_single_tuple(preds, df)
    assert rows == [] and g1 == 0.0
