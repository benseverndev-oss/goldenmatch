"""Stage E parity: the pipeline golden seam routes to the fused Arrow-native
golden kernel (default-on) and produces byte-identical golden output vs the
classic slow-path builder.

The config forces the survivorship SLOW path (a ``field_rules`` entry makes it
NOT fast-columnar-eligible) AND is golden_fused-COVERED (``most_complete`` is a
covered strategy), so the fused kernel actually fires -- confirmed via the
``golden_fused_used`` telemetry flag so a silent fused-decline can't pass the
parity test by accident.

The dataset is deliberately all-string so both paths agree on DTYPES: the
classic slow path assembles its golden frame from ``list[dict]`` records with an
all-``Utf8`` schema, while the fused kernel preserves the source column dtypes
(matching the fast columnar path's convention). For string columns those
coincide, so the parity check is clean values+dtypes. (For non-string golden
columns the two representations diverge by dtype -- a pre-existing classic
fast/slow inconsistency, flagged in the Stage E handoff, not introduced here.)
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenFieldRule,
    GoldenMatchConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.pipeline import run_dedupe, run_dedupe_df
from polars.testing import assert_frame_equal


def _kernel_present() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        return hasattr(native_module(), "golden_fused")
    except Exception:
        return False


requires_kernel = pytest.mark.skipif(
    not _kernel_present(),
    reason="golden_fused native kernel not built (build_native.py); CI builds it",
)


def _string_dupes_df(n_clusters: int = 15, members: int = 3) -> pl.DataFrame:
    """All-string personlike frame with ``n_clusters`` known clusters, each a
    triple sharing an email so the exact-email matchkey emits multi-member
    clusters deterministically."""
    rows: list[dict] = []
    for c in range(n_clusters):
        email = f"cluster{c}@example.com"
        for m in range(members):
            rows.append(
                {
                    "name": f"Person {c}" + ("." if m % 2 else ""),
                    "email": email,
                    "zip": f"100{c % 9:02d}",
                }
            )
    return pl.DataFrame(rows)


def _slow_path_config() -> GoldenMatchConfig:
    # field_rules -> NOT _polars_native_eligible (forces the survivorship slow
    # path), and most_complete is a golden_fused-covered strategy. quality
    # weighting off to keep the run goldencheck-independent + deterministic.
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="email_exact",
                type="exact",
                fields=[MatchkeyField(field="email", transforms=["lowercase"])],
            ),
        ],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"])],
            max_block_size=100,
            skip_oversized=False,
        ),
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            field_rules={"name": GoldenFieldRule(strategy="most_complete")},
            quality_weighting=False,
        ),
    )


@requires_kernel
def test_fused_golden_byte_identical_to_classic(monkeypatch):
    """GOLDENMATCH_GOLDEN_FUSED=1 (default) vs =0 (classic) produce byte-identical
    golden frames, and the =1 run actually used the fused kernel."""
    df = _string_dupes_df()
    cfg = _slow_path_config()

    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    fused = run_dedupe_df(df, cfg.model_copy(deep=True))
    # The whole point: fused was genuinely used (not a silent decline).
    assert fused["golden_fused_used"] is True

    monkeypatch.setenv("GOLDENMATCH_GOLDEN_FUSED", "0")
    classic = run_dedupe_df(df, cfg.model_copy(deep=True))
    assert classic["golden_fused_used"] is False

    assert fused["golden"] is not None
    assert classic["golden"] is not None
    a = fused["golden"].sort("__cluster_id__")
    b = classic["golden"].sort("__cluster_id__")
    assert_frame_equal(a, b, check_column_order=False, check_row_order=False)


def test_kill_switch_uses_classic(monkeypatch):
    """GOLDENMATCH_GOLDEN_FUSED=0 declines fused; golden still produced."""
    df = _string_dupes_df()
    cfg = _slow_path_config()
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_FUSED", "0")
    res = run_dedupe_df(df, cfg)
    assert res["golden_fused_used"] is False
    assert res["golden"] is not None and res["golden"].height > 0


def test_full_provenance_declines_fused(monkeypatch):
    """config.output.lineage_provenance=True -> fused declines (can't reproduce
    __survivorship_prov__), classic runs and still produces golden."""
    df = _string_dupes_df()
    cfg = _slow_path_config()
    cfg.output.lineage_provenance = True
    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    res = run_dedupe_df(df, cfg)
    assert res["golden_fused_used"] is False
    assert res["golden"] is not None and res["golden"].height > 0


def _write_nonstring_csv(path) -> None:
    """CSV with a NON-string golden column (`age` Int). The file path
    (scan_csv) infers native dtypes and does NOT pre-cast user columns to Utf8
    (unlike run_match's _cast_user_cols_to_str), so the classic slow golden path
    forces `age` to String while the fused kernel would preserve Int64 -- the
    exact byte-identity divergence the Utf8 cast in _try_fused_golden fixes."""
    lines = ["name,email,zip,age"]
    for c in range(15):
        email = f"cluster{c}@example.com"
        for m in range(3):
            name = f"Person {c}" + ("." if m % 2 else "")
            lines.append(f"{name},{email},100{c % 9:02d},{30 + c}")
    path.write_text("\n".join(lines) + "\n", encoding="utf8")


@requires_kernel
def test_file_path_nonstring_golden_byte_identical(tmp_path, monkeypatch):
    """The file entry point (run_dedupe / scan_csv native dtypes) with a
    non-string golden column: fused==classic on values AND dtypes (both Utf8
    after the cast fix), fused genuinely used. This is the case that hid the
    dtype divergence -- dedupe_df pre-casts, run_dedupe does not."""
    csv_path = tmp_path / "people.csv"
    _write_nonstring_csv(csv_path)
    cfg = _slow_path_config()

    monkeypatch.delenv("GOLDENMATCH_GOLDEN_FUSED", raising=False)
    fused = run_dedupe([(str(csv_path), "people")], cfg.model_copy(deep=True))
    assert fused["golden_fused_used"] is True

    monkeypatch.setenv("GOLDENMATCH_GOLDEN_FUSED", "0")
    classic = run_dedupe([(str(csv_path), "people")], cfg.model_copy(deep=True))
    assert classic["golden_fused_used"] is False

    assert fused["golden"] is not None and classic["golden"] is not None
    # The classic slow path emits `age` as Utf8; the fix makes fused match.
    assert classic["golden"].schema["age"] == pl.Utf8
    assert fused["golden"].schema["age"] == pl.Utf8
    a = fused["golden"].sort("__cluster_id__")
    b = classic["golden"].sort("__cluster_id__")
    assert_frame_equal(a, b, check_column_order=False, check_row_order=False)
