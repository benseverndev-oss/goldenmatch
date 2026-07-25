#!/usr/bin/env python3
"""Emit cross-language parity goldens for the FS domain comparators
``date_diff`` and ``geo_haversine`` (score-core score_one ids 17 / 18).

Writes tests/parity/fixtures/scorer-domain-comparators.json: rows of
[scorer, a, b, expected]. Unlike the rapidfuzz goldens, the BINDING oracle here
is the Python reference (``goldenmatch.core.scorer._date_diff_similarity_py`` /
``_geo_haversine_similarity_py``) which is itself byte-verified == the Rust
score-core kernel (tests/test_native_date_diff_geo_parity.py). So these lock the
pure-TS port to Rust-via-Python; the WASM path is pinned separately in
tests/parity/wasm-scorer.test.ts.

Outputs are DISCRETE bands, so cross-surface float noise (Rust libm vs JS Math
in the haversine) cannot shift a value unless a pair sits within an ULP of a band
edge -- which the geo cases below deliberately avoid (each is well inside a band).

Deterministic; imports goldenmatch only. Run:
    PYTHONIOENCODING=utf-8 .venv/bin/python \
        packages/python/goldenmatch/scripts/emit_domain_comparator_fixtures.py
"""
import json
from pathlib import Path

from goldenmatch.core.scorer import (
    _date_diff_similarity_py,
    _geo_haversine_similarity_py,
)

OUT = (
    Path(__file__).resolve().parents[3]
    / "typescript/goldenmatch/tests/parity/fixtures/scorer-domain-comparators.json"
)

# date_diff: bands (0->1.0, <=1d->0.92, <=31d->0.80, <=366d->0.60, <=1827d->0.30,
# else 0.0), MM/DD transposition floored to the <=31d band, edit-distance
# (`date`) fallback on unparseable input. Covers every band, the transposition
# floor, the accepted parse shapes (ISO / slash / compact / bare-year), invalid
# dates (-> fallback), and the unparseable fallback.
DATE_PAIRS = [
    ("1990-05-12", "1990-05-12"),   # same -> 1.0
    ("1990-01-01", "1990-01-02"),   # 1 day -> 0.92
    ("1990-01-01", "1990-01-15"),   # 14 days -> 0.80
    ("1990-01-01", "1990-01-31"),   # 30 days -> 0.80
    ("1990-01-01", "1990-06-01"),   # ~151 days -> 0.60
    ("1990-01-01", "1993-01-01"),   # ~3 y -> 0.30
    ("1990-01-01", "2000-01-01"),   # 10 y -> 0.0
    ("1990-01-02", "1990-02-01"),   # MM/DD transposition -> floored 0.80
    ("1990", "1991"),               # bare year, 365 d -> 0.60
    ("1990", "1990"),               # bare year same -> 1.0
    ("19900101", "19900103"),       # compact YYYYMMDD, 2 d -> 0.80
    ("1990/05/12", "1990/05/13"),   # slash sep, 1 d -> 0.92
    ("1990-13-40", "1990-13-40"),   # invalid -> fallback (date edit-dist)
    ("1990-05-12", "not-a-date"),   # one side unparseable -> fallback
    ("not-a-date", "not-a-date"),   # both unparseable -> levenshtein 1.0
    ("", ""),                       # empty -> fallback
]

# geo_haversine: bands (<=0.1km->1.0, <=1km->0.85, <=10km->0.5, <=100km->0.2,
# else 0.0), exact-string fallback on unparseable. Coordinates chosen well INSIDE
# each band (never near an edge) so Rust-libm vs JS-Math haversine noise can't
# flip a band.
GEO_PAIRS = [
    ("40.0,-73.0", "40.0,-73.0"),        # same -> 1.0
    ("40.0,-73.0", "40.0003,-73.0"),     # ~0.033 km -> 1.0
    ("40.0,-73.0", "40.004,-73.0"),      # ~0.44 km -> 0.85
    ("40.0,-73.0", "40.05,-73.0"),       # ~5.6 km -> 0.5
    ("40.0,-73.0", "40.5,-73.0"),        # ~55.6 km -> 0.2
    ("40.0,-73.0", "45.0,-73.0"),        # ~556 km -> 0.0
    ("40.0;-73.0", "40.05;-73.0"),       # semicolon sep -> 0.5
    ("40.0, -73.0", "40.0, -73.0"),      # spaces around sep -> 1.0
    ("abc", "abc"),                      # unparseable, equal -> 1.0
    ("abc", "def"),                      # unparseable, unequal -> 0.0
    ("200.0,0.0", "200.0,0.0"),          # out-of-range lat -> fallback exact 1.0
    ("40.0,-73.0", "notcoords"),         # one side unparseable -> 0.0
]

rows: list[list] = []
for a, b in DATE_PAIRS:
    rows.append(["date_diff", a, b, round(_date_diff_similarity_py(a, b), 6)])
for a, b in GEO_PAIRS:
    rows.append(["geo_haversine", a, b, round(_geo_haversine_similarity_py(a, b), 6)])

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps({"cases": rows}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)
print(f"wrote {len(rows)} cases -> {OUT}")
