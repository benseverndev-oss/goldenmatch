"""Property-based invariant tests (hypothesis).

Each test generates adversarial inputs and asserts an algebraic
invariant. Doubles as the project's fuzzing surface (OpenSSF
Scorecard Fuzzing check) -- keep the static `from hypothesis import`
lines: the detector greps for them.

Coverage:
    - Scorer bounds, symmetry, identity, None propagation
    - Standardizer idempotence + None handling
    - Fingerprint determinism, key-order invariance, dunder exclusion, py parity
    - safe_path NUL rejection and containment guarantee
    - sanitize_for_log output safety (no newlines, no control chars, length cap)

Exclusions (documented empirically):
    - dice/jaccard scorers expect same-length hex-encoded bloom filter strings
      (the well-formed PPRL shape -- bloom encodings are fixed-length per config).
      Hypothesis (#778) originally found that _dice_score_single /
      _jaccard_score_single raised a numpy broadcast ValueError on DIFFERENT-length
      inputs (e.g. '0000' vs '000000'):
          Falsifying example: a='0000', b='000000'
          ValueError: operands could not be broadcast together with shapes (2,) (3,)
      FIXED in #784: the single-pair helpers now zero-pad the shorter filter to
      max_len, mirroring the matrix variants, so single-vs-matrix parity holds on
      the same inputs. The bounds/symmetry tests still use a same-length strategy
      to verify the invariant on the well-formed shape;
      test_dice_mismatched_length_bug / test_jaccard_mismatched_length_bug are the
      regression tests pinning the no-crash behaviour.
    - soundex_match is excluded from bounds-with-arbitrary-text because
      jellyfish.soundex is undefined for some surrogate-pair / very unusual
      codepoint sequences in older builds; we use a restricted printable-ASCII
      strategy for it. The rapidfuzz-backed scorers (exact, jaro_winkler,
      levenshtein, token_sort, qgram) accept full printable-unicode text.
    - std_address and std_name_proper are INCLUDED in idempotence -- empirical
      check confirms both are idempotent over their defined output space.
    - NaN floats are excluded from fingerprint dict values because
      _value_bytes raises ValueError for non-finite floats (by spec).
"""

import tempfile
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Module-level settings profile (applied via decorator -- deadline=None avoids
# flaky per-example wall-clock failures on shared CI runners).
# ---------------------------------------------------------------------------

_SETTINGS = dict(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# General printable text (no surrogates, bounded length)
_text = st.text(max_size=64)
_printable_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po", "Pd"),
    ),
    max_size=64,
)
# Nonempty printable text (for identity tests where empty is trivially equal)
_nonempty_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs", "Po", "Pd"),
    ),
    min_size=1,
    max_size=64,
)

# Valid hex strings for bloom-filter scorers (dice/jaccard) -- same length.
# Same-length is the well-formed PPRL shape (bloom encodings are fixed-length
# per config), so bounds/symmetry/identity tests use it to verify the invariant.
# Mismatched-length inputs no longer crash (fixed in #784, single helpers
# zero-pad to max_len); that behaviour is pinned by
# test_dice_mismatched_length_bug / test_jaccard_mismatched_length_bug below.
_hex_byte_text = st.binary(min_size=1, max_size=32).map(lambda b: b.hex())
# Same-length bloom filter pairs
_hex_pair = st.integers(min_value=1, max_value=32).flatmap(
    lambda n: st.tuples(
        st.binary(min_size=n, max_size=n).map(lambda b: b.hex()),
        st.binary(min_size=n, max_size=n).map(lambda b: b.hex()),
    )
)

# Simple ASCII-printable for scorers sensitive to unicode shape
_ascii_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-",
    max_size=64,
)
_nonempty_ascii = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-",
    min_size=1,
    max_size=64,
)

# Dict strategy for fingerprint tests: str keys (no __ prefix, nonempty),
# values from the supported primitive types (no NaN floats -- by spec they raise).
# The alphabet is restricted to Lu/Ll/Nd (letters + digits), which excludes the
# Pc (connector punctuation) category that contains underscore -- so no generated
# key can start with "__". The .filter() is belt-and-braces in case the category
# exclusion ever changes.
_fp_key = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=16,
).filter(lambda k: not k.startswith("__"))  # belt-and-braces: alphabet excludes Pc (underscore)

_fp_value = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.text(max_size=32),
)

_fp_dict = st.dictionaries(_fp_key, _fp_value, min_size=0, max_size=8)

