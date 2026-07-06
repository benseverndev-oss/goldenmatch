"""Parity: the native ``detect_domain`` kernel must produce byte-identical output to
the pure-Python reference (``infermap.detect._detect_core_pure``). This is the gate
that lets it sit in ``_native_loader._GATED_ON`` (run under ``INFERMAP_NATIVE=auto``).

Skips cleanly when the native extension isn't built (pure-Python-only env). The CI
``infermap_native`` lane builds the wheel and runs this un-skipped under
``INFERMAP_NATIVE=1``.

Coverage: confident, tie, 3-way score tie (stable-sort host order), below-min-score,
empty columns -> no_data, all-hint-less -> no_data, multi-token hint, hint longer than
column. ASCII-only fixtures -- the ``str.lower()`` / ``\\s`` Unicode divergence is the
documented parity edge (design spec §6), out of scope here.
"""
from __future__ import annotations

import pytest
from infermap._native_loader import native_available, native_module
from infermap.detect import _detect_core_pure

native_only = pytest.mark.skipif(
    not native_available(), reason="infermap native extension not built"
)

# (columns, domains, min_score)
_CASES = [
    (["provider_npi", "first_name"], [("health", ["provider npi"]), ("fin", ["iban"])], 0.3),
    (["a", "b"], [("x", ["a"]), ("y", ["b"])], 0.3),  # tie
    (["a", "b"], [("x", ["a"]), ("y", ["b"]), ("z", ["a"])], 0.3),  # 3-way tie, host order
    (["a", "b", "c", "d"], [("h", ["a"])], 0.3),  # below_min_score (0.25)
    ([], [("h", ["x"])], 0.3),  # no_data (empty columns)
    (["a"], [("h", [])], 0.3),  # no_data (all hint-less)
    (["patient_id", "provider_npi", "dob"], [("health", ["patient id", "npi"]), ("fin", ["iban"])], 0.3),
    (["a"], [("h", ["a b c"])], 0.3),  # hint longer than column
    (["ORDER_ID", "Sku"], [("ecom", ["order id", "sku"])], 0.3),  # ASCII case-insensitivity
]


@native_only
@pytest.mark.parametrize("columns,domains,min_score", _CASES)
def test_detect_parity(columns, domains, min_score):
    native = tuple(native_module().detect_domain(columns, domains, min_score))
    assert native == _detect_core_pure(columns, domains, min_score)


def test_pure_stands_alone_without_wheel():
    """Box-runnable: the pure reference works regardless of the native ext."""
    assert _detect_core_pure(["a", "b"], [("x", ["a"])], 0.3) == ("x", 0.5, None, 0.0, "confident")


# ---------------------------------------------------------------------------
# Wave 2: name-scorer parity (exact / fuzzy_name / initialism)
# ---------------------------------------------------------------------------

from infermap.scorers.exact import _exact_score_pure  # noqa: E402
from infermap.scorers.fuzzy_name import _fuzzy_name_score_pure  # noqa: E402
from infermap.scorers.initialism import _score_pair  # noqa: E402

# ASCII name pairs (the Unicode-lower/\\s edge is the documented parity edge, spec §6.3).
_NAME_PAIRS = [
    ("City", "city"),
    ("provider_npi", "ProviderNPI"),
    ("first_name", "firstName"),
    ("assay_id", "ASSI"),
    ("confidence_score", "CONSC"),
    ("variant_id", "VARI"),
    ("order_id", "orderid"),
    ("abc", "xyz"),
    ("HTTPSConnection", "https_connection"),
    ("a", "a"),
    ("dob", "date_of_birth"),
    # tokenizer-boundary pairs (the providerIDs/URLs class a naive impl gets wrong)
    ("providerIDs", "provider_i_ds"),
    ("URLs", "ur_ls"),
    ("macOS", "mac_os"),
    ("iOS", "i_os"),
]


@native_only
@pytest.mark.parametrize("a,b", _NAME_PAIRS)
def test_exact_parity(a, b):
    assert native_module().exact_score(a, b) == _exact_score_pure(a, b)


