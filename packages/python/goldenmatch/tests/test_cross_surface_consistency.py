"""Gates for values that MUST agree across surfaces but are hand-maintained in
several places -- the drift class the focused consistency audit surfaced.

1. BUCKET_HASH_SEED: the bucket scorer and the distributed record store hash
   `__block_key__ % n_buckets` with the same seed, so a record's bucket is
   identical across surfaces. It was a duplicated literal; now a single source in
   core._hashing, pinned here.
2. Native scorer-id maps: the integer ids in _NATIVE_SCORER_IDS (score_buckets,
   the score_block_pairs/score_one namespace 0-14 PLUS the name-scorer bucket ids
   15/16 that the bucket kernel routes to fs-core) and _NATIVE_FIELD_SCORER_IDS
   (scorer.py, the score_field_matrix namespace) are hand-maintained mirrors of
   the Rust score-core dispatch. Nothing asserted they match the kernel; a
   renumber would silently mis-dispatch. Pinned canonically + behaviorally.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.backends.score_buckets import _NATIVE_SCORER_IDS
from goldenmatch.core import _hashing
from goldenmatch.core.scorer import _NATIVE_FIELD_SCORER_IDS, _native_field_matrix, score_field


class TestBucketHashSeedSingleSource:
    def test_score_buckets_uses_the_hashing_seed_object(self):
        from goldenmatch.backends import score_buckets
        assert score_buckets.BUCKET_HASH_SEED is _hashing.BUCKET_HASH_SEED

    def test_record_store_uses_the_hashing_seed_object(self):
        pytest.importorskip("duckdb")  # record_store imports duckdb at module load
        from goldenmatch.distributed import record_store
        assert record_store.BUCKET_HASH_SEED is _hashing.BUCKET_HASH_SEED

    def test_seed_value_is_pinned(self):
        # A change here reshuffles every bucket assignment on BOTH surfaces.
        assert _hashing.BUCKET_HASH_SEED == 0xC2B5C0BBE7ED5E5D


class TestNativeScorerIdMaps:
    # The canonical Rust score-core `score_one` dispatch (lib.rs): the block-pair
    # (bucket) kernel. `score_field_matrix` shares ids 0-3 (it delegates to
    # score_one) but ids 4+ are a DIFFERENT namespace -- in the bucket map
    # 4=date/5=qgram/6=soundex_match; in the field-matrix map 4=soundex_match.
    # `soundex_match` therefore lives in BOTH maps at DIFFERENT ids (bucket 6,
    # field 4); the two are pinned separately so they can't silently collide.
    _SCORE_ONE_IDS = {"jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3, "date": 4, "qgram": 5, "soundex_match": 6, "initialism_match": 7, "alias_match": 8, "dice": 9, "jaccard": 10, "phash": 11, "ensemble": 12, "radial": 13, "audio_fp": 14}
    # Name-scorer bucket ids EXTEND the bucket namespace beyond score_one (0-14):
    # the weighted bucket kernel (score_block_pairs/_arrow) intercepts 15/16 and
    # dispatches them to fs-core's name_freq_weighted_sim/given_name_aliased_sim
    # over the injected census/alias tables -- they are NOT score_one arms. Pinned
    # here so a renumber of either the Python map or the Rust NB_* consts fails.
    _NAME_BUCKET_IDS = {"name_freq_weighted_jw": 15, "given_name_aliased_jw": 16}
    # FS domain comparators (spec 2026-07-23, Phase 3): REAL score_one arms at
    # ids 17/18 (magnitude-aware date-diff / great-circle haversine), sitting
    # ABOVE the name-scorer 15/16 gap (15/16 are NOT score_one arms -- they're
    # intercepted by the bucket kernel). Pinned so a renumber of the Python map or
    # the Rust score_one match fails. `numeric_diff` is intentionally absent (its
    # band rides the scorer string, which the fixed-id score_one can't carry).
    _COMPARATOR_BUCKET_IDS = {"date_diff": 17, "geo_haversine": 18}
    _FIELD_MATRIX_IDS = {"jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3, "soundex_match": 4}

    def test_native_scorer_ids_match_score_one_ordering(self):
        # _NATIVE_SCORER_IDS = the score_one namespace (0-14) PLUS the two
        # name-scorer bucket ids (15/16, kernel-intercepted) PLUS the two FS domain
        # comparator score_one arms (17/18).
        assert _NATIVE_SCORER_IDS == {
            **self._SCORE_ONE_IDS,
            **self._NAME_BUCKET_IDS,
            **self._COMPARATOR_BUCKET_IDS,
        }

    def test_native_field_scorer_ids_match_score_field_matrix_ordering(self):
        assert _NATIVE_FIELD_SCORER_IDS == self._FIELD_MATRIX_IDS

    def test_shared_ids_agree_across_the_two_namespaces(self):
        # 0-3 are delegated by score_field_matrix to score_one, so they MUST be
        # identical in both Python dicts; ids 4+ diverge by namespace (bucket
        # date/qgram/soundex vs field-matrix soundex).
        for name in ("jaro_winkler", "levenshtein", "token_sort", "exact"):
            assert _NATIVE_SCORER_IDS[name] == _NATIVE_FIELD_SCORER_IDS[name]

    def test_field_matrix_ids_bind_to_the_right_scorer_in_the_kernel(self):
        """Behavioral: for each (name, id), the native score_field_matrix kernel
        dispatched by that id must produce the SAME score as the pure-Python
        score_field(name) -- proving the Python id->scorer binding agrees with the
        Rust arms. Catches a renumber of either the dict OR the kernel."""
        a, b = "smith", "smyth"  # alpha: valid for soundex + distinct across scorers
        # Skip unless the native field-matrix path is actually active (needs the
        # kernel built AND GOLDENMATCH_NATIVE != 0); _native_field_matrix returns
        # None otherwise. This is a native<->python parity check, not a "is native
        # on" check, so it only runs where native runs (the CI `native` lane).
        if _native_field_matrix([a, b], "jaro_winkler") is None:
            pytest.skip("native field-matrix path not active (unbuilt or GOLDENMATCH_NATIVE=0)")
        for name in _NATIVE_FIELD_SCORER_IDS:
            m = _native_field_matrix([a, b], name)  # routes name -> id -> kernel
            assert m is not None, f"native path unexpectedly declined for {name}"
            py = score_field(a, b, name)
            assert py is not None
            assert np.isclose(m[0, 1], py, atol=1e-6), (
                f"native id {_NATIVE_FIELD_SCORER_IDS[name]} for {name!r} scored "
                f"{m[0, 1]} but pure-Python score_field gave {py} -- id<->scorer drift"
            )