# ---------------------------------------------------------------------------
# Scorer: imports are deferred inside tests for two reasons:
#   1. xdist worker isolation -- each worker process must import independently
#      (global-state side effects like register_transform in process A are
#      invisible to process B).
#   2. Optional transitive deps (e.g. jellyfish for soundex_match) may not be
#      installed; deferring keeps collection clean and lets importorskip handle
#      missing deps at test time rather than at module load.
# ---------------------------------------------------------------------------

# soundex_match requires ASCII (jellyfish.soundex is undefined for some surrogate-
# pair / unusual codepoint sequences in older builds). The rapidfuzz-backed scorers
# accept full printable unicode -- use _printable_text for them.
_SOUNDEX_SCORERS = ["soundex_match"]
_RAPIDFUZZ_SCORERS = ["exact", "jaro_winkler", "levenshtein", "token_sort", "qgram"]
_SYMMETRIC_SCORERS = _RAPIDFUZZ_SCORERS + _SOUNDEX_SCORERS
_ALL_SCORERS_BOUNDS = _RAPIDFUZZ_SCORERS + _SOUNDEX_SCORERS

# Per-scorer text strategy: rapidfuzz handles full unicode; soundex needs ASCII.
_SCORER_TEXT_STRATEGY: dict = {
    "exact": _printable_text,
    "jaro_winkler": _printable_text,
    "levenshtein": _printable_text,
    "token_sort": _printable_text,
    "qgram": _printable_text,
    "soundex_match": _ascii_text,
}
_SCORER_NONEMPTY_STRATEGY: dict = {
    "exact": _nonempty_text,
    "jaro_winkler": _nonempty_text,
    "levenshtein": _nonempty_text,
    "token_sort": _nonempty_text,
    "qgram": _nonempty_text,
    "soundex_match": _nonempty_ascii,
}

# Standardizers that are idempotent by design (empirically verified above)
_IDEMPOTENT_STDS = [
    "std_email",
    "std_name_upper",
    "std_name_lower",
    "std_name_proper",
    "std_phone",
    "std_zip5",
    "std_state",
    "std_strip",
    "std_trim_whitespace",
    "std_address",
]


# ---------------------------------------------------------------------------
# Property 1: Scorer bounds
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(
    scorer=st.sampled_from(_ALL_SCORERS_BOUNDS),
    ab=st.data(),
)
def test_scorer_bounds(scorer: str, ab: st.DataObject) -> None:
    """score_field(a, b, scorer) is in [0.0, 1.0] for any non-None inputs.

    rapidfuzz-backed scorers use full printable-unicode text; soundex_match
    is restricted to ASCII (jellyfish.soundex limitation on unusual codepoints).
    """
    from goldenmatch.core.scorer import score_field

    text_strat = _SCORER_TEXT_STRATEGY[scorer]
    a = ab.draw(text_strat)
    b = ab.draw(text_strat)
    result = score_field(a, b, scorer)
    assert result is not None
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0, f"scorer={scorer!r} a={a!r} b={b!r} -> {result}"


@settings(**_SETTINGS)
@given(pair=_hex_pair)
def test_dice_bounds(pair: tuple) -> None:
    """dice on same-length hex-encoded bloom filters returns a float in [0.0, 1.0].

    NOTE: different-length inputs crash (known bug); tested separately below.
    """
    from goldenmatch.core.scorer import score_field

    a, b = pair
    result = score_field(a, b, "dice")
    assert result is not None
    assert 0.0 <= result <= 1.0, f"dice a={a!r} b={b!r} -> {result}"


@settings(**_SETTINGS)
@given(pair=_hex_pair)
def test_jaccard_bounds(pair: tuple) -> None:
    """jaccard on same-length hex-encoded bloom filters returns a float in [0.0, 1.0].

    NOTE: different-length inputs crash (known bug); tested separately below.
    """
    from goldenmatch.core.scorer import score_field

    a, b = pair
    result = score_field(a, b, "jaccard")
    assert result is not None
    assert 0.0 <= result <= 1.0, f"jaccard a={a!r} b={b!r} -> {result}"


def test_dice_mismatched_length_bug() -> None:
    """Different-length hex inputs should score, not crash (found by hypothesis).

    Regression for #784: the single-pair helper now zero-pads the shorter
    bloom filter to the longer length, matching ``_dice_score_matrix``.
    """
    from goldenmatch.core.scorer import score_field

    result = score_field("0000", "000000", "dice")
    assert result is not None and 0.0 <= result <= 1.0


def test_jaccard_mismatched_length_bug() -> None:
    """Different-length hex inputs should score, not crash (found by hypothesis).

    Regression for #784: the single-pair helper now zero-pads the shorter
    bloom filter to the longer length, matching ``_jaccard_score_matrix``.
    """
    from goldenmatch.core.scorer import score_field

    result = score_field("0000", "000000", "jaccard")
    assert result is not None and 0.0 <= result <= 1.0


