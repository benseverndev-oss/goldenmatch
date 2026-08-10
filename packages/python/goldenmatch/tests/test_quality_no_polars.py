"""Quality-fix arrow-lane polars-absent degradation (zero-config arrow eviction).

goldencheck's SCAN is arrow-native, but its fix engine (``apply_fixes``) is
polars-native (goldencheck's ``[polars]`` extra). So on the arrow lane, when
polars is not installed, ``core.quality._scan_and_fix`` keeps the native scan but
DEGRADES auto-fix to scan-only (report the detected issues, apply none) instead
of crashing. The polars-present path is byte-identical to before.

This subprocess test blocks ``import polars`` (the D6 zero-polars gate mechanism)
and asserts a dirty arrow frame runs the quality check to completion polars-free.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent


def _run_polars_blocked(body: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # PREPEND, don't clobber: a worktree run sets PYTHONPATH so the sibling
    # workspace packages (goldencheck) resolve from the worktree instead of the
    # editable install pointing at another checkout. Overwriting it silently
    # tested a stale goldencheck.
    _existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_PKG_ROOT}{os.pathsep}{_existing}" if _existing else str(_PKG_ROOT)
    )
    env["POLARS_SKIP_CPU_CHECK"] = "1"
    prelude = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('polars blocked (quality-fix gate)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_quality_fix_degrades_to_scan_only_without_polars():
    """SUBPROCESS, polars BLOCKED: run_quality_check on a DIRTY arrow frame
    (whitespace/smart-quote issues the scan flags) completes without importing
    polars -- the fix step degrades to scan-only rather than crashing."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.quality import run_quality_check
        # Leading/trailing whitespace + a smart quote -> goldencheck finds fixes,
        # which would trip the polars fix bridge if it weren't degraded.
        tbl = pa.table({
            "name": ["  Alice ", "Bob\\u2019s", "  Alice ", "Carol", "Bob\\u2019s"],
            "city": ["NYC", "LA", "NYC", "SF", "LA"],
        })
        fixed, fixes = run_quality_check(tbl, config=None)
        # Frame comes back (unchanged arrow, since fixes were skipped) + no crash.
        assert fixed is not None
        assert isinstance(fixes, list)
        assert "polars" not in sys.modules, "polars leaked in the quality path"
        print("QUALITY-NO-POLARS OK")
    """
    proc = _run_polars_blocked(body)
    assert proc.returncode == 0, f"stdout={proc.stdout}\\nstderr={proc.stderr[-2500:]}"
    assert "QUALITY-NO-POLARS OK" in proc.stdout


def test_compute_quality_scores_weights_cells_without_polars():
    """SUBPROCESS, polars BLOCKED: quality-weighted survivorship must still get
    its per-cell weights.

    ``goldencheck.cell_quality`` is arrow-native (it takes a ``pa.Table`` and its
    own docstring pins "stays polars-free"), so there is no reason for the
    weighting to fail open when polars is absent. Regression guard: the pipeline
    used to skip weighting entirely on a polars-free install, which silently
    changed golden-record field selection between `pip install goldenmatch` and
    `goldenmatch[polars]`.

    Fixture honours goldencheck's fuzzy thresholds (>= 50 rows, >= 3 distinct):
    "Californa" is a near-duplicate of the more frequent "California", so its row
    is penalized and the weight comes back keyed by ``__row_id__`` (NOT the
    positional index -- row_ids here are offset by 100 so a positional leak is
    visible)."""
    body = """
        import sys
        import pyarrow as pa
        from goldenmatch.core.quality import compute_quality_scores

        n_cal, n_tex, n_nev = 30, 20, 9
        city = ["California"] * n_cal + ["Texas"] * n_tex + ["Nevada"] * n_nev + ["Californa"]
        row_ids = [100 + i for i in range(len(city))]
        tbl = pa.table({"__row_id__": pa.array(row_ids, type=pa.int64()), "city": city})

        scores = compute_quality_scores(tbl)
        assert scores is not None, "quality weighting failed open with polars absent"
        typo_row_id = row_ids[-1]
        assert (typo_row_id, "city") in scores, f"typo row not penalized; got {scores}"
        assert scores[(typo_row_id, "city")] < 1.0
        # Keys must be row_ids, not positional indices (positional would be 59).
        assert (len(city) - 1, "city") not in scores, "scores keyed positionally, not by __row_id__"
        assert "polars" not in sys.modules, "polars leaked in the quality path"
        print("QUALITY-WEIGHTS-NO-POLARS OK")
    """
    proc = _run_polars_blocked(body)
    assert proc.returncode == 0, f"stdout={proc.stdout}\\nstderr={proc.stderr[-2500:]}"
    assert "QUALITY-WEIGHTS-NO-POLARS OK" in proc.stdout


def test_pipeline_engages_quality_weighting_without_polars():
    """SUBPROCESS, polars BLOCKED, END-TO-END: a default-config dedupe must
    actually CALL quality weighting, not silently skip it.

    ENGAGE PROBE (green harness != lane exercised): the pipeline used to zero out
    the member list when polars was absent, so ``compute_quality_scores`` was
    never invoked and survivorship quietly took the unweighted path. Asserting
    only on output would pass by luck on a tie-break; this spies the call.

    60 rows = 30 exact-match pairs, so every row is a multi-member cluster member
    and the scoped frame clears goldencheck's >= 50-row fuzzy threshold. One pair
    disagrees on city ("California" vs the "Californa" typo), which is the signal
    weighting exists to resolve."""
    body = """
        import sys
        import pyarrow as pa
        import goldenmatch.core.quality as Q

        calls = []
        _real = Q.compute_quality_scores

        def _spy(df, *a, **kw):
            out = _real(df, *a, **kw)
            calls.append(out)
            return out

        Q.compute_quality_scores = _spy

        import goldenmatch.core.pipeline as P
        from goldenmatch.config.schemas import (
            GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
            QualityConfig, TransformConfig,
        )

        names, cities = [], []
        for i in range(30):
            if i < 15:      city_a = city_b = "California"
            elif i < 24:    city_a = city_b = "Texas"
            elif i < 29:    city_a = city_b = "Nevada"
            else:           city_a, city_b = "Californa", "California"  # typo first
            names += [f"person{i}", f"person{i}"]
            cities += [city_a, city_b]
        tbl = pa.table({"name": names, "city": cities})

        cfg = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="k", type="exact", fields=[MatchkeyField(field="name")],
            )],
            quality=QualityConfig(mode="disabled"),
            transform=TransformConfig(mode="disabled"),
        )
        res = P.run_dedupe_df(tbl, cfg)

        assert calls, "compute_quality_scores was NEVER called (weighting skipped polars-free)"
        assert any(c for c in calls), f"weighting ran but produced no scores: {calls}"
        assert "polars" not in sys.modules, "polars leaked in the dedupe path"
        print("PIPELINE-WEIGHTING-NO-POLARS OK")
    """
    proc = _run_polars_blocked(body)
    assert proc.returncode == 0, f"stdout={proc.stdout}\\nstderr={proc.stderr[-2500:]}"
    assert "PIPELINE-WEIGHTING-NO-POLARS OK" in proc.stdout
