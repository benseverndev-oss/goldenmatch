from goldencheck.denial.models import DenialConstraint, Op, Predicate


def test_predicate_render_constant_and_variable():
    p_const = Predicate(kind="const", col_a="country", op=Op.EQ, col_b=None, literal="US")
    p_cmp = Predicate(kind="single", col_a="ship_date", op=Op.LT, col_b="order_date", literal=None)
    assert p_const.render() == "country = 'US'"
    assert p_cmp.render() == "ship_date < order_date"


def test_dc_columns_ordered_and_deduped():
    dc = DenialConstraint(
        predicates=(
            Predicate(kind="const", col_a="status", op=Op.EQ, col_b=None, literal="shipped"),
            Predicate(kind="single", col_a="ship_date", op=Op.LT, col_b="order_date", literal=None),
        ),
        g1=0.006, support=500, tuple_scope="single", exact=True,
    )
    assert dc.columns() == ("status", "ship_date", "order_date")


def test_dc_render_canonical():
    dc = DenialConstraint(
        predicates=(
            Predicate(kind="const", col_a="status", op=Op.EQ, col_b=None, literal="shipped"),
            Predicate(kind="single", col_a="ship_date", op=Op.LT, col_b="order_date", literal=None),
        ),
        g1=0.0, support=500, tuple_scope="single", exact=True,
    )
    r = dc.render()
    assert r.startswith("¬(") and r.endswith(")")
    assert "status = 'shipped'" in r and "ship_date < order_date" in r
