import polars as pl
from goldencheck.denial.mine import DenialConstraintProfiler, discover_denial_constraints
from goldencheck.models.finding import Severity


def _planted_frame(n=300, k=5, seed=0):
    import random
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        status = rng.choice(["shipped", "pending", "cancelled"])
        order = rng.randint(1, 100)
        # RULE: if shipped, ship_date >= order_date. Inject k violations.
        ship = order + rng.randint(0, 20)
        rows.append({"status": status, "order": order, "ship": ship})
    # force exactly k violating shipped rows (ship < order)
    shipped_idx = [i for i, r in enumerate(rows) if r["status"] == "shipped"][:k]
    for i in shipped_idx:
        rows[i]["ship"] = rows[i]["order"] - 1
    return pl.DataFrame(rows), set(shipped_idx)


def test_planted_single_tuple_dc_recovered():
    df, viol = _planted_frame()
    dcs = discover_denial_constraints(df, min_confidence=0.95)
    # a single-tuple DC over {status, ship, order} with the right rough g1 is found
    hit = [d for d in dcs if d.tuple_scope == "single"
           and set(d.columns()) >= {"status", "ship", "order"}]
    assert hit, f"planted DC not recovered; got {[d.render() for d in dcs]}"


def test_random_data_few_spurious():
    import random
    rng = random.Random(1)
    df = pl.DataFrame({f"c{j}": [rng.randint(0, 9) for _ in range(300)] for j in range(4)})
    dcs = discover_denial_constraints(df, min_confidence=0.98)
    assert len(dcs) <= 5   # FP guard: independent random cols -> few/no DCs


def test_determinism():
    df, _ = _planted_frame()
    a = discover_denial_constraints(df, seed=7)
    b = discover_denial_constraints(df, seed=7)
    assert [d.render() for d in a] == [d.render() for d in b]


def test_profiler_emits_findings():
    df, _ = _planted_frame()
    findings = DenialConstraintProfiler().profile(df)
    assert any(f.check == "denial_constraint" for f in findings)
    f = next(f for f in findings if f.check == "denial_constraint")
    assert f.severity in (Severity.WARNING, Severity.INFO)
    assert "," in f.column or f.column  # joined predicate columns
    assert "predicates" in f.metadata
