"""#1207 PR2a: data-driven TF name weighting."""
from __future__ import annotations

import polars as pl
from goldenmatch.core.tf_tables import value_frequencies


def test_value_frequencies_relative_and_transformed():
    df = pl.DataFrame({"last_name": ["Smith", "smith", "SMITH", "Zelinski", None, ""]})
    freqs = value_frequencies(df, "last_name", transforms=["lowercase", "strip"])
    # 3 "smith" + 1 "zelinski" over 4 non-empty -> 0.75 / 0.25; null+"" dropped
    assert abs(freqs["smith"] - 0.75) < 1e-9
    assert abs(freqs["zelinski"] - 0.25) < 1e-9
    assert "" not in freqs and None not in freqs