@pytest.mark.parametrize(
    "a, b",
    [
        ("ff", "ff00"),       # 1 byte vs 2 bytes, set bits in the short one
        ("ff00", "ff"),       # reversed order (the shorter is the second arg)
        ("a1b2", "a1b200"),   # partial overlap, mismatched length
        ("0000", "000000"),   # all-zero, mismatched length (the #778 shrink)
    ],
)
def test_dice_jaccard_single_matches_matrix_on_mismatched_length(a: str, b: str) -> None:
    """#784: single-pair dice/jaccard agree with the matrix variant on
    different-length bloom filters (both zero-pad the shorter to max_len)."""
    from goldenmatch.core.scorer import (
        _dice_score_matrix,
        _jaccard_score_matrix,
        score_field,
    )

    dice_single = score_field(a, b, "dice")
    jaccard_single = score_field(a, b, "jaccard")
    dice_matrix = float(_dice_score_matrix([a, b])[0, 1])
    jaccard_matrix = float(_jaccard_score_matrix([a, b])[0, 1])

    assert dice_single == pytest.approx(dice_matrix, abs=1e-9)
    assert jaccard_single == pytest.approx(jaccard_matrix, abs=1e-9)


# ---------------------------------------------------------------------------
# Property 2: Scorer symmetry
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(
    scorer=st.sampled_from(_SYMMETRIC_SCORERS),
    ab=st.data(),
)
def test_scorer_symmetry(scorer: str, ab: st.DataObject) -> None:
    """score_field(a, b, s) == score_field(b, a, s) for all symmetric scorers.

    rapidfuzz-backed scorers use full printable-unicode text; soundex_match
    is restricted to ASCII (jellyfish.soundex limitation on unusual codepoints).
    """
    from goldenmatch.core.scorer import score_field

    text_strat = _SCORER_TEXT_STRATEGY[scorer]
    a = ab.draw(text_strat)
    b = ab.draw(text_strat)
    fwd = score_field(a, b, scorer)
    rev = score_field(b, a, scorer)
    assert fwd == pytest.approx(rev, abs=1e-9), (
        f"scorer={scorer!r} a={a!r} b={b!r}: fwd={fwd} rev={rev}"
    )


@settings(**_SETTINGS)
@given(pair=_hex_pair)
def test_dice_symmetry(pair: tuple) -> None:
    """dice is symmetric on same-length valid hex bloom filter strings."""
    from goldenmatch.core.scorer import score_field

    a, b = pair
    fwd = score_field(a, b, "dice")
    rev = score_field(b, a, "dice")
    assert fwd == pytest.approx(rev, abs=1e-9)


@settings(**_SETTINGS)
@given(pair=_hex_pair)
def test_jaccard_symmetry(pair: tuple) -> None:
    """jaccard is symmetric on same-length valid hex bloom filter strings."""
    from goldenmatch.core.scorer import score_field

    a, b = pair
    fwd = score_field(a, b, "jaccard")
    rev = score_field(b, a, "jaccard")
    assert fwd == pytest.approx(rev, abs=1e-9)


# ---------------------------------------------------------------------------
# Property 3: Scorer identity  score_field(a, a, s) == 1.0
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(
    scorer=st.sampled_from(_RAPIDFUZZ_SCORERS + _SOUNDEX_SCORERS),
    ab=st.data(),
)
def test_scorer_identity(scorer: str, ab: st.DataObject) -> None:
    """score_field(a, a, s) == 1.0 for non-empty a across string scorers.

    rapidfuzz-backed scorers use full printable-unicode text; soundex_match
    is restricted to ASCII (jellyfish.soundex limitation on unusual codepoints).
    """
    from goldenmatch.core.scorer import score_field

    nonempty_strat = _SCORER_NONEMPTY_STRATEGY[scorer]
    a = ab.draw(nonempty_strat)
    result = score_field(a, a, scorer)
    assert result is not None
    assert result == pytest.approx(1.0, abs=1e-9), (
        f"scorer={scorer!r} a={a!r} -> {result} (expected 1.0)"
    )


