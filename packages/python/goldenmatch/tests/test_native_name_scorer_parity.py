"""Parity for the weighted-BUCKET name scorers (native ids 15/16) vs the pure
Python plugin references `NameFreqWeightedJW` / `GivenNameAliasedJW`.

These two scorers were the last Python↔TS `scorer_kernels` asymmetry: WASM-backed
on TS (score-wasm ids 20/21 over `fs-core`) but pure-Python-plugin-only on Python.
This wave gives them a NATIVE bucket kernel too — the weighted `score_block_pairs`
/ `score_block_pairs_arrow` kernels intercept the new bucket ids 15/16 and dispatch
them through `fs-core`'s `name_freq_weighted_sim` / `given_name_aliased_sim` over
the process-global census/alias tables (`set_name_reference_data`) — so the metric
(`scorer_kernels`, read from the bucket `_NATIVE_SCORER_IDS`) can move them to
`shared`.

UNLIKE `score_one` ids 0..=14, ids 15/16 read host-installed reference tables
(census surname counts → IDF; given-name nickname classes). Without them the kernel
degrades a name field to plain Jaro-Winkler, so every test installs the tables first
(exactly as the fast-path guard `_ensure_name_tables_installed` does).

Parity posture: TOLERANCE-bounded, not bit-exact. The native base JW is rapidfuzz-rs
whereas the plugin's is rapidfuzz-py; under "Rust is the reference" the native result
is authoritative. Empirically the two agree to machine epsilon here (name_freq_weighted
is `jw * weight`, given_name_aliased is `jw` or `1.0` — no FS-style level banding that
could amplify a borderline JW delta), so a 1e-9 abs tolerance is comfortable.

The fs-core `name_freq_weighted_sim` ports only the STATIC-census branch (not the
#1207 per-dataset `tf_freqs` downweight), so this test drives the plugin's static
branch (no `tf_freqs` passed) — matching how the fast-path guard declines native for
a tf-carrying name field.
"""
from __future__ import annotations

import itertools

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.refdata.scorer import GivenNameAliasedJW, NameFreqWeightedJW

_TOL = 1e-9


def _install(n) -> bool:
    """Install census + alias tables, mirroring `_ensure_name_tables_installed`.
    Uses a latest-wins RwLock kernel-side, so test order is irrelevant."""
    from goldenmatch.refdata.given_names import export_alias_forms
    from goldenmatch.refdata.surnames import export_counts

    surname_counts = [(name, float(c)) for name, c in export_counts()]
    alias_forms = export_alias_forms()
    if not surname_counts and not alias_forms:
        return False
    n.set_name_reference_data(surname_counts, alias_forms)
    return True


def _capable(n) -> bool:
    return (
        n is not None
        and hasattr(n, "set_name_reference_data")
        and hasattr(n, "NATIVE_SUPPORTS_NAME_BUCKET_SCORERS")
        and hasattr(n, "score_block_pairs")
    )


def _native_pair(n, a: str, b: str, scorer_id: int) -> float | None:
    """One 2-row block, one name field, weight 1.0, threshold -1 so the pair
    always emits — the kernel's per-pair weighted score IS the raw sim."""
    out = n.score_block_pairs([0, 1], [2], [[a, b]], [scorer_id], [1.0], 1.0, -1.0, [])
    return out[0][2] if out else None


# Adversarial corpus: common vs rare surnames (IDF band edges), given-name nickname
# classes, OOV names, casing, punctuation, empties.
_SURNAMES = [
    "Smith", "Smyth", "Smithe", "Jones", "Williams", "Brown", "Nguyen",
    "Zzyzyx", "Aaronsonovich", "smith", "SMITH", "O'Brien", "", "  ",
]
_GIVENS = [
    "William", "Bill", "Will", "Billy", "Bob", "Robert", "Bobby", "Rob",
    "Kate", "Catherine", "Kathy", "Xavier", "Zelda", "zzz", "", "  ",
]


def test_name_freq_weighted_bucket_id15_matches_plugin():
    n = _native_loader.native_module()
    if not _capable(n):
        pytest.skip("native kernel lacks the name-bucket-scorer capability")
    if not _install(n):
        pytest.skip("census/alias refdata packs unavailable")
    ref = NameFreqWeightedJW()
    worst = 0.0
    for a, b in itertools.combinations_with_replacement(_SURNAMES, 2):
        got = _native_pair(n, a, b, 15)
        # Plugin STATIC-census branch (no tf_freqs) — what fs-core ports.
        exp = ref.score_pair(a, b)
        assert got == pytest.approx(exp, abs=_TOL), f"nfw {a!r} {b!r}: {got} vs {exp}"
        worst = max(worst, abs(got - exp))
    assert worst < _TOL


