"""Linkage-parity + polars-free harness for the match pipeline arrow-flip.

Two guarantees, the eviction's own standard:
1. PARITY: arrow-input `match_df` produces byte-identical linkage (the set of
   (target_row_id, ref_row_id) matched pairs) to polars-input `match_df`, across
   diverse shapes.
2. POLARS-FREE: an arrow-input `match_df` completes in a subprocess with
   `import polars` BLOCKED (the D6 zero-polars end-state).
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pyarrow as pa
import pytest

# (target, reference) shapes exercising exact + fuzzy linkage.
_SHAPES = {
    "exact_email": (
        {
            "first": ["ann", "bob", "cara", "dan", "eve"],
            "last": ["smith", "jones", "lee", "poe", "adams"],
            "email": ["a@x.com", "b@y.com", "c@z.com", "d@w.com", "e@v.com"],
        },
        {
            "first": ["ann", "cara", "xavier"],
            "last": ["smith", "lee", "zim"],
            "email": ["a@x.com", "c@z.com", "x@q.com"],
        },
    ),
    "fuzzy_names": (
        {
            "name": ["Jonathan Smith", "Robert Jones", "Catherine Lee", "Daniel Poe"],
            "city": ["nyc", "la", "sf", "dc"],
        },
        {
            "name": ["Jon Smith", "Cathy Lee", "Xavier Zim"],
            "city": ["nyc", "sf", "bos"],
        },
    ),
}


def _pairs_from_result(result) -> set[tuple]:
    m = result.matched
    if m is None:
        return set()
    tbl = m if isinstance(m, pa.Table) else m.to_arrow()
    rows = tbl.to_pylist()
    return {
        (r.get("__target_row_id__"), r.get("__ref_row_id__")) for r in rows
    }


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_match_df_arrow_polars_linkage_parity(shape: str) -> None:
    """Arrow-input match_df yields byte-identical linkage to polars-input."""
    pl = pytest.importorskip("polars")
    from goldenmatch import match_df

    tgt, ref = _SHAPES[shape]
    rp = match_df(pl.DataFrame(tgt), pl.DataFrame(ref))
    ra = match_df(pa.table(tgt), pa.table(ref))
    assert _pairs_from_result(ra) == _pairs_from_result(rp), (
        f"{shape}: arrow linkage {_pairs_from_result(ra)} != "
        f"polars linkage {_pairs_from_result(rp)}"
    )


_NO_POLARS_PROBE = textwrap.dedent(
    """
    import os, sys
    class _B:
        def find_spec(self, n, p=None, t=None):
            if n == "polars" or n.startswith("polars."):
                raise ImportError("polars blocked (match arrow tripwire)")
            return None
        def find_module(self, n, p=None): return None
        def load_module(self, n): raise ImportError("blocked")
    sys.meta_path.insert(0, _B())
    os.environ.update(
        GOLDENMATCH_FRAME="arrow", POLARS_SKIP_CPU_CHECK="1",
        ARROW_DEFAULT_MEMORY_POOL="system", GOLDENMATCH_AUTOCONFIG_MEMORY="0",
        GOLDENMATCH_NATIVE=os.environ.get("GOLDENMATCH_NATIVE_GATE", "0"),
    )
    import pyarrow as pa
    from goldenmatch import match_df
    tgt = pa.table({"first":["ann","bob","cara","dan","eve"],
                    "last":["smith","jones","lee","poe","adams"],
                    "email":["a@x.com","b@y.com","c@z.com","d@w.com","e@v.com"]})
    ref = pa.table({"first":["ann","cara","xavier"],
                    "last":["smith","lee","zim"],
                    "email":["a@x.com","c@z.com","x@q.com"]})
    res = match_df(tgt, ref)
    assert res.matched is not None
    assert "polars" not in sys.modules, sorted(m for m in sys.modules if "polars" in m)
    print("MATCH_NO_POLARS_OK")
    """
)


def test_match_df_arrow_is_polars_free() -> None:
    """Arrow-input match_df completes with `import polars` blocked."""
    res = subprocess.run(
        [sys.executable, "-c", _NO_POLARS_PROBE],
        capture_output=True, text=True, timeout=180,
    )
    assert res.returncode == 0 and "MATCH_NO_POLARS_OK" in res.stdout, (
        f"match arrow tripwire failed (rc={res.returncode})\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
