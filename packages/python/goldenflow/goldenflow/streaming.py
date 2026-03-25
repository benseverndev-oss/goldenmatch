"""Streaming/incremental transform processing for GoldenFlow."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import polars as pl

from goldenflow.config.schema import GoldenFlowConfig
from goldenflow.engine.transformer import TransformEngine, TransformResult


class StreamProcessor:
    """Process data incrementally, one batch at a time.

    Usage:
        processor = StreamProcessor(config=config)

        # Process a single record
        result = processor.transform_one({"name": "  John  ", "phone": "(555) 123-4567"})

        # Process a batch
        result = processor.transform_batch(df_batch)

        # Stream a file in chunks
        for result in processor.stream_file("large_data.csv", chunk_size=10000):
            write_to_output(result.df)
    """

    def __init__(self, config: GoldenFlowConfig | None = None):
        self.engine = TransformEngine(config=config or GoldenFlowConfig())
        self._batch_count = 0

    def transform_one(self, record: dict) -> TransformResult:
        """Transform a single record (dict -> TransformResult)."""
        df = pl.DataFrame([record])
        return self.engine.transform_df(df)

    def transform_batch(self, df: pl.DataFrame) -> TransformResult:
        """Transform a batch of records."""
        self._batch_count += 1
        return self.engine.transform_df(df)

    def stream_file(
        self, path: str | Path, chunk_size: int = 10_000
    ) -> Iterator[TransformResult]:
        """Stream a file in chunks, yielding TransformResult per chunk."""
        path = Path(path)
        reader = pl.read_csv_batched(path, batch_size=chunk_size)

        while True:
            batches = reader.next_batches(1)
            if not batches:
                break
            for batch in batches:
                self._batch_count += 1
                yield self.engine.transform_df(batch)

    @property
    def batches_processed(self) -> int:
        return self._batch_count
