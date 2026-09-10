"""``GivenNameAliasedJW.score_matrix`` must apply exactly ``score_pair``'s alias rule.

``score_pair`` (which EM trains with) and the native kernel treat two given names as
aliases when they are normalized-equal or their CANONICAL sets intersect
(``are_equivalent``). ``score_matrix`` used to intersect ``aliases_of`` MEMBER sets,
which is looser: 'catherine' (canonical catherine) and 'kathy' (canonical kathleen)
share the members kate/katie, so the numpy FS route scored them 1.0 while the scalar
route and the kernel scored 0.644. historical_50k scores ``first_name`` with this
scorer.
"""
from __future__ import annotations

import itertools

import pytest
from goldenmatch.refdata import given_names as G
from goldenmatch.refdata import scorer as S
from goldenmatch.refdata.scorer import GivenNameAliasedJW

pytestmark = pytest.mark.skipif(
    not S.given_names_available(), reason="given-name alias pack not bundled",
)


def test_known_positive_the_pair_shares_members_but_not_canonicals():
    """If this stops holding, the regression below tests nothing."""
    assert G.are_equivalent("catherine", "kathy") is False
    assert G.aliases_of("catherine") & G.aliases_of("kathy")


def test_matrix_does_not_promote_names_that_share_only_a_member():
    sc = GivenNameAliasedJW()
    got = float(sc.score_matrix(["catherine", "kathy"])[0][1])
    assert got == pytest.approx(sc.score_pair("catherine", "kathy"), abs=1e-6)


def test_matrix_matches_score_pair_over_the_whole_alias_table():
    """Every name the table knows, plus OOV, empty, and case/whitespace variants."""
    sc = GivenNameAliasedJW()
    forms = G.export_alias_forms()
    names: list[str | None] = sorted({c for c, _ in forms} | {n for _, fs in forms for n in fs})
    assert len(names) > 50, "alias table unexpectedly small; the sweep would prove little"
    names += ["zzqx", "", "Bob", " bob ", "KATHY"]
    m = sc.score_matrix(names)
    bad = []
    for i, j in itertools.combinations(range(len(names)), 2):
        want = sc.score_pair(names[i], names[j])
        if want is None:
            continue
        if abs(float(m[i][j]) - want) > 1e-6:
            bad.append((names[i], names[j], float(m[i][j]), want))
    assert not bad, f"{len(bad)} pairs disagree with score_pair, e.g. {bad[:5]}"
