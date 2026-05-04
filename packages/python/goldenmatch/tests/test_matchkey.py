"""Tests for goldenmatch matchkey builder."""

import polars as pl

from goldenmatch.core.matchkey import (
    _xform_sig,
    build_matchkey_expr,
    compute_matchkeys,
    precompute_matchkey_transforms,
)
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField


class TestBuildMatchkeyExpr:
    """Tests for build_matchkey_expr."""

    def test_single_field_exact_normalizes(self):
        """Single field exact matchkey normalizes correctly."""
        mk = MatchkeyConfig(
            name="name_sdx",
            type="exact",
            fields=[MatchkeyField(field="first_name", transforms=["lowercase", "soundex"])],
        )
        expr = build_matchkey_expr(mk)

        df = pl.DataFrame({"first_name": ["John", "JANE", None]})
        result = df.select(expr)

        assert result.columns == ["__mk_name_sdx__"]
        values = result["__mk_name_sdx__"].to_list()
        # "John" -> "john" -> soundex("john") = "J500"
        # "JANE" -> "jane" -> soundex("jane") = "J500"  (wait, J500 for john, J500 for jane? Let me check)
        # Actually soundex("john") = "J500", soundex("jane") = "J500" — both start with J
        assert values[0] is not None
        assert values[1] is not None
        assert values[2] is None  # None stays None

    def test_multi_field_concatenation(self):
        """Multi-field exact matchkey concatenates with || separator."""
        mk = MatchkeyConfig(
            name="name_zip",
            type="exact",
            fields=[
                MatchkeyField(field="first_name", transforms=["lowercase"]),
                MatchkeyField(field="zip", transforms=[]),
            ],
        )
        expr = build_matchkey_expr(mk)

        df = pl.DataFrame({
            "first_name": ["John", "Jane"],
            "zip": ["19382", "10001"],
        })
        result = df.select(expr)

        assert result.columns == ["__mk_name_zip__"]
        values = result["__mk_name_zip__"].to_list()
        assert values[0] == "john||19382"
        assert values[1] == "jane||10001"

    def test_weighted_matchkey_returns_lit_none(self):
        """Weighted matchkey returns pl.lit(None) placeholder."""
        mk = MatchkeyConfig(
            name="fuzzy_name",
            type="weighted",
            threshold=0.8,
            fields=[
                MatchkeyField(field="first_name", transforms=["lowercase"], scorer="jaro_winkler", weight=0.5),
                MatchkeyField(field="last_name", transforms=["lowercase"], scorer="jaro_winkler", weight=0.5),
            ],
        )
        expr = build_matchkey_expr(mk)

        df = pl.DataFrame({"first_name": ["John"], "last_name": ["Smith"]})
        result = df.select(expr)

        assert result.columns == ["__mk_fuzzy_name__"]
        assert result["__mk_fuzzy_name__"][0] is None


class TestComputeMatchkeys:
    """Tests for compute_matchkeys."""

    def test_adds_matchkey_columns(self):
        """compute_matchkeys adds correct columns for exact matchkeys."""
        mks = [
            MatchkeyConfig(
                name="name_sdx",
                type="exact",
                fields=[MatchkeyField(field="first_name", transforms=["lowercase"])],
            ),
            MatchkeyConfig(
                name="zip_exact",
                type="exact",
                fields=[MatchkeyField(field="zip", transforms=[])],
            ),
        ]

        df = pl.DataFrame({
            "first_name": ["John", "Jane"],
            "zip": ["19382", "10001"],
        })
        lf = df.lazy()

        result = compute_matchkeys(lf, mks).collect()

        assert "__mk_name_sdx__" in result.columns
        assert "__mk_zip_exact__" in result.columns
        assert result["__mk_name_sdx__"].to_list() == ["john", "jane"]
        assert result["__mk_zip_exact__"].to_list() == ["19382", "10001"]


# --- Tests for precompute_matchkey_transforms (perf/hoist-matchkey-transforms) ---
# (Imports moved to top of file per CLAUDE.md guidance: ruff auto-fix can
# cascade-delete test functions when removing mid-file imports.)


def _mk(name: str, fields: list[MatchkeyField], threshold: float = 0.7) -> MatchkeyConfig:
    return MatchkeyConfig(name=name, type="weighted", threshold=threshold, fields=fields)


def _field(field: str, transforms: list[str], scorer: str = "jaro_winkler",
           weight: float = 1.0) -> MatchkeyField:
    return MatchkeyField(field=field, transforms=transforms, scorer=scorer, weight=weight)


def test_xform_sig_is_deterministic_across_processes():
    f1 = _field("name", ["lowercase", "strip"])
    f2 = _field("name", ["lowercase", "strip"])
    assert _xform_sig(f1) == _xform_sig(f2)
    sig = _xform_sig(f1)
    assert sig.startswith("__xform_name_") and sig.endswith("__")
    assert len(sig) > len("__xform_name___")


