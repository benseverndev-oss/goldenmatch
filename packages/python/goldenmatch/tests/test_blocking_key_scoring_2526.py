"""#2526: a field every candidate pair agrees on by construction carries no
signal, so auto-config must not score it.

Blocking makes its key constant inside each block. If that same column is also a
scored field, it contributes its full weight to every candidate pair as an OFFSET
with zero discriminative power -- it cannot separate a match from a non-match, it
only shifts the distribution up and flattens the threshold. Measured on DBLP-ACM:
18.6% of the weighted score, agreeing on 100% of candidates.

Two conditions gate the rule, and both have a test here because getting either
wrong silently changes behaviour on a benchmark:
  * every PASS must key on the field (multi_pass pairs can disagree otherwise)
  * every TRANSFORM must preserve equality (soundex blocks != equal values)
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig import (
    _always_agreeing_blocking_fields,
    _drop_uninformative_blocking_fields,
)


def _blocking(strategy, keys=(), passes=()):
    return BlockingConfig(strategy=strategy, keys=list(keys), passes=list(passes))


def _key(fields, transforms):
    return BlockingKeyConfig(fields=list(fields), transforms=list(transforms))


class TestAlwaysAgreeingFields:
    def test_static_key_with_normalising_transforms_is_constant(self):
        b = _blocking("static", keys=[_key(["__title_key__"], ["lowercase", "strip"])])
        assert _always_agreeing_blocking_fields(b) == {"__title_key__"}

    def test_lossy_transform_is_not_constant(self):
        # abt_buy's real shape: all passes key on `manufacturer`, but via soundex
        # and substring. Same block != same value ("Sony"/"Sonny" share a soundex),
        # so manufacturer still discriminates and must be kept.
        b = _blocking("multi_pass", passes=[
            _key(["manufacturer"], ["lowercase", "substring:0:5"]),
            _key(["manufacturer"], ["lowercase", "soundex"]),
            _key(["manufacturer"], ["lowercase", "token_sort", "substring:0:8"]),
        ])
        assert _always_agreeing_blocking_fields(b) == set()

    def test_unrecognised_transform_is_assumed_lossy(self):
        b = _blocking("static", keys=[_key(["x"], ["some_future_transform"])])
        assert _always_agreeing_blocking_fields(b) == set()

    def test_multi_pass_on_different_fields_intersects_to_nothing(self):
        b = _blocking("multi_pass", passes=[
            _key(["a"], ["lowercase"]), _key(["b"], ["lowercase"]),
        ])
        assert _always_agreeing_blocking_fields(b) == set()

    def test_field_in_every_pass_is_constant(self):
        b = _blocking("multi_pass", passes=[
            _key(["a", "b"], ["lowercase"]), _key(["a", "c"], ["strip"]),
        ])
        assert _always_agreeing_blocking_fields(b) == {"a"}

    def test_no_blocking_is_empty(self):
        assert _always_agreeing_blocking_fields(None) == set()

    def test_blocking_object_with_no_keys_or_passes_is_empty(self):
        # BlockingConfig itself rejects strategy='static' with no keys, so this
        # exercises the guard against any other blocking-ish object reaching here.
        class _Bare:
            keys = None
            passes = None
        assert _always_agreeing_blocking_fields(_Bare()) == set()


class TestDropUninformativeFields:
    def _mk(self, fields, threshold=0.7):
        return MatchkeyConfig(
            name="fuzzy_match", type="weighted", threshold=threshold,
            fields=[MatchkeyField(field=f, scorer=s, weight=w) for f, s, w in fields],
        )

    def test_drops_the_constant_field_and_rescales_the_threshold(self):
        # DBLP-ACM's shape: total weight 4.3, the constant is 0.8 of it.
        mk = self._mk([("title", "token_sort", 1.5), ("authors", "token_sort", 1.0),
                       ("venue", "ensemble", 1.0), ("__title_key__", "exact", 0.8)])
        b = _blocking("static", keys=[_key(["__title_key__"], ["lowercase"])])
        _drop_uninformative_blocking_fields([mk], b)
        assert [f.field for f in mk.fields] == ["title", "authors", "venue"]
        # Same absolute bar over the surviving weight: (0.7*4.3 - 0.8)/3.5.
        assert mk.threshold == round((0.7 * 4.3 - 0.8) / 3.5, 4)

    def test_rescale_keeps_the_same_pairs_above_the_bar(self):
        # The point of rescaling: a pair scoring exactly at the old boundary must
        # still sit exactly at the new one, so recall does not silently drop.
        mk = self._mk([("a", "exact", 1.0), ("k", "exact", 1.0)])
        b = _blocking("static", keys=[_key(["k"], ["lowercase"])])
        _drop_uninformative_blocking_fields([mk], b)
        # Old: (1.0*s_a + 1.0*1.0)/2 >= 0.7  <=>  s_a >= 0.4. New bar must be 0.4.
        assert mk.threshold == 0.4

    def test_leaves_the_matchkey_alone_when_the_rule_would_empty_it(self):
        mk = self._mk([("k", "exact", 1.0)])
        b = _blocking("static", keys=[_key(["k"], ["lowercase"])])
        _drop_uninformative_blocking_fields([mk], b)
        assert [f.field for f in mk.fields] == ["k"]  # a fieldless matchkey scores nothing

    def test_no_op_when_nothing_is_constant(self):
        mk = self._mk([("title", "token_sort", 1.5), ("authors", "token_sort", 1.0)])
        b = _blocking("static", keys=[_key(["__title_key__"], ["lowercase"])])
        _drop_uninformative_blocking_fields([mk], b)
        assert [f.field for f in mk.fields] == ["title", "authors"]
        assert mk.threshold == 0.7

    def test_exact_matchkeys_are_untouched(self):
        mk = MatchkeyConfig(name="e", type="exact",
                            fields=[MatchkeyField(field="k")])
        b = _blocking("static", keys=[_key(["k"], ["lowercase"])])
        _drop_uninformative_blocking_fields([mk], b)
        assert [f.field for f in mk.fields] == ["k"]