@settings(**_SETTINGS)
@given(a=_hex_byte_text)
def test_dice_identity(a: str) -> None:
    """dice(a, a) == 1.0 for any non-empty bloom filter with at least one set bit.

    Same-length inputs (a vs a) are trivially valid, so this avoids the known
    mismatched-length bug. An all-zero filter scores 0.0 by convention (no bits
    set means 0/0 = 0.0); we assert bounds regardless.
    """
    from goldenmatch.core.scorer import score_field

    result = score_field(a, a, "dice")
    assert result is not None
    assert 0.0 <= result <= 1.0
    import numpy as np
    bits = np.frombuffer(bytes.fromhex(a), dtype=np.uint8)
    if np.unpackbits(bits).sum() > 0:
        assert result == pytest.approx(1.0, abs=1e-9), (
            f"dice(a, a) for non-zero filter {a!r} -> {result}, expected 1.0"
        )


# ---------------------------------------------------------------------------
# Property 4: None propagation
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(
    scorer=st.sampled_from(_ALL_SCORERS_BOUNDS),
    xd=st.data(),
)
def test_scorer_none_propagation_left(scorer: str, xd: st.DataObject) -> None:
    """score_field(None, x, s) is None."""
    from goldenmatch.core.scorer import score_field

    x = xd.draw(_SCORER_TEXT_STRATEGY[scorer])
    assert score_field(None, x, scorer) is None


@settings(**_SETTINGS)
@given(
    scorer=st.sampled_from(_ALL_SCORERS_BOUNDS),
    xd=st.data(),
)
def test_scorer_none_propagation_right(scorer: str, xd: st.DataObject) -> None:
    """score_field(x, None, s) is None."""
    from goldenmatch.core.scorer import score_field

    x = xd.draw(_SCORER_TEXT_STRATEGY[scorer])
    assert score_field(x, None, scorer) is None


@settings(**_SETTINGS)
@given(scorer=st.sampled_from(_ALL_SCORERS_BOUNDS))
def test_scorer_both_none(scorer: str) -> None:
    """score_field(None, None, s) is None."""
    from goldenmatch.core.scorer import score_field

    assert score_field(None, None, scorer) is None


# ---------------------------------------------------------------------------
# Property 5: Standardizer idempotence
# ---------------------------------------------------------------------------

def _get_std_fn(name: str):
    """Return the standardizer function by name."""
    import goldenmatch.core.standardize as _std
    return getattr(_std, name)


@settings(**_SETTINGS)
@given(
    name=st.sampled_from(_IDEMPOTENT_STDS),
    value=_text,
)
def test_standardizer_idempotent(name: str, value: str) -> None:
    """f(f(x)) == f(x) for all idempotent standardizers on arbitrary str."""
    fn = _get_std_fn(name)
    first = fn(value)
    second = fn(first)
    assert second == first, (
        f"{name}({value!r}) = {first!r}, but {name}({first!r}) = {second!r}"
    )


@settings(**_SETTINGS)
@given(name=st.sampled_from(_IDEMPOTENT_STDS))
def test_standardizer_none_passthrough(name: str) -> None:
    """f(None) is None for all standardizers."""
    fn = _get_std_fn(name)
    assert fn(None) is None


# ---------------------------------------------------------------------------
# Property 6: Fingerprint determinism + key-order invariance + dunder + parity
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(d=_fp_dict)
def test_fingerprint_determinism(d: dict) -> None:
    """record_fingerprint(d) returns the same value on two calls."""
    from goldenmatch.core._hashing import record_fingerprint

    h1 = record_fingerprint(d)
    h2 = record_fingerprint(d)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


@settings(**_SETTINGS)
@given(d=_fp_dict)
def test_fingerprint_key_order_invariant(d: dict) -> None:
    """record_fingerprint is independent of key insertion order."""
    from goldenmatch.core._hashing import record_fingerprint

    d_rev = dict(reversed(list(d.items())))
    assert record_fingerprint(d) == record_fingerprint(d_rev)


@settings(**_SETTINGS)
@given(d=_fp_dict)
def test_fingerprint_dunder_exclusion(d: dict) -> None:
    """Adding __-prefixed keys does not change the fingerprint."""
    from goldenmatch.core._hashing import record_fingerprint

    d_with_dunder = {**d, "__row_id__": 42, "__source__": "test"}
    assert record_fingerprint(d) == record_fingerprint(d_with_dunder)


@settings(**_SETTINGS)
@given(d=_fp_dict)
def test_fingerprint_py_parity(d: dict) -> None:
    """record_fingerprint == _fingerprint_py (Python reference) for all inputs.

    When the native hashing kernel is active this is a cross-language parity
    fuzz; when it's inactive (GOLDENMATCH_NATIVE=0) this trivially confirms
    the two call the same code path.
    """
    from goldenmatch.core._hashing import _fingerprint_py, record_fingerprint

    assert record_fingerprint(d) == _fingerprint_py(d)