def test_given_name_aliased_bucket_id16_matches_plugin():
    n = _native_loader.native_module()
    if not _capable(n):
        pytest.skip("native kernel lacks the name-bucket-scorer capability")
    if not _install(n):
        pytest.skip("census/alias refdata packs unavailable")
    ref = GivenNameAliasedJW()
    for a, b in itertools.combinations_with_replacement(_GIVENS, 2):
        got = _native_pair(n, a, b, 16)
        exp = ref.score_pair(a, b)
        assert got == pytest.approx(exp, abs=_TOL), f"gna {a!r} {b!r}: {got} vs {exp}"


def test_name_scorer_paths_specific():
    n = _native_loader.native_module()
    if not _capable(n):
        pytest.skip("native kernel lacks the name-bucket-scorer capability")
    if not _install(n):
        pytest.skip("census/alias refdata packs unavailable")
    # given_name_aliased: nickname canonical equality -> 1.0; unrelated -> plain JW.
    assert _native_pair(n, "Bob", "Robert", 16) == pytest.approx(1.0, abs=_TOL)
    assert _native_pair(n, "William", "Bill", 16) == pytest.approx(1.0, abs=_TOL)
    assert _native_pair(n, "Kate", "Catherine", 16) == pytest.approx(1.0, abs=_TOL)
    assert _native_pair(n, "William", "Walter", 16) == pytest.approx(
        GivenNameAliasedJW().score_pair("William", "Walter"), abs=_TOL
    )
    # name_freq_weighted: the census downweight fires only in the borderline JW
    # band [0.70, 0.95) (an exact agreement returns raw JW = 1.0, unweighted). A
    # borderline COMMON-surname pair is pulled below its raw JW; the native score
    # matches the plugin's downweighted value.
    from rapidfuzz.distance import JaroWinkler

    ref = NameFreqWeightedJW()
    raw_jw = JaroWinkler.similarity("Smith", "Smyth")
    downweighted = _native_pair(n, "Smith", "Smyth", 15)
    assert downweighted < raw_jw - 1e-6  # common surname pulled down
    assert downweighted == pytest.approx(ref.score_pair("Smith", "Smyth"), abs=_TOL)
    # An OOV (rare) surname pair is NOT in the census, so no downweight -> raw JW.
    assert _native_pair(n, "Zzyzyx", "Zzyzyx", 15) == pytest.approx(1.0, abs=_TOL)


def test_name_scorer_bucket_multi_pair_block():
    """score_block_pairs over a multi-row block dispatching ids 15/16 == the
    per-pair plugin mirror on every emitted pair."""
    n = _native_loader.native_module()
    if not _capable(n):
        pytest.skip("native kernel lacks the name-bucket-scorer capability")
    if not _install(n):
        pytest.skip("census/alias refdata packs unavailable")
    values = ["William", "Bill", "Robert", "Bob", "Xavier"]
    row_ids = list(range(len(values)))
    for scorer_id, ref in ((15, NameFreqWeightedJW()), (16, GivenNameAliasedJW())):
        emitted = n.score_block_pairs(
            row_ids, [len(values)], [values], [scorer_id], [1.0], 1.0, -1.0, []
        )
        got = {(min(a, b), max(a, b)): s for a, b, s in emitted}
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                exp = ref.score_pair(values[i], values[j])
                assert got[(i, j)] == pytest.approx(exp, abs=_TOL), (
                    f"id{scorer_id} {values[i]!r} {values[j]!r}"
                )


def test_name_scorer_arrow_matches_vec_kernel():
    """score_block_pairs_arrow (the Arrow-buffer entry the pipeline actually
    calls) dispatches ids 15/16 identically to the Vec-based score_block_pairs."""
    pa = pytest.importorskip("pyarrow")
    n = _native_loader.native_module()
    if not _capable(n) or not hasattr(n, "score_block_pairs_arrow"):
        pytest.skip("native kernel lacks the arrow name-bucket path")
    if not _install(n):
        pytest.skip("census/alias refdata packs unavailable")
    values = ["William", "Bill", "Smith", "Smyth", "Zzyzyx"]
    row_ids = pa.array(list(range(len(values))), type=pa.int64())
    for scorer_id in (15, 16):
        vec = n.score_block_pairs(
            list(range(len(values))), [len(values)], [values], [scorer_id],
            [1.0], 1.0, -1.0, [],
        )
        arrow = n.score_block_pairs_arrow(
            row_ids, [pa.array(values)], [len(values)], [scorer_id],
            [1.0], 1.0, -1.0,
        )
        vec_map = {(a, b): s for a, b, s in vec}
        arrow_map = {(a, b): s for a, b, s in arrow}
        assert vec_map.keys() == arrow_map.keys()
        for k in vec_map:
            assert vec_map[k] == pytest.approx(arrow_map[k], abs=_TOL), f"id{scorer_id} {k}"
