import polars as pl
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.streaming import StreamProcessor


def test_transform_one():
    processor = StreamProcessor()
    result = processor.transform_one({"name": "  John  ", "email": "JOHN@TEST.COM"})
    assert result.df.shape == (1, 2)


def test_transform_batch():
    df = pl.DataFrame({"name": ["  A  ", "  B  "], "email": ["X@Y.COM", "Z@W.COM"]})
    processor = StreamProcessor()
    result = processor.transform_batch(df)
    assert result.df.shape[0] == 2


def test_stream_file(sample_csv):
    processor = StreamProcessor()
    results = list(processor.stream_file(sample_csv, chunk_size=2))
    assert len(results) >= 1
    total_rows = sum(r.df.shape[0] for r in results)
    assert total_rows == 3  # sample_csv has 3 rows
    assert processor.batches_processed >= 1


def test_stream_with_config(sample_csv):
    config = GoldenFlowConfig(
        transforms=[TransformSpec(column="name", ops=["strip"])]
    )
    processor = StreamProcessor(config=config)
    results = list(processor.stream_file(sample_csv, chunk_size=2))
    assert len(results) >= 1
