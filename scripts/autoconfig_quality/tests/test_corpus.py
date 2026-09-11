"""The FS link-cut routing corpus is frozen: rows are written from DESIGN and must
also hold on HOLDOUT, which is why HOLDOUT is pinned verbatim here."""

from __future__ import annotations

import json

import pytest

from scripts.autoconfig_quality import corpus as C


def test_design_set_is_pinned():
    assert C.DESIGN == (
        "person",
        "household_hardneg",
        "cotenant_hardneg",
        "febrl3",
        "febrl4",
        "ncvr_synthetic",
        "ncvr_real",
        "historical_50k",
        "dblp_acm",
        "dblp_scholar",
        "amazon_google",
    )


def test_holdout_set_is_frozen():
    """Changing this list after any routing row exists means re-running the full
    gate on both sets (spec: Corpus and harness)."""
    assert C.HOLDOUT == (
        "abt_buy",
        "walmart_amazon",
        "itunes_amazon",
        "fodors_zagats",
        "febrl1",
        "febrl2",
        "synth_person_c05_d10",
        "synth_person_c05_d40",
        "synth_person_c20_d10",
        "synth_person_c20_d40",
        "synth_biblio_c05_d10",
        "synth_biblio_c05_d40",
        "synth_biblio_c20_d10",
        "synth_biblio_c20_d40",
    )


def test_sets_are_disjoint_and_duplicate_free():
    assert not set(C.DESIGN) & set(C.HOLDOUT)
    assert len(set(C.DESIGN)) == len(C.DESIGN)
    assert len(set(C.HOLDOUT)) == len(C.HOLDOUT)


def test_corpus_of_and_select():
    assert C.corpus_of("febrl3") == "design"
    assert C.corpus_of("abt_buy") == "holdout"
    assert C.corpus_of("synthetic_person") == "unlisted"
    assert C.select("all") == C.DESIGN + C.HOLDOUT
    with pytest.raises(KeyError):
        C.select("everything")


def test_ci_never_provisions_real_voter_pii():
    assert "ncvr_real" in C.LOCAL_ONLY
    assert "ncvr_real" not in C.ci_datasets("design")
    assert C.ci_datasets("holdout") == list(C.HOLDOUT)


def test_cli_lists_ci_datasets_as_json(capsys):
    assert C.main(["--list", "design", "--ci"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == C.ci_datasets("design")
