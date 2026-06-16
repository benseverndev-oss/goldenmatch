"""Tests for collective entity resolution features."""
from tests.collective_er.metrics import pairwise_prf


def test_pairwise_prf_perfect():
    """Perfect clustering should yield F1=1.0."""
    truth = {0: "A", 1: "A", 2: "B"}          # record_id -> true entity
    clusters = {0: 0, 1: 0, 2: 1}             # record_id -> predicted cluster
    p, r, f = pairwise_prf(clusters, truth)
    assert (p, r, f) == (1.0, 1.0, 1.0)


def test_pairwise_prf_over_merge():
    """Over-merging should hurt precision while keeping recall full."""
    truth = {0: "A", 1: "A", 2: "B"}
    clusters = {0: 0, 1: 0, 2: 0}             # wrongly merged 2 with A
    p, r, f = pairwise_prf(clusters, truth)
    assert r == 1.0 and p < 1.0               # recall full, precision hurt
