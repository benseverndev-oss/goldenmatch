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


# -- Tier 4: the FILE front-door (what `pip install goldenmatch` actually runs) --


def test_auto_configure_files_is_polars_free(tmp_path):
    """SUBPROCESS, polars import BLOCKED: `auto_configure([(csv, name)])` runs to
    completion WITHOUT importing polars.

    Tier 3 above proves the *DataFrame* front-door, and only when the native
    kernel is built. Neither holds for a plain ``pip install goldenmatch``: there
    is no native wheel and no polars (it is an optional extra), and the first
    thing the CLI does with a file is ``auto_configure(files)``. That ingest was
    the last polars island on the zero-config path -- ``pl.read_csv`` /
    ``pl.read_excel`` / ``pl.read_parquet`` + ``pl.concat`` -- so
    ``goldenmatch dedupe customers.csv`` exited 1 with "Auto-config error: No
    module named 'polars'" for every user who installed the documented way.

    Deliberately runs ``GOLDENMATCH_NATIVE=0``: the pure-Python backend is what a
    wheel-only install gets, so the gate has to hold without the kernel.
    """
    csv = tmp_path / "customers.csv"
    rows = ["first,last,email,zip"]
    for i in range(120):
        f = ["ann", "ann", "bob", "bobby", "cara", "dan"][i % 6]
        last = ["smith", "smith", "jones", "jones", "lee", "poe"][i % 6]
        rows.append(f"{f},{last},{f}.{last}@x.com,{10000 + (i % 30)}")
    csv.write_text("\n".join(rows), encoding="utf-8")

    body = f"""
        import sys
        from goldenmatch.core.autoconfig import auto_configure
        cfg = auto_configure([({str(csv)!r}, "customers")])
        assert cfg is not None
        assert cfg.get_matchkeys(), "auto_configure produced no matchkeys"
        assert "polars" not in sys.modules, "polars leaked on the file front-door"
        print("FILE FRONT-DOOR POLARS-FREE OK")
    """
    proc = _run_subprocess(
        body,
        {
            "GOLDENMATCH_FRAME": "arrow",
            "GOLDENMATCH_NATIVE": "0",
            "GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE": "1",
            "POLARS_SKIP_CPU_CHECK": "1",
            "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-2500:]}"
    assert "FILE FRONT-DOOR POLARS-FREE OK" in proc.stdout


def test_auto_configure_files_matches_polars_ingest(tmp_path):
    """The Arrow ingest must not change what auto-config DECIDES.

    Config-equivalence (matchkeys + blocking), not row-identity: the same CSV
    read through the arrow reader and through the legacy polars reader must
    produce the same auto-config decisions. Guards the swap in
    ``_read_autoconfig_input``.
    """
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.io_arrow import read_table_arrow

    csv = tmp_path / "c.csv"
    rows = ["first,last,email,zip"]
    for i in range(200):
        f = ["ann", "bob", "cara", "dan", "eve"][i % 5]
        last = ["smith", "jones", "lee", "poe", "ray"][(i // 5) % 5]
        rows.append(f"{f},{last},{f}.{last}@x.com,{10000 + (i % 40)}")
    csv.write_text("\n".join(rows), encoding="utf-8")

    def _summary(cfg):
        mks = sorted(
            (mk.type, tuple(sorted(fl.field for fl in (mk.fields or []) if fl.field)))
            for mk in cfg.get_matchkeys()
        )
        blk = None
        if cfg.blocking:
            blk = (
                cfg.blocking.strategy,
                tuple(sorted(k.fields[0] if k.fields else "?" for k in (cfg.blocking.keys or []))),
            )
        return (mks, blk)

    cfg_arrow = auto_configure_df(read_table_arrow(csv, encoding="utf8-lossy"), _skip_finalize=True)
    cfg_polars = auto_configure_df(
        pl.read_csv(csv, encoding="utf8-lossy", infer_schema_length=10000, ignore_errors=True),
        _skip_finalize=True,
    )
    assert _summary(cfg_arrow) == _summary(cfg_polars)


# -- Tier 5: the whole CLI, polars-free (what the README tells people to run) --


def test_cli_zero_config_dedupe_is_polars_free(tmp_path):
    """SUBPROCESS, polars import BLOCKED: the documented first command --
    ``goldenmatch dedupe <csv> --output-clusters`` -- runs to completion,
    exits 0 and WRITES its output, with no polars and no native kernel.

    This is the end-to-end version of the tiers above and the one that matches
    what a reader of the README actually types after ``pip install goldenmatch``.
    Three separate leaks sat on it: the ``auto_configure`` file ingest, the
    ``_preflight_report`` decline that pinned every zero-config run to the
    classic polars lane, and the csv writer.
    """
    csv = tmp_path / "customers.csv"
    rows = ["first,last,email,zip"]
    for i in range(150):
        f = ["ann", "ann", "bob", "bobby", "cara", "dan"][i % 6]
        last = ["smith", "smith", "jones", "jones", "lee", "poe"][i % 6]
        rows.append(f"{f},{last},{f}.{last}@x.com,{10000 + (i % 30)}")
    csv.write_text("\n".join(rows), encoding="utf-8")
    outdir = tmp_path / "out"

    body = f"""
        import sys
        from typer.testing import CliRunner
        from goldenmatch.cli.main import app

        result = CliRunner().invoke(
            app,
            ["dedupe", {str(csv)!r}, "--output-clusters",
             "--output-dir", {str(outdir)!r}, "--run-name", "tw"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output[-3000:]
        assert "polars" not in sys.modules, "polars leaked on the CLI zero-config path"
        import os
        written = sorted(os.listdir({str(outdir)!r}))
        assert any(f.endswith("_clusters.csv") for f in written), written
        print("CLI ZERO-CONFIG POLARS-FREE OK")
    """
    proc = _run_subprocess(
        body,
        {
            "GOLDENMATCH_FRAME": "arrow",
            "GOLDENMATCH_NATIVE": "0",
            "GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE": "1",
            "POLARS_SKIP_CPU_CHECK": "1",
            "GOLDENMATCH_AUTOCONFIG_MEMORY": "0",
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-3000:]}"
    assert "CLI ZERO-CONFIG POLARS-FREE OK" in proc.stdout


# -- Tier 6: NE on an exact matchkey, polars-free ---------------------------


def _customers_csv_with_id(path, n_identities: int = 60) -> None:
    """A csv shaped like the one a reader actually has: a unique ``id``, an
    ``email`` that IS an identifier, and near-duplicate rows.

    Two properties are load-bearing, and both were learned the hard way:

    * **Emails must be mostly distinct.** Auto-config only builds an exact
      matchkey on a column that clears an identifier-uniqueness guard
      (``cardinality_ratio >= 0.50`` for ``col_type=email``), and it only
      promotes NEGATIVE EVIDENCE onto a matchkey that exists. An earlier
      version of this fixture cycled first and last names in lockstep, which
      produced ~9 distinct emails over 135 rows (ratio 0.0667); auto-config
      excluded every exact-eligible column and fell back to fuzzy-only, so the
      gate went vacuous. Here each identity gets its own email and only some are
      duplicated, giving ratio ~0.67 -- the guard wants >= 0.50, and 1.0 fails
      too (a perfectly-unique column is a surrogate key, not shared identity).
    * **``id`` stays perfectly unique.** It is excluded as an exact matchkey
      (a surrogate key has no shared identity to match on) but it is exactly
      what NE gets promoted ON.

    Tier 5's fixture has no id column at all, which is why tier 5 ran the whole
    CLI polars-free and still missed the leak in
    ``_apply_negative_evidence_to_exact_pairs``: the code path was covered, the
    config shape that path generates on ordinary data was not.
    """
    firsts = ["Jonathan", "Katherine", "Michael", "Elizabeth", "Robert", "Amara",
              "Priya", "Tomasz", "Yusuf", "Ingrid"]
    lasts = ["Okafor", "Nakamura", "Silva", "Petrov", "Andersen", "Rahman",
             "Dubois", "Kowalski", "Mbeki", "Lindqvist"]
    cities = ["Leeds", "Bristol", "Cardiff", "Dundee"]
    rows = ["id,first_name,last_name,email,city"]
    rid = 0
    for i in range(n_identities):
        f = firsts[i % len(firsts)]
        l = lasts[(i // len(firsts)) % len(lasts)]
        email = f"{f.lower()}.{l.lower()}{i}@example.com"
        city = cities[i % len(cities)]
        rid += 1
        rows.append(f"{rid},{f},{l},{email},{city}")
        if i % 2 == 0:  # near-duplicate: same identity, noisier spelling
            rid += 1
            # The email is repeated VERBATIM: it is the identity the exact
            # matchkey joins on, and case-mangling it would make every address
            # distinct -- which pushes cardinality_ratio to 1.0 and gets the
            # column excluded as a perfectly-unique surrogate key, the same
            # guard that excludes `id`. The noise belongs in the other columns.
            rows.append(f"{rid},{f.upper()},{l},{email},{city.upper()}")
    path.write_text(chr(10).join(rows), encoding="utf-8")


def test_cli_zero_config_with_negative_evidence_is_polars_free(tmp_path):
    """SUBPROCESS, polars BLOCKED: the documented first command on a csv that
    HAS an id column -- so auto-config promotes negative evidence onto the exact
    matchkey -- runs to completion and writes its clusters.

    Regression gate for the leak 3.17.0 shipped: NE on an exact matchkey went
    through ``_as_polars_df``, so on a polars-free install every auto-config
    controller iteration errored and the final pipeline raised
    ``ModuleNotFoundError: No module named 'polars'`` (exit 3) -- on the exact
    command the README, the docs site and the PyPI page all tell people to run.

    The test asserts NE is ACTUALLY ACTIVE in the committed config, not just
    that the run exits 0. Without that assertion the fixture could drift back to
    a shape that never promotes NE and the gate would silently stop testing
    anything -- which is precisely how this escaped the first time.
    """
    csv = tmp_path / "customers.csv"
    _customers_csv_with_id(csv)
    outdir = tmp_path / "out"

    csv_repr = repr(str(csv))
    body = f"""
        import os, sys
        from typer.testing import CliRunner
        from goldenmatch.cli.main import app

        # PRECONDITION, checked BEFORE the run: this fixture must still produce
        # a config with negative evidence on the exact matchkey, or the gate
        # below proves nothing. Checked first because the CLI run leaves
        # process state behind that changes what a later auto_configure returns.
        from goldenmatch.core.autoconfig import auto_configure
        _cfg = auto_configure([({csv_repr}, "customers")])
        assert any(
            getattr(mk, "negative_evidence", None) for mk in _cfg.matchkeys
        ), (
            "fixture no longer promotes negative evidence -- gate is now vacuous. "
            f"matchkeys={{[(m.name, len(m.negative_evidence or [])) for m in _cfg.matchkeys]}}"
        )
        assert "polars" not in sys.modules, "polars leaked in auto_configure"

        result = CliRunner().invoke(
            app,
            ["dedupe", {str(csv)!r}, "--output-clusters",
             "--output-dir", {str(outdir)!r}, "--run-name", "ne"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output[-4000:]
        assert "polars" not in sys.modules, "polars leaked on the NE exact-matchkey path"

        written = sorted(os.listdir({str(outdir)!r}))
        assert any(f.endswith("_clusters.csv") for f in written), written

        print("CLI ZERO-CONFIG NE POLARS-FREE OK")
    """
    # Deliberately NOT the tier-5 env. GOLDENMATCH_AUTOCONFIG_MEMORY=0 and
    # GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=1 make auto-config drop the
    # `exact_email` matchkey entirely, and with it the negative evidence this
    # gate exists to exercise (measured on this fixture: matchkeys go from
    # [exact_email (1 NE), fuzzy_match] to just [fuzzy_match]). Setting them
    # here would leave the test green against the very bug it is written for.
    proc = _run_subprocess(
        body,
        {
            "GOLDENMATCH_FRAME": "arrow",
            "GOLDENMATCH_NATIVE": "0",
            "POLARS_SKIP_CPU_CHECK": "1",
            # Isolate the CROSS-RUN auto-config memory
            # (`~/.goldenmatch/autoconfig_memory.db`, keyed by data shape, with
            # no env override -- so redirecting home is the only lever). Without
            # this the gate reads a config REMEMBERED from an earlier run on a
            # similarly-shaped csv instead of deriving one from this fixture:
            # it passed on a developer box with a warm store and failed in CI
            # with a cold one, which is a false green in the direction that
            # matters.
            "HOME": str(tmp_path / "home"),
            "USERPROFILE": str(tmp_path / "home"),
            # Set EXPLICITLY, never left to inherit. `_run_subprocess` copies
            # `os.environ`, and `test_zero_config_dedupe_df_arrow_functional`
            # earlier in this file does
            # `os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")` in
            # the PARENT pytest process -- so running this test after that one
            # silently handed it the one value that drops the exact matchkey,
            # and whether this gate tested anything came down to test ORDER.
            "GOLDENMATCH_AUTOCONFIG_MEMORY": "1",
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr[-4000:]}"
    assert "CLI ZERO-CONFIG NE POLARS-FREE OK" in proc.stdout
