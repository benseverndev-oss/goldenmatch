"""Per-name adaptive co-author threshold: size-aware, clamped, unsupervised."""
from adaptive import per_name_coauthor_threshold
from normalize import encode_set


def _cells(sizes):
    """Build co-author cells with the given per-paper co-author counts."""
    return [encode_set([f"co{n}_{i}" for i in range(k)]) for n, k in enumerate(sizes)]


def test_bigger_blocks_get_a_lower_threshold():
    # median co-author count 2 (small) vs 10 (big-collaboration)
    small = per_name_coauthor_threshold(_cells([2, 2, 2, 2]), t_min=0.02, t_max=0.5)
    big = per_name_coauthor_threshold(_cells([10, 10, 10, 10]), t_min=0.02, t_max=0.5)
    assert big < small  # big-collaboration blocks accept single-shared at lower J


def test_targets_single_shared_coauthor():
    # median 3 -> U_typical = 2*3-1 = 5 -> alpha/U = 0.2; one shared over 5 = 0.2
    t = per_name_coauthor_threshold(_cells([3, 3, 3]), alpha=1.0, t_min=0.01, t_max=0.9)
    assert abs(t - 0.2) < 1e-9


def test_clamped_to_bounds():
    # huge co-author sets -> alpha/U tiny -> clamped up to t_min
    t = per_name_coauthor_threshold(_cells([50, 50, 50]), t_min=0.06, t_max=0.20)
    assert t == 0.06
    # tiny sets -> alpha/U big -> clamped down to t_max
    t2 = per_name_coauthor_threshold(_cells([1, 1, 1]), t_min=0.06, t_max=0.20)
    assert t2 == 0.20


def test_empty_coauthors_keeps_strict_bar():
    # no co-author signal -> return t_max (orgtext carries these in the relational engine)
    assert per_name_coauthor_threshold(["", "", None], t_max=0.20) == 0.20


def test_alpha_scales_the_bar():
    lo = per_name_coauthor_threshold(_cells([5, 5, 5]), alpha=1.0, t_min=0.01, t_max=0.9)
    hi = per_name_coauthor_threshold(_cells([5, 5, 5]), alpha=2.0, t_min=0.01, t_max=0.9)
    assert hi > lo  # requiring ~2 shared co-authors is a stricter bar