@native_only
@pytest.mark.parametrize("a,b", _NAME_PAIRS)
def test_fuzzy_parity(a, b):
    # rapidfuzz-rs (score-core) vs Python rapidfuzz byte-equality re-validation (spec §6.1).
    assert native_module().fuzzy_name_score(a, b) == _fuzzy_name_score_pure(a, b)


@native_only
@pytest.mark.parametrize("a,b", _NAME_PAIRS)
def test_initialism_parity(a, b):
    # both None (abstain) or both the same graded float.
    assert native_module().initialism_score(a, b) == _score_pair(a, b)


# ---------------------------------------------------------------------------
# Wave 3: profile scorer parity (scalars-only kernel)
# ---------------------------------------------------------------------------

from infermap.scorers.profile import _profile_score_pure  # noqa: E402

# 10-tuple: (src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
#            src_val_count, tgt_val_count, src_avg_len, tgt_avg_len)
_PROFILE_CASES = [
    # identical profiles -> 1.0
    ("string", "string", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0),
    # dtype mismatch -> drops 0.4
    ("string", "int", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0),
    # avg_len floor: both 0.0 -> denom floors to 1.0
    ("string", "string", 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0),
    # one empty-sample side -> len_sim = 1 - 8/8 = 0.0
    ("string", "string", 0.0, 0.0, 0.5, 0.5, 100.0, 100.0, 0.0, 8.0),
    # cardinality floor: tiny cards (uniq*count < 1.0)
    ("string", "string", 0.0, 0.0, 0.01, 0.02, 10.0, 10.0, 4.0, 4.0),
    # lopsided null -> similarity clamps to 0.0
    ("string", "string", 0.0, 1.0, 0.5, 0.5, 100.0, 100.0, 8.0, 8.0),
    # lopsided uniqueness
    ("string", "string", 0.1, 0.1, 1.0, 0.0, 100.0, 100.0, 8.0, 8.0),
    # asymmetric lengths
    ("string", "string", 0.1, 0.1, 0.5, 0.5, 100.0, 100.0, 3.0, 30.0),
    # realistic mixed (non-round rates -> catches float-path rounding divergence)
    ("string", "int", 0.13, 0.87, 0.42, 0.58, 250.0, 90.0, 12.5, 7.25),
]


@native_only
@pytest.mark.parametrize("args", _PROFILE_CASES)
def test_profile_parity(args):
    # exact byte-equality (not approx) -- the whole point of the gate.
    assert native_module().profile_score(*args) == _profile_score_pure(*args)
# ---------------------------------------------------------------------------
# Wave 4: pattern_type scorer parity (regex fixture-drift gate)
# ---------------------------------------------------------------------------

import json  # noqa: E402
import pathlib  # noqa: E402

from infermap.scorers.pattern_type import _match_types_pure  # noqa: E402

_CORPUS_PATH = pathlib.Path(__file__).parent / "pattern_type_corpus.jsonl"


def _load_corpus() -> list[dict]:
    rows: list[dict] = []
    for line in _CORPUS_PATH.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _corpus_must() -> list[str]:
    return [r["s"] for r in _load_corpus() if r.get("tier", "must") == "must"]


def _corpus_informational() -> list[str]:
    return [r["s"] for r in _load_corpus() if r.get("tier") == "informational"]


@native_only
@pytest.mark.parametrize("s", _corpus_must())
def test_pattern_type_parity(s):
    # exact bitmask byte-equality across the ASCII must-pass contract.
    assert native_module().pattern_match_types([s]) == [_match_types_pure(s)]


@native_only
def test_pattern_type_unicode_edge_recorded():
    """Documented parity edge (\\d/\\s Unicode tables): RECORD divergence, do not gate.

    Prints an AGREE/DIVERGE line per informational fixture so CI logs pin where the
    boundary actually falls. Intentionally asserts nothing about agreement.
    """
    for s in _corpus_informational():
        native = native_module().pattern_match_types([s])[0]
        pure = _match_types_pure(s)
        verdict = "AGREE" if native == pure else "DIVERGE"
        print(f"[pattern_type edge] {verdict} native={native:#010b} "
              f"pure={pure:#010b} {s!r}")
