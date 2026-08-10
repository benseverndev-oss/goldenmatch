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
from typing import Any, Callable

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
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    import goldenmatch.core.pipeline as P

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
    from goldenmatch.config.schemas import GoldenMatchConfig
    import goldenmatch.core.pipeline as P

    cfg = GoldenMatchConfig(matchkeys=[_exact_matchkey()])
    return P.run_dedupe_df(_people_table(), cfg)


CASES: dict[str, Callable[[], Any]] = {
    "exact": case_exact,
    "default_prep": case_default_prep,
}