# ---------------------------------------------------------------------------
# Property 7: safe_path
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(
    pre=_ascii_text,
    post=_ascii_text,
)
def test_safe_path_nul_raises(pre: str, post: str) -> None:
    """safe_path raises ValueError if the path contains a NUL byte."""
    from goldenmatch.core._paths import safe_path

    path_with_nul = pre + "\x00" + post
    with pytest.raises(ValueError, match="NUL"):
        safe_path(path_with_nul)


@settings(**_SETTINGS)
@given(candidate=st.text(
    alphabet=st.characters(
        blacklist_characters="\x00",
        blacklist_categories=("Cs",),  # no surrogates
    ),
    max_size=48,
))
def test_safe_path_containment_or_error(candidate: str) -> None:
    """With a base_dir jail, safe_path either raises PathOutsideAllowedRootError
    or returns a path that is_relative_to the jail. No third outcome.

    Hypothesis generates adversarial path strings; the property verifies
    that the containment check is exhaustive.
    """
    from goldenmatch.core._paths import PathOutsideAllowedRootError, safe_path

    with tempfile.TemporaryDirectory() as td:
        jail = Path(td)
        # Build a candidate path under the jail so we exercise the common case
        # alongside traversal attempts. If candidate is empty or resolves to the
        # jail root itself, that's inside the jail and should succeed.
        try:
            candidate_path = str(jail / candidate)
        except (ValueError, OSError):
            # On Windows some characters are illegal in path components; skip.
            return

        try:
            result = safe_path(candidate_path, base_dir=jail)
            # Success path: result must be inside the jail
            assert result.is_relative_to(jail), (
                f"safe_path returned {result!r} which is outside jail {jail!r}"
            )
        except PathOutsideAllowedRootError:
            # Escape attempt caught -- correct behavior
            pass
        except ValueError as exc:
            # Only NUL-byte errors are expected ValueErrors from safe_path itself
            assert "NUL" in str(exc), f"Unexpected ValueError: {exc}"


# ---------------------------------------------------------------------------
# Property 8: sanitize_for_log
# ---------------------------------------------------------------------------

@settings(**_SETTINGS)
@given(value=_text)
def test_sanitize_no_newlines(value: str) -> None:
    """Output of sanitize_for_log contains no \\n or \\r."""
    from goldenmatch.core._logging import sanitize_for_log

    out = sanitize_for_log(value)
    assert "\n" not in out, f"\\n found in {out!r}"
    assert "\r" not in out, f"\\r found in {out!r}"


@settings(**_SETTINGS)
@given(value=_text)
def test_sanitize_no_control_chars(value: str) -> None:
    """Output of sanitize_for_log contains no C0/C1 control chars.

    Allowed: space (0x20), tab (0x09) -- tab is in the 0x09-0x1f gap but is
    whitelisted (0x09 is NOT in the _CONTROL_CHARS pattern). LF/CR are
    replaced to space before the regex fires, so they never appear as
    control characters in output.

    C0 control range (stripped by regex): 0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f
    C1 control range (stripped by regex): 0x7f-0x9f
    """
    from goldenmatch.core._logging import sanitize_for_log

    out = sanitize_for_log(value)
    for ch in out:
        cp = ord(ch)
        # The regex strips: 0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f, 0x7f-0x9f
        # It leaves tab (0x09), CR/LF are converted to space first.
        is_forbidden = (
            (0x00 <= cp <= 0x08)
            or cp in (0x0b, 0x0c)
            or (0x0e <= cp <= 0x1f)
            or (0x7F <= cp <= 0x9F)
        )
        assert not is_forbidden, (
            f"Control char U+{cp:04X} ({unicodedata.name(ch, '?')!r}) found in output "
            f"for input {value!r}"
        )


@settings(**_SETTINGS)
@given(value=_text)
def test_sanitize_max_length(value: str) -> None:
    """Output of sanitize_for_log is at most 1000 characters."""
    from goldenmatch.core._logging import sanitize_for_log

    out = sanitize_for_log(value)
    assert len(out) <= 1000


@settings(**_SETTINGS)
@given(obj=st.one_of(
    st.integers(),
    st.floats(allow_nan=True),
    st.booleans(),
    st.none(),
    st.binary(max_size=16),
    st.lists(st.integers(), max_size=8),
    st.dictionaries(st.text(max_size=8), st.integers(), max_size=4),
))
def test_sanitize_accepts_any_object(obj: object) -> None:
    """sanitize_for_log accepts any Python object and returns a str without raising."""
    from goldenmatch.core._logging import sanitize_for_log

    result = sanitize_for_log(obj)
    assert isinstance(result, str)
    assert len(result) <= 1000
