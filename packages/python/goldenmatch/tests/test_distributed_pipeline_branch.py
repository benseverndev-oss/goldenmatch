import pytest

ray = pytest.importorskip("ray")


def test_load_input_frames_routes_to_distributed_when_flag_set(tmp_path, monkeypatch):
    """When GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1 AND backend=ray, route to Ray Datasets."""
    import polars as pl
    from goldenmatch.core.pipeline import _load_input_frames

    monkeypatch.setenv("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")

    csv = tmp_path / "in.csv"
    pl.DataFrame({"id": range(100), "name": ["x"] * 100}).write_csv(csv)

    class _Cfg:
        backend = "ray"
        inputs = [str(csv)]

    out = _load_input_frames(_Cfg())
    # Distributed branch returns a Ray Dataset
    assert hasattr(out, "count")
    assert hasattr(out, "map_batches")
    assert out.count() == 100


def test_load_input_frames_uses_legacy_loader_without_flag(tmp_path, monkeypatch):
    """Without the env flag, route to the legacy load_files path."""
    import polars as pl
    from goldenmatch.core.pipeline import _load_input_frames

    monkeypatch.delenv("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", raising=False)

    csv = tmp_path / "in.csv"
    pl.DataFrame({"id": range(50), "name": ["x"] * 50}).write_csv(csv)

    class _Cfg:
        backend = "ray"  # backend=ray but flag NOT set -> legacy path
        inputs = [str(csv)]

    out = _load_input_frames(_Cfg())
    # Legacy load_files returns list[pl.LazyFrame]
    assert isinstance(out, list)
    assert len(out) == 1


def test_load_input_frames_uses_legacy_loader_for_non_ray_backend(tmp_path, monkeypatch):
    """For any non-ray backend, route to legacy even with the flag set."""
    import polars as pl
    from goldenmatch.core.pipeline import _load_input_frames

    monkeypatch.setenv("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")

    csv = tmp_path / "in.csv"
    pl.DataFrame({"id": range(20), "name": ["x"] * 20}).write_csv(csv)

    class _Cfg:
        backend = "bucket"
        inputs = [str(csv)]

    out = _load_input_frames(_Cfg())
    assert isinstance(out, list)
