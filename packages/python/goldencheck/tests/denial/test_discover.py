from goldencheck.denial.discover import _complement, discover, rank


def test_complement_bounds_to_p_bits():
    assert _complement(0b0000, 3) == 0b111        # no phantom high bits
    assert _complement(0b0101, 4) == 0b1010


def test_planted_dc_recovered_strict():
    # 3 predicates (bits 0,1,2). Evidence: no element has bits {0,1} both set.
    # Elements: {0}, {1}, {2}, {0,2}, {1,2}  -> {0,1} never co-occur -> DC = {0,1}
    ev = {0b001: 10, 0b010: 10, 0b100: 5, 0b101: 3, 0b110: 2}
    total = sum(ev.values())
    dcs = discover(ev, n_predicates=3, total=total, eps=0.0)
    assert 0b011 in dcs                            # {0,1} is a DC
    # and no superset of {0,1} is separately returned (minimality)
    assert 0b111 not in dcs


def test_approximate_dc_within_eps():
    # {0,1} co-occur in only 1 of 100 elements -> a DC at eps=0.05 but not eps=0.0
    ev = {0b001: 49, 0b010: 49, 0b011: 1, 0b100: 1}
    total = 100
    assert 0b011 in discover(ev, 3, total, eps=0.05)
    assert 0b011 not in discover(ev, 3, total, eps=0.0)


def test_minimality_no_superset():
    ev = {0b001: 10, 0b010: 10, 0b100: 10}     # bits 0,1,2 never co-occur pairwise
    dcs = discover(ev, 3, 30, eps=0.0)
    # every returned DC is minimal: none is a superset of another
    for x in dcs:
        assert not any(y != x and (y & x) == y for y in dcs)


def test_rank_is_deterministic_and_capped():
    ev = {0b001: 10, 0b010: 10, 0b100: 10}
    dcs = discover(ev, 3, 30, eps=0.0)
    r1 = rank(dcs, ev, 30, max_constraints=2)
    r2 = rank(dcs, ev, 30, max_constraints=2)
    assert r1 == r2 and len(r1) <= 2
