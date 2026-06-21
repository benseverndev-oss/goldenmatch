"""The trained drug model's HONEST behavior: morphological lift + arbitrary-brand
non-generalization. These two tests ARE the measured result GS2 set out to find."""

from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from goldenmatch.synonym.drug import DrugSynonymModel
from goldenmatch.synonym.providers import resolve_synonym_model


def test_morphological_pair_beats_jw():
    # cefuroxime/cefuroxim: a HELD-OUT morphological variant (not in training).
    # The trained model lifts it above plain Jaro-Winkler — the real, measured win.
    m = DrugSynonymModel()
    a, b = "cefuroxime", "cefuroxim"
    assert m.score(a, b) > float(JaroWinkler.similarity(a, b))


def test_arbitrary_brand_does_not_generalize():
    # Advil/ibuprofen: a HELD-OUT arbitrary brand pair (no morphological signal).
    # The model canNOT resolve it — the measured ceiling, encoded as a test.
    s = DrugSynonymModel().score("Advil", "ibuprofen")
    assert s is not None and s < 0.5


def test_registered_for_drug_domain():
    import goldenmatch.synonym  # noqa: F401 - triggers registration

    assert type(resolve_synonym_model("drug")).__name__ == "DrugSynonymModel"


def test_none_guard():
    assert DrugSynonymModel().score(None, "x") is None
