"""Linkage-parity + polars-free harness for the match pipeline arrow-flip.

Two guarantees, the eviction's own standard:
1. PARITY: arrow-input `match_df` produces byte-identical linkage (the set of
   (target_row_id, ref_row_id) matched pairs) to polars-input `match_df`, across
   diverse shapes.
2. POLARS-FREE: an arrow-input `match_df` completes in a subprocess with
   `import polars` BLOCKED (the D6 zero-polars end-state).
"""
from __future__ import annotations

import inspect
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


_EXACT_EMAIL_CFG = """\
matchkeys:
  - name: k
    type: exact
    fields:
      - field: email
"""


def _exact_cfg(tmp_path):
    from goldenmatch.config.loader import load_config

    p = tmp_path / "cfg.yaml"
    p.write_text(_EXACT_EMAIL_CFG, encoding="utf-8")
    return load_config(str(p))


def test_run_match_file_linkage_matches_the_polars_lane(tmp_path) -> None:
    """CONFIGURED match: file ingest (arrow) vs the polars `match_df` lane.

    Deliberately configured, not zero-config. An earlier version of this test
    used `auto_config=True` -- which, once zero-config was pinned back to the
    polars ingest (see below), meant BOTH sides were polars and the test
    exercised none of the port.
    """
    pl = pytest.importorskip("polars")
    from goldenmatch.core.pipeline import run_match, run_match_df

    tgt, ref = _SHAPES["exact_email"]
    t_path = _csv(tmp_path, "target.csv", tgt)
    r_path = _csv(tmp_path, "ref.csv", ref)

    from_file = run_match(
        target_file=(t_path, "target"),
        reference_files=[(r_path, "reference")],
        config=_exact_cfg(tmp_path),
    )
    from_polars = run_match_df(
        pl.DataFrame(tgt), pl.DataFrame(ref), _exact_cfg(tmp_path)
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
    assert _pairs(from_file), "no pairs -- a green run measuring nothing"


def test_run_match_rejects_a_column_map_naming_a_missing_column(tmp_path) -> None:
    """Same contract as ingest.apply_column_map, which the seam ingest replaced:
    a map naming a column the file does not have is an error, not a silent
    no-op that leaves the matchkey pointing at nothing."""
    from goldenmatch.core.pipeline import run_match

    t_path = _csv(tmp_path, "t.csv", {"a": [1], "b": ["x"]})
    r_path = _csv(tmp_path, "r.csv", {"a": [1], "b": ["x"]})

    with pytest.raises(ValueError, match="not in file"):
        run_match(
            target_file=(t_path, "target", {"nope": "renamed"}),
            reference_files=[(r_path, "reference")],
            config=_exact_cfg(tmp_path),
        )


def test_run_match_file_is_polars_free(tmp_path) -> None:
    """CONFIGURED match completes in a SUBPROCESS with polars blocked."""
    tgt, ref = _SHAPES["exact_email"]
    t_path = _csv(tmp_path, "t.csv", tgt)
    r_path = _csv(tmp_path, "r.csv", ref)
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(_EXACT_EMAIL_CFG, encoding="utf-8")

    body = textwrap.dedent(
        f"""
        import sys
        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "polars" or name.startswith("polars."):
                    raise ImportError("No module named 'polars'")
                return None
        sys.meta_path.insert(0, _Block())

        from goldenmatch.config.loader import load_config
        from goldenmatch.core.pipeline import run_match

        res = run_match(
            target_file=({t_path!r}, "target"),
            reference_files=[({r_path!r}, "reference")],
            config=load_config({str(cfg_path)!r}),
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


def test_zero_config_match_still_requires_polars() -> None:
    """A LIMITATION, pinned so it cannot be forgotten or silently widened.

    Zero-config match keeps the polars ingest on purpose. Routing it through the
    arrow ingest changed what auto-config derives its config FROM -- pyarrow's
    CSV inference plus a Utf8 cast does not produce the same strings as polars'
    scan_csv plus a Utf8 cast -- and collapsed the suggest_quality scorecard:

        orgs_hard  convergence_final_f1  0.2939 -> 0.0000
        dblp_acm   convergence_final_f1  0.7296 -> 0.5645

    with every other convergence metric drifting down too. Configured match is
    unaffected and IS polars-free (the test above).

    When arrow auto-config parity is established on that scorecard, this test is
    the thing that should fail -- delete it and re-enable the zero-config case
    above. It asserts the gate, not the ingest, so it stays honest either way.
    """
    from goldenmatch.core import pipeline

    src = inspect.getsource(pipeline.run_match)
    assert "if auto_config:" in src, (
        "run_match no longer forks on auto_config -- if arrow auto-config parity "
        "was fixed, delete this test and restore the zero-config polars-free case"
    )
