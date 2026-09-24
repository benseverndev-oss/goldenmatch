from __future__ import annotations

import warnings

import pyarrow as pa
from goldenmatch.core.frame import ArrowFrame


def test_arrow_sort_is_stable_nulls_first_without_future_warning() -> None:
    table = pa.table(
        {
            "key": pa.array(["b", None, "a", "b", "a"], type=pa.string()),
            "position": pa.array([0, 1, 2, 3, 4], type=pa.int64()),
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        result = ArrowFrame(table).sort(["key"])

    assert result.column("key").to_list() == [None, "a", "a", "b", "b"]
    assert result.column("position").to_list() == [1, 2, 4, 0, 3]
