"""PR-6 acceptance gate: zero-config / arrow ``dedupe_df`` and polars.

The autoconfig arrow-port (PR-6) flips the ``run_dedupe_df`` front-door onto the
frame seam (``cast_all_str`` + ``ensure_row_ids``, no ``.lazy()/.collect()``
polars round-trip) and widens ``dedupe_df`` / ``auto_configure_df`` to accept
``pa.Table`` / ``Frame``. Three tiers, in increasing strictness:

1. **Functional (in-process, polars present):** ``dedupe_df(pa.Table,
   config=None)`` runs zero-config to completion and returns a result whose
   dup-count matches the polars path (config-equivalence, not row-identity).

2. **Front-door polars-free (subprocess, polars import BLOCKED):** with an
   EXPLICIT config and the pure backend (``GOLDENMATCH_NATIVE=0``),
   ``dedupe_df(pa.Table, config=cfg)`` runs to completion WITHOUT importing
   polars. This is the concrete PR-6 win -- the seam front-door plus the
   pipeline's arrow lane carry a full arrow dedupe with polars absent.

3. **Zero-config polars-free (subprocess, the TRUE endgame gate):** with polars
   BLOCKED, ``dedupe_df(pa.Table, config=None)`` runs to completion. This is
   ``xfail`` today: ``auto_configure_df`` still bridges arrow -> polars for the
   controller + ``_legacy_auto_configure_v0`` heuristic (NEITHER is arrow-ported
   -- ``AutoConfigController.run``'s all-null gate subscripts ``df[col]`` and v0
   has ~15 ``df[col]`` / ``df.filter(pl.col(...))`` sites). It flips to green
   once that controller/v0 arrow port lands. See the boundary note at
   ``core/autoconfig.py`` ``auto_configure_df``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pyarrow as pa
import pytest

_PKG_ROOT = Path(__file__).parent.parent


def _native_available() -> bool:
    """Whether the native kernel is built in this environment (the endgame
    tripwire runs GOLDENMATCH_NATIVE=1, so it needs native present)."""
    try:
        from goldenmatch.core._native_loader import native_available
        return bool(native_available())
    except Exception:  # noqa: BLE001 - absent loader => treat as no native
        return False


def _person_table(n: int = 48) -> pa.Table:
    firsts = ["ann", "ann", "bob", "bobby", "cara", "dan", "dan", "eve"]
    lasts = ["smith", "smith", "jones", "jones", "lee", "poe", "poe", "ray"]
    reps = (n + len(firsts) - 1) // len(firsts)
    return pa.table(
        {
            "first": (firsts * reps)[:n],
            "last": (lasts * reps)[:n],
            "email": [f"e{i % 10}@x.com" for i in range(n)],
        }
    )


def _run_subprocess(body: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG_ROOT)
    env.update(extra_env)
    # Meta-path finder that makes `import polars` raise -- the D6 zero-polars
    # gate mechanism (see tests/_zero_polars_probe.py / test_zero_polars_gate.py).
    prelude = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('polars blocked (PR-6 tripwire)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


# -- Tier 1: functional (polars present) ------------------------------------


def test_zero_config_dedupe_df_arrow_functional():
    """`dedupe_df(pa.Table, config=None)` runs zero-config to completion and
    returns a result whose dup-count matches the polars path (arrow-vs-polars
    config-equivalence). Runs with polars present; native disabled for the box."""
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    import polars as pl
    from goldenmatch import dedupe_df

    tbl = _person_table(48)
    res_arrow = dedupe_df(tbl, config=None)
    assert res_arrow is not None
    # A result frame is produced (dupes may be empty but must not be None-crash).
    arrow_dupes = res_arrow.dupes.num_rows if res_arrow.dupes is not None else 0

    res_pl = dedupe_df(pl.from_arrow(tbl), config=None)
    pl_dupes = res_pl.dupes.num_rows if res_pl.dupes is not None else 0

    # Config-equivalence, not row-identity: the same zero-config decisions on the
    # same data must find the same number of duplicate rows on both backends.
    assert arrow_dupes == pl_dupes


def test_auto_configure_df_accepts_arrow_table():
    """`auto_configure_df(pa.Table)` no longer raises TypeError (the pre-PR-6
    ArrowFrame-only shim rejected a bare pa.Table) and returns a config."""
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.autoconfig import auto_configure_df

    cfg = auto_configure_df(_person_table(48), _skip_finalize=True)
    assert cfg is not None
    assert cfg.get_matchkeys()  # produced at least one matchkey


def test_zero_config_arrow_with_exact_column_matches_polars():
    """Regression: `auto_configure_df(pa.Table)` on data WITH an exact-eligible
    identifier column must produce the SAME config as the equivalent
    pl.DataFrame. The eager indicator `estimate_sparse_match_signal` runs only
    when exact columns exist and used to crash on a bare pa.Table
    (`'pyarrow.lib.Table' object has no attribute 'is_empty'`), degrading arrow
    zero-config to a RED v0 fallback -- a divergence the all-fuzzy 48-row
    fixtures above never exercised. The boundary now coerces non-polars input to
    polars until the scoring lane is arrow-ported, so the two must agree."""
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df

    data = {
        "cust_id": [f"C{i:05d}" for i in range(400)],  # unique -> exact-eligible
        "first": (["ann", "bob", "cara", "dan", "eve", "fay"] * 67)[:400],
        "last": (["smith", "jones", "lee", "poe", "ray", "kim"] * 67)[:400],
        "zip": [str(10000 + (i % 50)) for i in range(400)],
    }

    def _summary(cfg):
        mks = sorted(
            (mk.type, tuple(sorted(f.field for f in (mk.fields or []) if f.field)))
            for mk in cfg.get_matchkeys()
        )
        blk = None
        if cfg.blocking:
            blk = (
                cfg.blocking.strategy,
                tuple(
                    sorted(
                        k.fields[0] if k.fields else "?"
                        for k in (cfg.blocking.keys or [])
                    )
                ),
            )
        # `backend` EXCLUDED: with GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE default-on
        # the arrow sample routes to the bucket scorer, so `backend` reads
        # "bucket" on arrow vs None on polars -- a benign difference (identical
        # clusters, #526). Everything that decides the output (mks + blocking)
        # must still agree.
        return (mks, blk)

    cfg_pl = auto_configure_df(pl.DataFrame(data), _skip_finalize=True)
    cfg_pa = auto_configure_df(pa.table(data), _skip_finalize=True)
    assert _summary(cfg_pl) == _summary(cfg_pa)


# -- Tier 2: front-door polars-free (the concrete PR-6 win) -----------------


def test_explicit_config_arrow_dedupe_is_polars_free():
    """SUBPROCESS, polars import BLOCKED: `dedupe_df(pa.Table, config=cfg)` with
    an explicit config + the pure backend runs a full arrow dedupe to completion
    WITHOUT importing polars. This proves the PR-6 front-door seam port (no
    `df.cast(...).lazy()` / `_add_row_ids` / `collect` polars round-trip) plus
    the pipeline arrow lane carry an arrow dedupe polars-free."""
    body = """
        import os
        import pyarrow as pa
        from goldenmatch import dedupe_df
        from goldenmatch.config.schemas import (
            GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
            QualityConfig, TransformConfig,
        )
        tbl = pa.table({
            "first": ["ann", "ann", "bob", "bobby", "cara"] * 4,
            "last": ["smith", "smith", "jones", "jones", "lee"] * 4,
        })
        cfg = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="k", type="exact",
                fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
            )],
            quality=QualityConfig(mode="disabled"),
            transform=TransformConfig(mode="disabled"),
        )
        res = dedupe_df(tbl, config=cfg)
        assert res is not None
        import sys
        assert "polars" not in sys.modules, "polars leaked on the arrow front-door"
        print("FRONT-DOOR POLARS-FREE OK")
    """
    proc = _run_subprocess(
        body,
        {
            "GOLDENMATCH_FRAME": "arrow",
            "GOLDENMATCH_NATIVE": "0",
            "POLARS_SKIP_CPU_CHECK": "1",
            "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "FRONT-DOOR POLARS-FREE OK" in proc.stdout


# -- Tier 3: zero-config polars-free (the TRUE endgame gate) ----------------


@pytest.mark.skipif(
    not _native_available(),
    reason=(
        "the true zero-config polars-free tripwire needs the native kernel "
        "(the subprocess runs GOLDENMATCH_NATIVE=1); skipped where native isn't "
        "built (e.g. the main python matrix)."
    ),
)
def test_zero_config_dedupe_df_is_polars_free():
    """SUBPROCESS, polars import BLOCKED: run ZERO-CONFIG `dedupe_df(pa.Table,
    config=None)` to completion WITHOUT importing polars. The whole port's
    acceptance gate -- now a REAL gate (was xfail before the arrow-native default
    landed, 2026-07-14).

    Flipped green by the W1 fixes (all merged): `auto_configure_df` stays
    arrow-native by default (GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=1); the quality
    fixer degrades to scan-only without polars (#1766); multi_pass blocking stays
    on the seam (#1767); ClusterFrames + scoring (bucket) are arrow-native. The
    transform prep + golden fallback the leak-catalog flagged have working arrow
    fallbacks, so a polars-BLOCKED zero-config dedupe runs to completion. Plan:
    docs/superpowers/plans/2026-07-14-goldenmatch-zero-config-arrow-polars-free.md."""
    body = """
        import os
        import pyarrow as pa
        from goldenmatch.core._native_loader import native_available
        assert native_available(), "native kernel unavailable -- true tripwire needs it"
        from goldenmatch import dedupe_df
        tbl = pa.table({
            "first": ["ann", "ann", "bob", "bobby", "cara", "dan", "dan", "eve"] * 4,
            "last": ["smith", "smith", "jones", "jones", "lee", "poe", "poe", "ray"] * 4,
            "email": ["e%d@x.com" % (i % 10) for i in range(32)],
        })
        res = dedupe_df(tbl, config=None)
        assert res is not None
        import sys
        assert "polars" not in sys.modules, "polars leaked on zero-config arrow path"
        print("ZERO-CONFIG POLARS-FREE OK")
    """
    proc = _run_subprocess(
        body,
        {
            "GOLDENMATCH_FRAME": "arrow",
            "GOLDENMATCH_NATIVE": "1",
            # Pin arrow-native explicitly: the whole point of this gate is the
            # arrow-native path, and _run_subprocess inherits dict(os.environ),
            # so a sibling test that left GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=0
            # (e.g. the arrow-vs-polars parity comparison) would otherwise force
            # the polars input-boundary coercion and false-fail this tripwire.
            "GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE": "1",
            "POLARS_SKIP_CPU_CHECK": "1",
            "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "ZERO-CONFIG POLARS-FREE OK" in proc.stdout
