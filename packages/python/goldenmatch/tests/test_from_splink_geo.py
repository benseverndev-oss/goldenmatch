"""P3b: Splink DistanceInKMAtThresholds -> geo_haversine conversion.

Geo is the ONE cross-column comparison: Splink gives SEPARATE lat + lng columns,
but goldenmatch's geo_haversine scores ONE comma-joined "lat,long" field. The
conversion emits a MatchkeyField.derive_from=[lat,lng] + derive_separator=","
that the pipeline materializes before scoring. SQL below is the REAL splink 4
serialization (cl.DistanceInKMAtThresholds('lat','lng',[1,10]).get_comparison).
"""
import pyarrow as pa
import pytest
from goldenmatch.config.from_splink import (
    ConversionReport,
    convert_comparison,
    recognize_level,
)
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.frame import to_frame
from goldenmatch.core.matchkey import precompute_matchkey_transforms_frame


def _geo_level(km, quote='"'):
    """Real DuckDB (quote=\") / Spark (quote=`) haversine level SQL at <km>."""
    c = lambda n: f"{quote}{n}{quote}"  # noqa: E731
    return (
        f"cast( acos( case when ( sin( radians({c('lat_l')}) ) * sin( radians({c('lat_r')}) ) "
        f"+ cos( radians({c('lat_l')}) ) * cos( radians({c('lat_r')}) ) "
        f"* cos( radians({c('lng_r')} - {c('lng_l')}) ) ) > 1 then 1 "
        f"when ( sin( radians({c('lat_l')}) ) * sin( radians({c('lat_r')}) ) "
        f"+ cos( radians({c('lat_l')}) ) * cos( radians({c('lat_r')}) ) "
        f"* cos( radians({c('lng_r')} - {c('lng_l')}) ) ) < -1 then -1 "
        f"else ( sin( radians({c('lat_l')}) ) * sin( radians({c('lat_r')}) ) "
        f"+ cos( radians({c('lat_l')}) ) * cos( radians({c('lat_r')}) ) "
        f"* cos( radians({c('lng_r')} - {c('lng_l')}) ) ) end ) * 6371 as float ) <= {km}"
    )


@pytest.mark.parametrize("quote", ['"', "`"])
@pytest.mark.parametrize("km,band", [(1, 0.85), (10, 0.5), (100, 0.2)])
def test_geo_recognized_both_dialects(quote, km, band):
    r = recognize_level(_geo_level(km, quote))
    assert r is not None
    assert r.kind == "geo_haversine"
    assert r.column == "lat__lng"
    assert r.derive_from == ["lat", "lng"]
    assert r.derive_separator == ","
    assert r.sim_threshold == pytest.approx(band, abs=1e-9)
    assert r.approx is True


def test_geo_mismatched_lat_lng_dropped():
    # lat operands disagree (lat vs latitude) -> not a clean single-lat haversine.
    sql = _geo_level(1).replace('radians("lat_r")', 'radians("latitude_r")')
    assert recognize_level(sql) is None


def test_non_geo_acos_not_recognized():
    # acos present but no "* 6371" earth-radius marker -> not haversine.
    assert recognize_level('cast( acos("x_l") as float ) <= 1') is None


def _distance_comparison():
    """Real DuckDB DistanceInKMAtThresholds([1, 10]): compound null, two km
    levels, ELSE."""
    return {
        "output_column_name": "location",
        "comparison_levels": [
            {
                "sql_condition": (
                    '("lat_l" IS NULL OR "lat_r" IS NULL) OR '
                    '("lng_l" IS NULL OR "lng_r" IS NULL)'
                ),
                "is_null_level": True,
            },
            {"sql_condition": _geo_level(1)},
            {"sql_condition": _geo_level(10)},
            {"sql_condition": "ELSE"},
        ],
    }


def test_distance_comparison_converts_to_geo_field():
    report = ConversionReport()
    field = convert_comparison(_distance_comparison(), 0, report)

    assert field is not None
    assert field.field == "lat__lng"
    assert field.scorer == "geo_haversine"
    assert field.levels == 3
    assert field.level_thresholds == [0.85, 0.5]
    assert field.derive_from == ["lat", "lng"]
    assert field.derive_separator == ","


def test_distance_comparison_warns_on_km_snap():
    report = ConversionReport()
    convert_comparison(_distance_comparison(), 0, report)
    warns = [f.message for f in report.findings if f.severity == "warning"]
    assert any("great-circle km cutoff snapped" in w for w in warns)


def test_derive_from_requires_two_columns():
    with pytest.raises(ValueError, match="at least 2 source columns"):
        MatchkeyField(field="lat__lng", scorer="geo_haversine", weight=1.0,
                      derive_from=["lat"])


def test_geo_field_materializes_on_arrow_lane():
    """The synthesized 'lat,long' field is built (comma-joined) by the pipeline's
    arrow-lane transform precompute -- the goldenmatch-default path."""
    field = MatchkeyField(field="lat__lng", scorer="geo_haversine", weight=1.0,
                          derive_from=["lat", "lng"], derive_separator=",")
    mk = MatchkeyConfig(name="geo", type="weighted", threshold=0.5, fields=[field])
    tbl = pa.table({"lat": ["40.7", "51.5", None], "lng": ["-74.0", "-0.12", "5.0"]})

    out = to_frame(precompute_matchkey_transforms_frame(tbl, [mk])).to_arrow().to_pydict()
    assert out["lat__lng"] == ["40.7,-74.0", "51.5,-0.12", ",5.0"]


def test_geo_conversion_scores_end_to_end():
    """A converted geo config actually resolves: two coordinates ~0.7 km apart
    cluster; a coordinate ~900 km away does not."""
    import goldenmatch as gm

    report = ConversionReport()
    field = convert_comparison(_distance_comparison(), 0, report)
    # Score it on the weighted path (a converted field carries level_thresholds,
    # not a weight); one field, weight 1.0 -> match score == the geo sim.
    field.weight = 1.0
    mk = MatchkeyConfig(
        name="geo", type="weighted", threshold=0.6, fields=[field]
    )
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
    )

    config = GoldenMatchConfig(
        matchkeys=[mk],
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["country"])]
        ),
    )
    # rows 0 & 1: ~0.7 km apart in London; row 2: Paris (~340 km).
    df = pa.table({
        "id": ["a", "b", "c"],
        "lat": ["51.5074", "51.5010", "48.8566"],
        "lng": ["-0.1278", "-0.1200", "2.3522"],
        "country": ["gb", "gb", "gb"],
    })
    result = gm.dedupe_df(df, config=config)
    # rows 0 (a) & 1 (b) share a cluster; row 2 (c, Paris) is a singleton.
    multi = [set(c["members"]) for c in result.clusters.values() if c["size"] > 1]
    assert {0, 1} in multi, (multi, [dict(c) for c in result.clusters.values()])
    assert not any(2 in m for m in multi)
