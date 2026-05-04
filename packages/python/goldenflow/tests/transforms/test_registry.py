import polars as pl

from goldenflow.transforms import (
    get_transform,
    list_transforms,
    parse_transform_name,
    register_transform,
)


def test_register_and_retrieve_expr_transform():
    @register_transform(
        name="_test_lower",
        input_types=["string"],
        auto_apply=True,
        priority=50,
        mode="expr",
    )
    def _test_lower(column: str) -> pl.Expr:
        return pl.col(column).str.to_lowercase()

    info = get_transform("_test_lower")
    assert info is not None
    assert info.mode == "expr"
    assert info.auto_apply is True


def test_register_and_retrieve_series_transform():
    @register_transform(
        name="_test_upper_series",
        input_types=["string"],
        auto_apply=False,
        priority=30,
        mode="series",
    )
    def _test_upper_series(series: pl.Series) -> pl.Series:
        return series.str.to_uppercase()

    info = get_transform("_test_upper_series")
    assert info is not None
    assert info.mode == "series"


def test_get_nonexistent_transform():
    assert get_transform("nonexistent_transform_xyz") is None


def test_parse_transform_name_simple():
    name, params = parse_transform_name("strip")
    assert name == "strip"
    assert params == []


def test_parse_transform_name_with_params():
    name, params = parse_transform_name("truncate:100")
    assert name == "truncate"
    assert params == ["100"]


def test_parse_transform_name_with_multiple_params():
    name, params = parse_transform_name("clamp:0:100")
    assert name == "clamp"
    assert params == ["0", "100"]


def test_list_transforms_returns_registered():
    @register_transform(
        name="_test_list_marker",
        input_types=["string"],
        auto_apply=False,
        priority=10,
        mode="expr",
    )
    def _marker(column: str) -> pl.Expr:
        return pl.col(column)

    transforms = list_transforms()
    assert "_test_list_marker" in [t.name for t in transforms]
