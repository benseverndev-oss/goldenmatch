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


# ---------------------------------------------------------------------------
# The FILE entry point (`run_match`), ported off polars.
#
# `_run_match_pipeline` had an arrow lane the whole time; `run_match` just never
# reached it, because it built a `pl.LazyFrame` from `load_file` and handed that
# in. So `goldenmatch match` failed on a default install -- and invisibly, since
# cli/match.py catches the ImportError, prints "Runtime error: ..." and exits 3.
#
# The oracle here is the polars `match_df` lane on the same data: `run_match` no
# longer HAS a polars variant to compare against.


def _csv(tmp_path, name: str, cols: dict) -> str:
    header = ",".join(cols)
    n = len(next(iter(cols.values())))
    rows = [",".join(str(cols[c][i]) for c in cols) for i in range(n)]
    p = tmp_path / name
    p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_run_match_file_linkage_matches_the_polars_lane(shape, tmp_path) -> None:
    pl = pytest.importorskip("polars")
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.pipeline import run_match, run_match_df

    tgt, ref = _SHAPES[shape]
    t_path = _csv(tmp_path, "target.csv", tgt)
    r_path = _csv(tmp_path, "ref.csv", ref)

    from_file = run_match(
        target_file=(t_path, "target"),
        reference_files=[(r_path, "reference")],
        config=GoldenMatchConfig(),
        auto_config=True,
    )
    from_polars = run_match_df(
        pl.DataFrame(tgt), pl.DataFrame(ref), GoldenMatchConfig(), auto_config=True
    )

    def _pairs(result) -> set:
        m = result.get("matched") if isinstance(result, dict) else result.matched
        if m is None:
            return set()
        tbl = m if isinstance(m, pa.Table) else m.to_arrow()
        return {
            (r.get("__target_row_id__"), r.get("__ref_row_id__"))
            for r in tbl.to_pylist()
        }

    assert _pairs(from_file) == _pairs(from_polars)


def test_run_match_rejects_a_column_map_naming_a_missing_column(tmp_path) -> None:
    """Same contract as ingest.apply_column_map, which the seam ingest replaced:
    a map naming a column the file does not have is an error, not a silent
    no-op that leaves the matchkey pointing at nothing."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.pipeline import run_match

    t_path = _csv(tmp_path, "t.csv", {"a": [1], "b": ["x"]})
    r_path = _csv(tmp_path, "r.csv", {"a": [1], "b": ["x"]})

    with pytest.raises(ValueError, match="not in file"):
        run_match(
            target_file=(t_path, "target", {"nope": "renamed"}),
            reference_files=[(r_path, "reference")],
            config=GoldenMatchConfig(),
            auto_config=True,
        )


@pytest.mark.parametrize("zero_config", [False, True])
def test_run_match_file_is_polars_free(zero_config, tmp_path) -> None:
    """Both routes, in a SUBPROCESS with polars blocked at the meta path.

    The zero-config case is covered on purpose: the arrow lane used to exclude
    `auto_config`, so a zero-config match fell through to the polars branch --
    and `run_match_df`'s arrow path hit `combined_lf.collect()` on a pa.Table
    there.
    """
    tgt, ref = _SHAPES["exact_email"]
    t_path = _csv(tmp_path, "t.csv", tgt)
    r_path = _csv(tmp_path, "r.csv", ref)
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(
        "matchkeys:\n"
        "  - name: k\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: email\n",
        encoding="utf-8",
    )

    body = textwrap.dedent(
        f"""
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "polars" or name.startswith("polars."):
                    raise ImportError("No module named 'polars'")
                return None
        sys.meta_path.insert(0, _Block())

        from goldenmatch.core.pipeline import run_match
        zero = {zero_config!r}
        if zero:
            from goldenmatch.config.schemas import GoldenMatchConfig
            cfg, auto = GoldenMatchConfig(), True
        else:
            from goldenmatch.config.loader import load_config
            cfg, auto = load_config({str(cfg_path)!r}), False

        res = run_match(
            target_file=({t_path!r}, "target"),
            reference_files=[({r_path!r}, "reference")],
            config=cfg,
            auto_config=auto,
        )
        matched = res.get("matched")
        n = 0 if matched is None else matched.num_rows
        assert n > 0, "no matches -- a green run measuring nothing"
        assert "polars" not in sys.modules, "the match lane imported polars"
        print("MATCH POLARS-FREE OK", n)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, timeout=900
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr[-2500:]
    assert "MATCH POLARS-FREE OK" in proc.stdout
