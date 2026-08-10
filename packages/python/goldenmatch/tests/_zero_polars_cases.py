"""Config matrix for the D6 zero-polars gate.

Importable WITHOUT pytest and WITHOUT the parent ``conftest.py`` (which imports
polars), because the probe subprocess imports this module while polars imports
are blocked at ``sys.meta_path``.

Each case is a zero-arg callable that RUNS a dedupe and returns the result. A
case owns its own ingest on purpose: ``exact`` goes through the CSV front
(``run_dedupe``) so the arrow reader stays covered, while the rest hand an
in-memory ``pa.Table`` to ``run_dedupe_df``. Returning a builder tuple instead
would have quietly collapsed both entry points into one.

Keep fixtures TINY -- the gate runs one subprocess per case per lane.
"""
from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Callable
from typing import Any

# Configs that legitimately CANNOT run polars-free today. Every entry needs a
# reason. Adding one is a review conversation; removing one is a win.
#
# NOTE: a leak is not automatically a decline. Read the traceback first -- a
# stray `isinstance(x, pl.DataFrame)` on a covered path is a BUG to fix, not a
# dependency to declare. Only genuine polars-only features belong here.
KNOWN_POLARS_DEPENDENT: dict[str, str] = {}


def _people_table() -> Any:
    """60 rows of duplicate pairs.

    Clears goldencheck's fuzzy thresholds (``_MIN_ROWS=50``, ``_MIN_DISTINCT=3``)
    so the quality-weighting cases get real signal -- the pre-existing 5-row
    fixture in this suite could never trigger a penalty.

    One pair disagrees on city ("Californa" vs "California") so survivorship has
    something to resolve.
    """
    import pyarrow as pa

    names: list[str] = []
    cities: list[str] = []
    emails: list[str] = []
    for i in range(30):
        if i < 15:
            city_a = city_b = "California"
        elif i < 24:
            city_a = city_b = "Texas"
        elif i < 29:
            city_a = city_b = "Nevada"
        else:
            city_a, city_b = "Californa", "California"
        names += [f"person{i}", f"person{i}"]
        cities += [city_a, city_b]
        emails += [f"p{i}@x.com", f"p{i}@x.com"]
    return pa.table({"name": names, "city": cities, "email": emails})


def _exact_matchkey(field: str = "name") -> Any:
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    return MatchkeyConfig(
        name="k", type="exact", fields=[MatchkeyField(field=field)]
    )


def _prep_disabled() -> dict[str, Any]:
    """quality + transform explicitly OFF (they default ON)."""
    from goldenmatch.config.schemas import QualityConfig, TransformConfig

    return {
        "quality": QualityConfig(mode="disabled"),
        "transform": TransformConfig(mode="disabled"),
    }


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------


def case_exact() -> Any:
    """The ORIGINAL probe case, preserved verbatim: CSV front + exact matchkey,
    prep disabled. Keeps the arrow reader under the gate."""
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    d = pathlib.Path(tempfile.mkdtemp())
    csv = d / "people.csv"
    csv.write_text(
        "first,last,city\n"
        "ann,smith,nyc\n"
        "ann,smith,nyc\n"
        "bob,jones,la\n"
        "bobby,jones,la\n"
        "cara,lee,sf\n",
        encoding="utf-8",
    )
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="k",
                type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )
        ],
        **_prep_disabled(),
    )
    res = P.run_dedupe([(str(csv), "people")], cfg)
    assert res["golden"] is not None and res["golden"].num_rows >= 1
    return res


def case_default_prep() -> Any:
    """Quality + transform at their DEFAULTS (both default-ON).

    This is what a real `pip install goldenmatch` user gets, and the config the
    original single-case probe never exercised.
    """
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(matchkeys=[_exact_matchkey()])
    return P.run_dedupe_df(_people_table(), cfg)


