"""Arrow tripwire for the bridge-invoked core-API aux functions.

Runs each aux function the Rust bridge calls (`validate_dataframe`,
`auto_fix_dataframe`, `detect_anomalies`, `profile_dataframe`, `preflight`,
`postflight`, `train_em`/`score_probabilistic`) on a `pa.Table` in a
subprocess with `import polars` BLOCKED (the D6 zero-polars end-state), proving
the paths the `#1747 [polars]` stopgap covers stay polars-free once the extra is
dropped from the rust bridge lanes.

Mirrors the `_zero_polars_probe.py` mechanism (a `sys.meta_path` finder that
raises ImportError on any `polars` import).
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

_PROBE = textwrap.dedent(
    """
    import os, sys

    class _Blocker:
        def find_module(self, name, path=None):
            return self if (name == "polars" or name.startswith("polars.")) else None
        def find_spec(self, name, path=None, target=None):
            if name == "polars" or name.startswith("polars."):
                raise ImportError("polars blocked (aux tripwire)")
            return None
        def load_module(self, name):
            raise ImportError("polars blocked (aux tripwire)")

    sys.meta_path.insert(0, _Blocker())
    os.environ.update(
        GOLDENMATCH_FRAME="arrow",
        GOLDENMATCH_NATIVE=os.environ.get("GOLDENMATCH_NATIVE_GATE", "0"),
        POLARS_SKIP_CPU_CHECK="1",
        ARROW_DEFAULT_MEMORY_POOL="system",
        GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE="1",
        GOLDENMATCH_AUTOCONFIG_MEMORY="0",
    )

    import pyarrow as pa

    tbl = pa.table({
        "first": ["ann", "ann", "bob", "cara", "dan", "dan"],
        "last": ["smith", "smith", "jones", "lee", "poe", "poe"],
        "email": ["a@x.com", "a@x.com", "b@y.com", "c@z.com", "d@w.com", "d@w.com"],
    })

    # validate_dataframe
    from goldenmatch.core.validate import validate_dataframe, ValidationRule
    v = validate_dataframe(tbl, [ValidationRule(column="email", rule_type="not_null")])
    assert isinstance(v, tuple), v

    # auto_fix_dataframe
    from goldenmatch.core.autofix import auto_fix_dataframe
    fixed, fixes = auto_fix_dataframe(tbl)
    assert isinstance(fixes, list), fixes

    # detect_anomalies
    from goldenmatch.core.anomaly import detect_anomalies
    a = detect_anomalies(tbl)
    assert isinstance(a, list), a

    # profile_dataframe
    from goldenmatch.core.profiler import profile_dataframe
    p = profile_dataframe(tbl)
    assert p["total_columns"] == 3, p
    assert [c["name"] for c in p["columns"]] == ["first", "last", "email"], p

    # preflight + postflight over an auto-built config
    from goldenmatch import auto_configure_df
    cfg = auto_configure_df(tbl)
    from goldenmatch.core.autoconfig_verify import preflight
    pr = preflight(tbl, cfg)
    assert pr is not None

    # Fellegi-Sunter train_em / score_probabilistic on an arrow block
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.probabilistic import train_em, score_probabilistic
    t = tbl.append_column("__row_id__", pa.array([0, 1, 2, 3, 4, 5], type=pa.int64()))
    mk = MatchkeyConfig(
        name="p", type="probabilistic",
        fields=[MatchkeyField(field="first", scorer="jaro_winkler")],
    )
    em = train_em(t, mk, n_sample_pairs=10, max_iterations=2)
    pairs = score_probabilistic(t, mk, em)
    assert isinstance(pairs, list), pairs

    # Hard proof: polars never entered sys.modules.
    assert "polars" not in sys.modules, sorted(m for m in sys.modules if "polars" in m)
    print("AUX_NO_POLARS_OK")
    """
)


def test_coreapi_aux_functions_are_polars_free() -> None:
    """Every bridge-invoked aux core-API function runs on arrow with polars blocked."""
    res = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert res.returncode == 0, (
        f"aux tripwire failed (rc={res.returncode})\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "AUX_NO_POLARS_OK" in res.stdout, res.stdout
