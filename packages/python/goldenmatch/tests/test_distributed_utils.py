import polars as pl
import pytest


def test_is_ray_dataset_recognises_polars_df_as_false():
    from goldenmatch.distributed import is_ray_dataset
    df = pl.DataFrame({"x": [1, 2]})
    assert is_ray_dataset(df) is False


def test_is_ray_dataset_recognises_ray_dataset_as_true():
    ray = pytest.importorskip("ray")
    from goldenmatch.distributed import is_ray_dataset

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)
    ds = ray.data.from_items([{"x": 1}, {"x": 2}])
    assert is_ray_dataset(ds) is True


def test_is_ray_dataset_handles_none_and_primitives():
    from goldenmatch.distributed import is_ray_dataset
    assert is_ray_dataset(None) is False
    assert is_ray_dataset("string") is False
    assert is_ray_dataset(42) is False
