"""ArrowColumn.sort / is_sorted must not warn on pyarrow >= 25 (#2992).

`pc.sort_indices(arr, null_placement=...)` builds SortOptions, whose global
null_placement pyarrow 25 deprecated with a FutureWarning on every call.
`pc.array_sort_indices` takes it through ArraySortOptions, which is not
deprecated and exists across the supported pyarrow>=14 range.
"""

import warnings

import pyarrow as pa
from goldencheck.core.frame import ArrowColumn


def test_sort_is_nulls_first_stable_and_warning_free():
    col = ArrowColumn(pa.chunked_array([[3, None], [1, None, 2, 1]]))
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        out = col.sort().to_list()
        assert col.sort().is_sorted()
    assert out == [None, None, 1, 1, 2, 3]