def case_weighted_fuzzy() -> Any:
    """Weighted matchkey with fuzzy per-field scorers.

    There is no "fuzzy" matchkey TYPE (the literal is exact/weighted/
    probabilistic); fuzzy comparison rides on ``weighted`` via per-field
    ``scorer``. This is the scorer path where the #2250 lane divergence lived.
    """
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    cfg = GoldenMatchConfig(
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
        matchkeys=[
            MatchkeyConfig(
                name="k",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler", weight=0.7),
                    MatchkeyField(field="city", scorer="jaro_winkler", weight=0.3),
                ],
            )
        ],
        **_prep_disabled(),
    )
    return P.run_dedupe_df(_people_table(), cfg)


def case_probabilistic() -> Any:
    """Fellegi-Sunter matchkey -- the EM/FS path."""
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    cfg = GoldenMatchConfig(
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
        matchkeys=[
            MatchkeyConfig(
                name="k",
                type="probabilistic",
                fields=[
                    MatchkeyField(field="name", scorer="jaro_winkler"),
                    MatchkeyField(field="city", scorer="jaro_winkler"),
                ],
            )
        ],
        **_prep_disabled(),
    )
    return P.run_dedupe_df(_people_table(), cfg)


def case_quality_weighting() -> Any:
    """quality_weighting=True over the dirty-city fixture.

    Regression pin for #2462: weighting used to be silently skipped polars-free
    (and dead on the arrow lane even WITH polars, because
    ``"col" not in pa_table.columns`` is silently True).
    """
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import GoldenMatchConfig, GoldenRulesConfig

    cfg = GoldenMatchConfig(
        matchkeys=[_exact_matchkey()],
        golden_rules=GoldenRulesConfig(default_strategy="most_complete", quality_weighting=True),
        **_prep_disabled(),
    )
    return P.run_dedupe_df(_people_table(), cfg)


def case_zero_config() -> Any:
    """No matchkeys -- auto-config picks. The most-used entry point."""
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import GoldenMatchConfig

    cfg = GoldenMatchConfig(matchkeys=[], **_prep_disabled())
    return P.run_dedupe_df(_people_table(), cfg)


def case_golden_strategies() -> Any:
    """NON-default survivorship: per-field ``most_recent`` + ``longest_value``.

    The default is ``most_complete``; the fused golden kernel declines some
    strategies and falls back to the demux, so this covers a different golden
    route than the other cases.
    """
    import goldenmatch.core.pipeline as P
    import pyarrow as pa
    from goldenmatch.config.schemas import (
        GoldenFieldRule,
        GoldenMatchConfig,
        GoldenRulesConfig,
    )

    tbl = _people_table()
    # Recency column so `most_recent` has something to order by.
    seen = pa.array([1_600_000_000 + i for i in range(tbl.num_rows)], type=pa.int64())
    tbl = tbl.append_column("seen_at", seen)

    cfg = GoldenMatchConfig(
        matchkeys=[_exact_matchkey()],
        golden_rules=GoldenRulesConfig(
            default_strategy="most_complete",
            fields={
                "city": GoldenFieldRule(strategy="most_recent", date_column="seen_at"),
                "email": GoldenFieldRule(strategy="longest_value"),
            },
        ),
        **_prep_disabled(),
    )
    return P.run_dedupe_df(tbl, cfg)


def case_bucket_partitioned() -> Any:
    """``partitioned_block_scoring`` -- the bucketed-materialize scale path.

    One of the two flows `_arrow_lane_supported` historically declined, and the
    one whose assignment-table build was polars-native.
    """
    import goldenmatch.core.pipeline as P
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    cfg = GoldenMatchConfig(
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
        matchkeys=[
            MatchkeyConfig(
                name="k",
                type="weighted",
                threshold=0.85,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        partitioned_block_scoring=True,
        **_prep_disabled(),
    )
    return P.run_dedupe_df(_people_table(), cfg)


CASES: dict[str, Callable[[], Any]] = {
    "exact": case_exact,
    "default_prep": case_default_prep,
    "weighted_fuzzy": case_weighted_fuzzy,
    "probabilistic": case_probabilistic,
    "quality_weighting": case_quality_weighting,
    "zero_config": case_zero_config,
    "golden_strategies": case_golden_strategies,
    "bucket_partitioned": case_bucket_partitioned,
}
