"""FS field-agnostic orthogonal-anchor blocking (GOLDENMATCH_FS_ORTHOGONAL_BLOCKING).

`_diversify_probabilistic_blocking` (v2 lever #3) only diversifies onto `date` +
`zip`/`identifier`/`phone` col_types. The strongest orthogonal anchor a dataset
carries may be classified as something else -- on historical_50k `birth_place`
profiles as `name` (null 0.13, card 0.48), slips past that whitelist, and the FS
candidate set stays gated on the corrupted name keys (blocking_recall caps ~0.78).

`_diversify_unused_orthogonal_blocking` selects by DATA SHAPE (coverage +
cardinality band), not col_type, and marks its passes `additive=True` so the field
stays EM-trained (co-locate WITHOUT demoting the discriminator -- demoting it costs
F1, demoting a strong name field collapses recall). Default OFF => byte-identical.

Measured (scripts/bench_er_headtohead out-of-panel harness, flag OFF->ON):
historical_50k F1 0.826->0.847, febrl3 0.987->0.994, febrl4 (holdout) 0.989->0.995
-- recall-driven, no holdout regression.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _diversify_unused_orthogonal_blocking,
    auto_configure_probabilistic_df,
)
from goldenmatch.core.blocker import collect_blocking_fields

FLAG = "GOLDENMATCH_FS_ORTHOGONAL_BLOCKING"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _p(name, col_type, card=0.5, null=0.0):
    return ColumnProfile(
        name=name, dtype="Utf8", col_type=col_type, confidence=0.9,
        null_rate=null, cardinality_ratio=card, avg_len=8,
    )


def _name_blocking():
    """Blocking keyed entirely on the name composites (the historical_50k shape)."""
    return BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["full_name"], transforms=["soundex"]),
            BlockingKeyConfig(fields=["first_and_surname"], transforms=["substring:0:5"]),
        ],
    )


def _person_profiles():
    return [
        _p("record_id", "identifier", card=1.0),      # near-unique surrogate
        _p("full_name", "name", card=0.96),            # primary block key
        _p("first_and_surname", "name", card=0.95),    # primary block key
        _p("first_name", "name", card=0.42),           # moderate, unused
        _p("surname", "name", card=0.74, null=0.10),   # moderate, unused
        _p("birth_place", "name", card=0.48, null=0.13),  # THE orthogonal anchor
        _p("gender", "name", card=0.002),              # near-constant
        _p("occupation", "name", card=0.14, null=0.49),  # too sparse
    ]


# ── selection logic ─────────────────────────────────────────────────────────


def test_default_off_is_byte_identical(monkeypatch):
    blocking = _name_blocking()
    out = _diversify_unused_orthogonal_blocking(blocking, _person_profiles(), None)
    assert out is blocking  # unchanged object, no passes added


def test_on_discovers_orthogonal_anchor_by_shape(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    out = _diversify_unused_orthogonal_blocking(_name_blocking(), _person_profiles(), None)
    added = {f for p in out.passes for f in p.fields} - {
        "full_name", "first_and_surname"
    }
    # birth_place (the win) is discovered WITHOUT being named -- purely by shape,
    # alongside the other moderate-card well-populated unused fields.
    assert "birth_place" in added
    assert "first_name" in added
    assert "surname" in added
    # near-unique / near-constant / too-sparse are excluded.
    assert "record_id" not in added   # card 1.0
    assert "gender" not in added      # card 0.002
    assert "occupation" not in added  # null 0.49


def test_added_passes_are_additive(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    out = _diversify_unused_orthogonal_blocking(_name_blocking(), _person_profiles(), None)
    for p in out.passes:
        if p.fields[0] in ("full_name", "first_and_surname"):
            assert p.additive is False  # primary passes untouched
        else:
            assert p.additive is True   # orthogonal anchors stay EM-trained


def test_already_covered_field_not_readded(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    # birth_place is ALREADY a blocking pass -> the rule must not duplicate it.
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["full_name"], transforms=["soundex"]),
            BlockingKeyConfig(fields=["birth_place"], transforms=["substring:0:4"]),
        ],
    )
    out = _diversify_unused_orthogonal_blocking(blocking, _person_profiles(), None)
    bp_passes = [p for p in out.passes if p.fields == ["birth_place"]]
    assert len(bp_passes) == 1  # not re-added


def test_none_blocking_returns_none(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    assert _diversify_unused_orthogonal_blocking(None, _person_profiles(), None) is None


# ── EM-demotion decoupling (collect_blocking_fields for_em) ──────────────────


def test_collect_blocking_fields_for_em_keeps_additive():
    cfg = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["full_name"], transforms=["soundex"]),
            BlockingKeyConfig(fields=["birth_place"], transforms=["strip"], additive=True),
            BlockingKeyConfig(fields=["dob"], transforms=["substring:0:4"]),
        ],
    )
    # Full inventory (block-building) includes every field a pass keys on.
    assert set(collect_blocking_fields(cfg)) == {"full_name", "birth_place", "dob"}
    # EM demotion set excludes the additive-only anchor (it stays EM-trained).
    assert set(collect_blocking_fields(cfg, for_em=True)) == {"full_name", "dob"}


def test_field_in_additive_and_primary_still_demoted():
    cfg = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["x"], transforms=["strip"], additive=True),
            BlockingKeyConfig(fields=["x", "y"], transforms=["soundex"]),
        ],
    )
    # x also keys a PRIMARY pass -> still demoted (agrees in that block).
    assert set(collect_blocking_fields(cfg, for_em=True)) == {"x", "y"}


def test_no_additive_pass_for_em_is_byte_identical():
    cfg = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["a"], transforms=[]),
            BlockingKeyConfig(fields=["b"], transforms=["soundex"]),
        ],
    )
    assert collect_blocking_fields(cfg) == collect_blocking_fields(cfg, for_em=True)


# ── end-to-end through the auto-config entry point ───────────────────────────


def _person_table(n=200):
    import random

    rng = random.Random(7)
    places = ["London", "Paris", "Berlin", "Rome", "Madrid", "Vienna", "Oslo"]
    firsts = ["john", "jane", "alice", "bob", "carol", "dave", "erin"]
    lasts = ["smith", "jones", "brown", "clark", "davis", "evans"]
    cols = {"record_id": [], "first_name": [], "surname": [],
            "birth_place": [], "dob": []}
    for i in range(n):
        cols["record_id"].append(f"r{i:05d}")
        cols["first_name"].append(rng.choice(firsts))
        cols["surname"].append(rng.choice(lasts))
        cols["birth_place"].append(rng.choice(places))
        y = rng.randint(1950, 1999)
        cols["dob"].append(f"{y}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}")
    return pa.table(cols)


def test_end_to_end_off_has_no_additive_passes(monkeypatch):
    cfg = auto_configure_probabilistic_df(_person_table())
    passes = list(cfg.blocking.passes or []) or list(cfg.blocking.keys or [])
    assert all(not getattr(p, "additive", False) for p in passes)


def test_end_to_end_on_adds_additive_orthogonal_pass(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    cfg = auto_configure_probabilistic_df(_person_table())
    passes = list(cfg.blocking.passes or []) or list(cfg.blocking.keys or [])
    additive = [p for p in passes if getattr(p, "additive", False)]
    assert additive, "orthogonal rule should add >=1 additive pass on the person shape"
    # every additive pass is single-field and stays out of the EM demotion set
    em_fields = set(collect_blocking_fields(cfg.blocking, for_em=True))
    for p in additive:
        assert len(p.fields) == 1
        # a purely-additive field is not demoted
        if not any(
            (not getattr(q, "additive", False)) and p.fields[0] in q.fields
            for q in passes
        ):
            assert p.fields[0] not in em_fields
