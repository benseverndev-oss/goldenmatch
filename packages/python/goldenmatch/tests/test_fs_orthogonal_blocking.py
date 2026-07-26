"""FS field-agnostic orthogonal-anchor blocking (GOLDENMATCH_FS_ORTHOGONAL_BLOCKING).

`_diversify_probabilistic_blocking` (v2 lever #3) only diversifies onto `date` +
`zip`/`identifier`/`phone` col_types. The strongest orthogonal anchor a dataset
carries may be classified as something else -- on historical_50k `birth_place`
profiles as `name` (null 0.13, card 0.48), slips past that whitelist, and the FS
candidate set stays gated on the corrupted name keys (blocking_recall caps ~0.78).

`_diversify_unused_orthogonal_blocking` selects by DATA SHAPE (coverage +
cardinality band), not col_type, and marks its passes `additive=True` so the field
stays EM-trained (co-locate WITHOUT demoting the discriminator -- demoting it costs
F1, demoting a strong name field collapses recall).

**Gated `GOLDENMATCH_FS_ORTHOGONAL_BLOCKING=auto` (default):** fires ONLY on
person-shaped data (a `name` + a `date` col_type) -- the regime where the lever
generalises out-of-panel (historical_50k B3 0.808->0.873, febrl4 holdout +0.006) --
and is a NO-OP on bibliographic/product data (dblp_scholar -0.24, no name+date).
`=1` forces everywhere, `=0` disables.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _dataset_is_person_shaped,
    _diversify_unused_orthogonal_blocking,
    _fs_orthogonal_blocking_mode,
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
    """historical_50k shape: name fields + a `date` (dob) -> person-shaped."""
    return [
        _p("record_id", "identifier", card=1.0),      # near-unique surrogate
        _p("full_name", "name", card=0.96),            # primary block key
        _p("first_and_surname", "name", card=0.95),    # primary block key
        _p("first_name", "name", card=0.42),           # moderate, unused
        _p("surname", "name", card=0.74, null=0.10),   # moderate, unused
        _p("birth_place", "name", card=0.48, null=0.13),  # THE orthogonal anchor
        _p("dob", "date", card=0.58, null=0.22),       # the person temporal anchor
        _p("gender", "name", card=0.002),              # near-constant
        _p("occupation", "name", card=0.14, null=0.49),  # too sparse
    ]


def _biblio_profiles():
    """dblp_scholar shape: NO `name`, NO `date` col_type -> not person-shaped."""
    return [
        _p("record_id", "identifier", card=1.0),
        _p("title", "description", card=0.99),
        _p("authors", "string", card=0.93),
        _p("venue", "string", card=0.43),   # moderate-card, but a TOPIC bucket
        _p("year", "year", card=0.04),
    ]


# ── person-shape detector + mode resolver ────────────────────────────────────


def test_person_shape_detector():
    assert _dataset_is_person_shaped(_person_profiles()) is True
    assert _dataset_is_person_shaped(_biblio_profiles()) is False
    # name WITHOUT a date is not enough (conservative — errs toward not firing)
    assert _dataset_is_person_shaped([_p("full_name", "name"), _p("zip", "zip")]) is False
    # date WITHOUT a name is not enough
    assert _dataset_is_person_shaped([_p("dob", "date"), _p("amount", "numeric")]) is False
    # multi_name composite counts as a name field
    assert _dataset_is_person_shaped([_p("full", "multi_name"), _p("dob", "date")]) is True


def test_mode_resolver(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert _fs_orthogonal_blocking_mode() == "auto"
    for v in ("1", "true", "on", "yes", "enabled"):
        monkeypatch.setenv(FLAG, v)
        assert _fs_orthogonal_blocking_mode() == "on"
    for v in ("0", "false", "off", "no", "disabled"):
        monkeypatch.setenv(FLAG, v)
        assert _fs_orthogonal_blocking_mode() == "off"
    monkeypatch.setenv(FLAG, "garbage")
    assert _fs_orthogonal_blocking_mode() == "auto"


# ── gate behavior (auto / on / off) ──────────────────────────────────────────


def test_auto_fires_on_person(monkeypatch):
    # default (auto), no flag: a person-shaped profile set fires.
    out = _diversify_unused_orthogonal_blocking(_name_blocking(), _person_profiles(), None)
    added = {f for p in out.passes for f in p.fields} - {"full_name", "first_and_surname"}
    assert "birth_place" in added  # discovered by shape, never named
    assert "first_name" in added and "surname" in added
    assert "record_id" not in added and "gender" not in added and "occupation" not in added


def test_auto_is_noop_on_nonperson(monkeypatch):
    # default (auto): bibliographic shape (no name+date) -> unchanged, byte-identical.
    blocking = _name_blocking()
    out = _diversify_unused_orthogonal_blocking(blocking, _biblio_profiles(), None)
    assert out is blocking


def test_off_never_fires(monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    blocking = _name_blocking()
    assert _diversify_unused_orthogonal_blocking(blocking, _person_profiles(), None) is blocking


def test_on_forces_even_on_nonperson(monkeypatch):
    # force on: fires regardless of shape (a person dataset a classifier misjudged).
    monkeypatch.setenv(FLAG, "1")
    out = _diversify_unused_orthogonal_blocking(_name_blocking(), _biblio_profiles(), None)
    added = {f for p in out.passes for f in p.fields} - {"full_name", "first_and_surname"}
    assert "venue" in added  # forced despite being a topic bucket


def test_added_passes_are_additive(monkeypatch):
    out = _diversify_unused_orthogonal_blocking(_name_blocking(), _person_profiles(), None)
    for p in out.passes:
        if p.fields[0] in ("full_name", "first_and_surname"):
            assert p.additive is False  # primary passes untouched
        else:
            assert p.additive is True   # orthogonal anchors stay EM-trained


def test_already_covered_field_not_readded(monkeypatch):
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


def _product_table(n=200):
    """No name, no date -> not person-shaped."""
    import random

    rng = random.Random(3)
    brands = ["acme", "globex", "initech", "umbrella", "hooli"]
    cols = {"record_id": [], "title": [], "manufacturer": [], "price": []}
    for i in range(n):
        cols["record_id"].append(f"p{i:05d}")
        cols["title"].append(f"widget {rng.randint(1, 999)} {rng.choice(brands)}")
        cols["manufacturer"].append(rng.choice(brands))
        cols["price"].append(f"{rng.randint(1, 500)}.99")
    return pa.table(cols)


def test_end_to_end_auto_person_adds_additive(monkeypatch):
    # default (auto), no flag: a person table auto-enables the lever.
    cfg = auto_configure_probabilistic_df(_person_table())
    passes = list(cfg.blocking.passes or []) or list(cfg.blocking.keys or [])
    additive = [p for p in passes if getattr(p, "additive", False)]
    assert additive, "auto gate should fire on person-shaped data"
    em_fields = set(collect_blocking_fields(cfg.blocking, for_em=True))
    for p in additive:
        assert len(p.fields) == 1
        if not any(
            (not getattr(q, "additive", False)) and p.fields[0] in q.fields
            for q in passes
        ):
            assert p.fields[0] not in em_fields  # purely-additive field stays EM-trained


def test_end_to_end_off_disables_on_person(monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    cfg = auto_configure_probabilistic_df(_person_table())
    passes = list(cfg.blocking.passes or []) or list(cfg.blocking.keys or [])
    assert all(not getattr(p, "additive", False) for p in passes)


def test_end_to_end_auto_noop_on_product(monkeypatch):
    # default (auto): a product table (no name+date) gets no additive passes.
    cfg = auto_configure_probabilistic_df(_product_table())
    passes = list(cfg.blocking.passes or []) or list(cfg.blocking.keys or [])
    assert all(not getattr(p, "additive", False) for p in passes)
