"""Python parity test for the auto-config native kernels (Tasks C4-C6).

Structure
---------
1. ``test_planner_golden_vectors`` — load ``planner_vectors.json``, run each
   vector through ``native_module().autoconfig_decide_plan``, assert JSON
   output equals expected.

2. ``test_classifier_golden_vectors`` — same for ``classifier_vectors.json``
   with ``autoconfig_classify_columns`` on single-element lists.  Floats are
   compared within 1e-9 rather than ``==`` to be robust to floating-point
   representation of the same f64 values across serde and json.loads.

3. ``test_wiring_equivalence`` — for a few representative DataFrames, assert
   that ``profile_columns(df)`` returns identical ``ColumnProfile`` results
   with native OFF vs ON.  Proves the dispatch wiring in autoconfig.py
   (Task C6) doesn't alter behaviour.

Skip conditions
---------------
The whole module is skipped when:
  - the native ext is not built / importable (``native_available()`` is False), OR
  - the ext was built but is a stale wheel that predates the autoconfig shims
    (``hasattr(native_module(), "autoconfig_decide_plan")`` is False).

Both are expected on a Windows dev box that hasn't run ``build_native.py``
since the shims were added; the CI ``native`` lane (Linux) runs the full suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from goldenmatch.core._native_loader import native_available, native_module

# ── Skip guard ──────────────────────────────────────────────────────────────

def _autoconfig_native_available() -> bool:
    """True iff the native ext is built AND carries the autoconfig shims."""
    if not native_available():
        return False
    _nm = native_module()
    return _nm is not None and hasattr(_nm, "autoconfig_decide_plan")


pytestmark = pytest.mark.skipif(
    not _autoconfig_native_available(),
    reason=(
        "native ext not built or is a stale wheel without autoconfig_decide_plan; "
        "run scripts/build_native.py or rely on the CI native lane"
    ),
)

# ── Golden vector path ───────────────────────────────────────────────────────
# parents[0]=tests, parents[1]=goldenmatch pkg, parents[2]=python,
# parents[3]=packages → packages/rust/extensions/autoconfig-core/golden
GOLDEN_DIR = Path(__file__).parents[3] / "rust" / "extensions" / "autoconfig-core" / "golden"


# ── Test 1: planner golden vectors ───────────────────────────────────────────

def test_planner_golden_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every vector in planner_vectors.json must round-trip through the native
    planner shim and produce output == the oracle-generated expected dict."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    _nm = native_module()

    vectors_path = GOLDEN_DIR / "planner_vectors.json"
    assert vectors_path.exists(), f"golden vectors not found at {vectors_path}"
    vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
    assert len(vectors) >= 1, "planner_vectors.json is empty"

    for i, v in enumerate(vectors):
        got_str = _nm.autoconfig_decide_plan(json.dumps(v["input"]))  # type: ignore[union-attr]
        got = json.loads(got_str)
        assert got == v["expected"], (
            f"planner vector #{i} mismatch.\n"
            f"  input:    {v['input']}\n"
            f"  expected: {v['expected']}\n"
            f"  got:      {got}"
        )


# ── Test 2: classifier golden vectors ────────────────────────────────────────

def test_classifier_golden_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every vector in classifier_vectors.json must round-trip through the
    native classifier shim.  Floats are compared within 1e-9."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    _nm = native_module()

    vectors_path = GOLDEN_DIR / "classifier_vectors.json"
    assert vectors_path.exists(), f"golden vectors not found at {vectors_path}"
    vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
    assert len(vectors) >= 1, "classifier_vectors.json is empty"

    for i, v in enumerate(vectors):
        # The native shim takes a list; wrap the single input in a list.
        input_list = [v["input"]]
        got_str = _nm.autoconfig_classify_columns(json.dumps(input_list))  # type: ignore[union-attr]
        got_list = json.loads(got_str)
        assert len(got_list) == 1, f"vector #{i}: expected 1 output, got {len(got_list)}"
        got = got_list[0]
        expected = v["expected"]

        # Non-float fields: exact match.
        for key in ("name", "dtype", "col_type", "needs_llm_escalation"):
            if key in expected:
                assert got.get(key) == expected[key], (
                    f"classifier vector #{i} field '{key}': expected {expected[key]!r}, "
                    f"got {got.get(key)!r}.  Input: {v['input']['name']}"
                )

        # Float fields: within 1e-9.
        for key in ("confidence", "null_rate", "cardinality_ratio", "avg_len"):
            if key in expected:
                exp_val = float(expected[key])
                got_val = float(got.get(key, float("nan")))
                assert abs(got_val - exp_val) < 1e-9, (
                    f"classifier vector #{i} float field '{key}': "
                    f"expected {exp_val}, got {got_val}.  Input: {v['input']['name']}"
                )


# ── Test 3: wiring equivalence ───────────────────────────────────────────────

def _make_person_df() -> pl.DataFrame:
    """Small person-shaped DataFrame covering the main col_types."""
    return pl.DataFrame({
        "first_name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
        "last_name": ["Smith", "Jones", "Brown", "Davis", "Wilson"],
        "email": ["a@b.com", "b@c.com", "c@d.com", "d@e.com", "e@f.com"],
        "zip_code": ["10001", "10002", "10003", "10004", "10005"],
        "birth_year": [1980, 1985, 1990, 1975, 1995],
    })


def _make_generic_df() -> pl.DataFrame:
    """DataFrame with mixed types exercising the data-profiling branches."""
    return pl.DataFrame({
        "record_id": ["A001", "B002", "C003", "D004", "E005"],
        "description": [
            "This is a long description of the item that exceeds fifty chars in length.",
            "Another long description here that also exceeds the fifty char threshold.",
            "Yet another lengthy description for testing the description branch here.",
            "And one more lengthy description to ensure the branch fires correctly.",
            "Final long description for this test dataset's description column here.",
        ],
        "city": ["New York", "Chicago", "Houston", "Phoenix", "Seattle"],
    })


def _profiles_as_comparable(profiles: list) -> list[dict]:
    """Convert ColumnProfile list to a list of dicts for comparison.

    Excludes ``sample_values`` because the native path truncates to 5
    (matching the Python path) but the VALUES may be ordered differently
    depending on which side did the list comprehension.  The functional
    parity test is col_type / confidence / null_rate / cardinality_ratio /
    avg_len — the metrics that drive matchkey and blocking decisions.
    """
    return [
        {
            "name": p.name,
            "dtype": p.dtype,
            "col_type": p.col_type,
            "confidence": p.confidence,
            "null_rate": p.null_rate,
            "cardinality_ratio": p.cardinality_ratio,
            "avg_len": p.avg_len,
        }
        for p in profiles
    ]


@pytest.mark.parametrize("df_factory", [_make_person_df, _make_generic_df])
def test_wiring_equivalence(
    monkeypatch: pytest.MonkeyPatch,
    df_factory,
) -> None:
    """profile_columns(df) must return the same ColumnProfiles regardless of
    whether native is OFF (pure Python) or ON (native classify)."""
    from goldenmatch.core.autoconfig import profile_columns

    df = df_factory()

    # Pure-Python path
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    profiles_py = _profiles_as_comparable(profile_columns(df))

    # Native path
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    profiles_native = _profiles_as_comparable(profile_columns(df))

    assert len(profiles_py) == len(profiles_native), (
        f"profile count mismatch: Python={len(profiles_py)}, native={len(profiles_native)}"
    )
    for i, (py, nat) in enumerate(zip(profiles_py, profiles_native)):
        assert py["name"] == nat["name"], f"col #{i}: name mismatch"
        assert py["col_type"] == nat["col_type"], (
            f"col '{py['name']}': col_type mismatch: Python={py['col_type']!r}, "
            f"native={nat['col_type']!r}"
        )
        assert abs(py["confidence"] - nat["confidence"]) < 1e-9, (
            f"col '{py['name']}': confidence mismatch: "
            f"Python={py['confidence']}, native={nat['confidence']}"
        )
        # null_rate, cardinality_ratio, avg_len pass through unchanged from
        # the Polars stats — they must be identical.
        for key in ("null_rate", "cardinality_ratio", "avg_len"):
            assert abs(py[key] - nat[key]) < 1e-12, (
                f"col '{py['name']}': {key} mismatch: "
                f"Python={py[key]}, native={nat[key]}"
            )
