"""Arrow-native UDF plumbing for the Spark tier.

The tier used ``pandas_udf`` everywhere, which made **pandas a runtime
requirement on every executor** -- in a repo that evicted polars, made pyarrow
the hard dependency, and does not declare pandas as a dependency at all. The
executor environment P1 ships had to `pip install pandas` explicitly to make the
tier work, which is an undeclared dependency documented rather than fixed.

Spark has first-class Arrow UDFs, so there was never a need:

    @arrow_udf("string")
    def f(s: pa.Array) -> pa.Array: ...

Same shape as ``pandas_udf``, ``pa.Array`` instead of ``pd.Series``, and pyarrow
is already a hard dependency.

Two things fall out of the change beyond dropping a dependency:

- **Nulls stop being NaN.** A pandas Series of an all-null string batch infers
  float64, so iterating yielded NaN floats -- and NaN is TRUTHY, so `x or ""`
  did not rescue it and the float reached the scorer as
  `TypeError: 'float' object is not subscriptable`. A `pa.Array` carries a
  validity bitmap; `to_pylist()` gives `None`. The bug class disappears rather
  than being defended against.
- **The batch is already the shape the kernel wants.** `pa.Array` for a string
  column IS the offsets+data layout `score-cabi` takes, so the Python path can
  hand buffers to Rust with no conversion.
"""
from __future__ import annotations

from typing import Any


def arrow_udf(return_type: str) -> Any:
    """``pyspark.sql.functions.arrow_udf``, resolved with a legible failure.

    Raises rather than falling back to ``pandas_udf``: a silent fallback would
    reinstate the pandas requirement on executors, which is the whole thing
    being removed. Better to fail at registration, on the driver, with a message
    naming the Spark version needed, than to work by quietly shipping pandas.
    """
    from pyspark.sql import functions as F

    fn = getattr(F, "arrow_udf", None)
    if fn is None:
        import pyspark

        raise RuntimeError(
            f"pyspark {pyspark.__version__} has no `arrow_udf`. The Spark tier "
            f"is Arrow-native and deliberately does NOT fall back to "
            f"`pandas_udf`, because that would put pandas back on every "
            f"executor -- an undeclared dependency this package does not have. "
            f"Upgrade pyspark, or run this workload on the one-box path."
        )
    return fn(return_type)


def to_pylist(arr: Any) -> list:
    """Values of a ``pa.Array`` as Python objects, nulls as ``None``.

    A one-line helper so call sites read the same everywhere and nobody
    reintroduces ``.tolist()`` (the pandas spelling) by muscle memory.
    """
    return arr.to_pylist()


def from_pylist(values: list, type_: str = "string") -> Any:
    """A ``pa.Array`` from Python values, for returning out of an arrow_udf.

    ``type_`` mirrors the UDF's declared return type; passing it explicitly
    stops pyarrow inferring, which on an all-null batch would produce a null-typed
    array that Spark cannot match to the declared schema.
    """
    import pyarrow as pa

    dtype = {"string": pa.string(), "double": pa.float64()}[type_]
    return pa.array(values, type=dtype)
