"""#1351 -- cardinality_ratio + null_rate must be measured on the FULL column,
not the 1,000-row classification sample.

Root cause: ``profile_columns`` sampled 1,000 rows and computed
``cardinality_ratio`` from that sample. A small sample systematically
OVER-estimates cardinality for moderate-cardinality columns (birthday /
coupon-collector effect): a zip column with ~5k distinct values in ~19k rows
reads ~0.96 in a 1k sample vs a true ~0.26. That inflated ratio promoted
``zip -> "identifier"`` (the numeric-shape cardinality promotion), which
committed a standalone ``exact[zip]`` matchkey and caused ~55% over-merge.

Type CLASSIFICATION tolerates sampling; CARDINALITY does not. These tests pin
the decoupling: classification value-heuristics stay on the sample, but
``cardinality_ratio`` and ``null_rate`` are honest full-column measurements.

All dataframes are small in-memory (<= a few thousand rows) -- profiling-level
only, no controller/engine/benchmark runs.
"""
import polars as pl
import pytest
from goldenmatch.core._native_loader import native_enabled
from goldenmatch.core.autoconfig import profile_columns

# Whether the native autoconfig classify kernel is active in this env. The
# #1351 promotion fix must hold on BOTH paths; these tests exercise whichever
# is active. (In the --extra dev env the native wheel is typically present.)
_NATIVE_AUTOCONFIG = native_enabled("autoconfig")


def _profile_by_name(df: pl.DataFrame) -> dict:
    return {p.name: p for p in profile_columns(df)}


class TestFullDataCardinality1351:
    def test_repro_low_cardinality_ratio_is_honest_not_sample_inflated(self):
        """A high-density, low-cardinality zip column (26 distinct in 2,000 rows,
        true ratio ~0.013) must report the HONEST full-column ratio.

        The buggy path divided unique-in-sample by the SAMPLE height (1,000), so
        even capturing all 26 distinct it read ~0.026 -- double the truth. The
        honest full-column ratio is 26 / 2,000.
        """
        zips = [f"{10000 + (i % 26):05d}" for i in range(2000)]
        df = pl.DataFrame({"zip": zips, "name": [f"n{i}" for i in range(2000)]})

        prof = _profile_by_name(df)["zip"]

        assert df.height > 1000  # sampling path is active
        assert prof.cardinality_ratio == pytest.approx(26 / 2000, abs=1e-4)
        # Guard against the sample denominator (26 / 1000 = 0.026).
        assert prof.cardinality_ratio < 0.02

    def test_moderate_cardinality_zip_not_promoted_to_identifier(self):
        """A zip column whose 1k sample looks near-unique (moderate full-data
        cardinality) must stay ``zip`` -- NOT be promoted to ``identifier``.

        2,500 distinct 5-digit zips across 3,000 rows -> honest ratio ~0.833. A
        1,000-row sample is almost all-unique (~0.98 >= the 0.95 promotion floor),
        which is exactly what used to (wrongly) promote it. With honest cardinality
        threaded into the promotion the column stays a zip.
        """
        zips = [f"{10000 + (i % 2500):05d}" for i in range(3000)]
        df = pl.DataFrame({"zip": zips})

        prof = _profile_by_name(df)["zip"]

        assert prof.cardinality_ratio == pytest.approx(2500 / 3000, abs=1e-3)
        assert prof.col_type != "identifier"
        assert prof.col_type == "zip"

    def test_genuinely_high_cardinality_still_identifier(self):
        """A column with genuinely near-unique full-data cardinality still
        promotes to identifier and carries ratio ~= 1.0."""
        phones = [f"{5550000000 + i}" for i in range(2000)]
        df = pl.DataFrame({"phone": phones})

        prof = _profile_by_name(df)["phone"]

        assert prof.cardinality_ratio == pytest.approx(1.0, abs=1e-6)
        assert prof.col_type == "identifier"

    def test_null_rate_is_honest_full_column(self):
        """null_rate reflects the true full-column null fraction, exactly."""
        values: list[str | None] = [f"val{i}" for i in range(1500)] + [None] * 500
        df = pl.DataFrame({"code": pl.Series("code", values, dtype=pl.Utf8)})

        prof = _profile_by_name(df)["code"]

        assert df.height == 2000
        assert prof.null_rate == pytest.approx(0.25, abs=1e-6)

    def test_native_and_python_paths_agree_on_promotion(self):
        """The promotion fix must hold regardless of which classify path runs.

        This documents the active path so a failure here is attributable. Both
        paths route through the same honest-cardinality promotion (native via the
        Python-side post-classify demotion), so a moderate-cardinality zip stays
        a zip either way.
        """
        zips = [f"{20000 + (i % 2200):05d}" for i in range(3000)]
        df = pl.DataFrame({"zip": zips})

        prof = _profile_by_name(df)["zip"]

        # Assertion holds on native and pure-Python paths alike.
        assert prof.col_type == "zip", (
            f"promoted to {prof.col_type!r} on "
            f"{'native' if _NATIVE_AUTOCONFIG else 'python'} classify path"
        )

    def test_name_authoritative_id_not_demoted(self):
        """A column NAMED like an id (`order_id`) holding legitimately-repeating
        numeric values (moderate cardinality) must stay ``identifier``.

        The native-path demotion correction (#1351) re-runs the data-only
        classifier, which would call a moderate-cardinality numeric column
        "numeric" -- but ``order_id`` is ``identifier`` because its NAME is
        authoritative, not because of the cardinality promotion being corrected.
        Demoting it to "numeric" would make ``build_matchkeys`` skip it and the id
        would silently lose its matchkey. The name-authority guard must prevent
        that on the native path (the pure-Python path already respects it).
        """
        # 900 distinct ids across 3,000 rows -> honest ratio 0.3 (below the
        # promotion floor); numeric-shaped so _guess_type -> "numeric".
        ids = [str(700000 + (i % 900)) for i in range(3000)]
        df = pl.DataFrame({"order_id": ids})

        prof = _profile_by_name(df)["order_id"]

        assert prof.cardinality_ratio == pytest.approx(900 / 3000, abs=1e-3)
        assert prof.col_type == "identifier", (
            f"name-authoritative order_id wrongly reclassified to "
            f"{prof.col_type!r} on "
            f"{'native' if _NATIVE_AUTOCONFIG else 'python'} classify path"
        )