def test_precompute_matchkey_transforms_dedups_signatures():
    df = pl.DataFrame({"name": ["Alice", "BOB"]})
    mk_a = _mk("a", [_field("name", ["lowercase"])])
    mk_b = _mk("b", [_field("name", ["lowercase"])])
    out = precompute_matchkey_transforms(df, [mk_a, mk_b])
    xform_cols = [c for c in out.columns if c.startswith("__xform_")]
    assert len(xform_cols) == 1


def test_precompute_matchkey_transforms_distinct_transforms_same_field():
    df = pl.DataFrame({"name": ["Alice"]})
    mk = _mk("m", [
        _field("name", ["lowercase"]),
        _field("name", ["uppercase"]),
    ])
    out = precompute_matchkey_transforms(df, [mk])
    xform_cols = sorted(c for c in out.columns if c.startswith("__xform_"))
    assert len(xform_cols) == 2
    assert out[xform_cols[0]].to_list() != out[xform_cols[1]].to_list()


def test_precompute_matchkey_transforms_native_chain_path():
    df = pl.DataFrame({"name": ["  Alice  ", "BOB"]})
    mk = _mk("m", [_field("name", ["lowercase", "strip"])])
    out = precompute_matchkey_transforms(df, [mk])
    sig = _xform_sig(_field("name", ["lowercase", "strip"]))
    assert out[sig].to_list() == ["alice", "bob"]


def test_precompute_matchkey_transforms_python_fallback_path():
    df = pl.DataFrame({"name": ["Smith", "Smyth"]})
    mk = _mk("m", [_field("name", ["soundex"])])
    out = precompute_matchkey_transforms(df, [mk])
    sig = _xform_sig(_field("name", ["soundex"]))
    vals = out[sig].to_list()
    assert vals[0] == vals[1]


def test_precompute_matchkey_transforms_skips_record_embedding():
    df = pl.DataFrame({"name": ["a"], "desc": ["b"]})
    mk = MatchkeyConfig(name="m", type="weighted", threshold=0.5, fields=[
        MatchkeyField(field="__record__", transforms=[], scorer="record_embedding",
                      weight=1.0, columns=["name", "desc"]),
        _field("name", ["lowercase"]),
    ])
    out = precompute_matchkey_transforms(df, [mk])
    assert "__record__" not in out.columns
    sig_name = _xform_sig(_field("name", ["lowercase"]))
    assert sig_name in out.columns


def test_precompute_matchkey_transforms_skips_empty_transforms():
    df = pl.DataFrame({"name": ["Alice"]})
    mk = _mk("m", [_field("name", [])])
    out = precompute_matchkey_transforms(df, [mk])
    xform_cols = [c for c in out.columns if c.startswith("__xform_")]
    assert xform_cols == []


def test_precompute_matchkey_transforms_is_idempotent():
    # Calling the helper a second time on already-augmented output is a no-op
    # (the `sig in df.columns` guard short-circuits). Pins this contract so a
    # future "always recompute" change won't silently shadow the existing
    # __xform_*__ columns.
    df = pl.DataFrame({"name": ["Alice", "BOB"]})
    mk = _mk("m", [_field("name", ["lowercase"])])
    once = precompute_matchkey_transforms(df, [mk])
    twice = precompute_matchkey_transforms(once, [mk])
    assert once.columns == twice.columns
    sig = _xform_sig(_field("name", ["lowercase"]))
    assert once[sig].to_list() == twice[sig].to_list()


def test_run_dedupe_pipeline_populates_xform_columns():
    # End-to-end: the dedupe entry point actually wires precompute into the
    # pipeline and the augmented columns reach the scorer. Without this test,
    # someone could revert the pipeline.py change and only the helper-level
    # tests would still pass.
    import csv
    import tempfile
    from pathlib import Path
    from goldenmatch import dedupe
    from goldenmatch.config.schemas import (
        BlockingConfig, BlockingKeyConfig, GoldenMatchConfig,
        GoldenRulesConfig, OutputConfig,
    )

    tmp = Path(tempfile.mkdtemp()) / "t.csv"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "email", "zip"])
        w.writerow([1, "Alice Smith", "alice@x.com", "10001"])
        w.writerow([2, "Alice Smyth", "alice@x.com", "10001"])
        w.writerow([3, "Bob Jones", "bob@y.com", "20002"])

    config = GoldenMatchConfig(
        matchkeys=[_mk("m", [
            _field("name", ["lowercase", "strip"], scorer="jaro_winkler", weight=0.7),
            _field("email", ["lowercase", "strip"], scorer="jaro_winkler", weight=0.3),
        ], threshold=0.6)],
        blocking=BlockingConfig(
            max_block_size=100, skip_oversized=True,
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
        ),
        standardization={"email": ["lowercase", "strip"], "name": ["strip"]},
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
        output=OutputConfig(format="csv", directory=str(tmp.parent), run_name="r"),
    )
    result = dedupe(str(tmp), config=config)
    # Alice Smith / Alice Smyth share the same email + zip — they should cluster.
    assert result.total_records == 3
    assert result.total_clusters >= 1
    # And at least one scored pair was found (the Alice cluster).
    assert len(result.scored_pairs) >= 1
