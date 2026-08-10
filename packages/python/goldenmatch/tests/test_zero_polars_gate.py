"""D6 gate: the arrow lane's covered engine runs with polars NEVER imported.

Subprocess-based (the W0 lazy-import gate precedent): run an eligible
dedupe on the Frame lane (tests/_zero_polars_probe.py) and assert
``polars`` is absent from ``sys.modules``. This is the endgame's
invariant #1 arbiter -- every ``isinstance(x, pl.X)`` on a hot path
triggers the lazy proxy and fails this test until it is guarded.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_PROBE = Path(__file__).parent / "_zero_polars_probe.py"


import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _zero_polars_cases import CASES, KNOWN_POLARS_DEPENDENT  # noqa: E402


@pytest.mark.parametrize("case_name", sorted(CASES))
@pytest.mark.parametrize("native", ["0", "1"], ids=["pure", "native"])
def test_zero_polars_across_config_matrix(case_name, native):
    """Every COVERED config runs polars-free on BOTH lanes.

    One subprocess PER CASE so a leak is attributed to the exact config that
    caused it -- a single shared process would only say "something leaked".

    BOTH lanes: the default install is native-ON, and the native kernel branches
    have their own polars touchpoints (a raw isinstance in the bucket fast worker
    escaped the pure-lane-only gate).
    """
    if case_name in KNOWN_POLARS_DEPENDENT:
        pytest.skip(f"declared polars-dependent: {KNOWN_POLARS_DEPENDENT[case_name]}")

    tests_dir = Path(__file__).parent
    env = dict(os.environ)
    # PREPEND, don't clobber: a worktree run needs the sibling workspace packages
    # (goldencheck) to resolve from the worktree, not from an editable install
    # pointing at another checkout.
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [str(tests_dir.parent), str(tests_dir), env.get("PYTHONPATH", "")] if p
    )
    env["GOLDENMATCH_NATIVE_GATE"] = native
    proc = subprocess.run(
        [sys.executable, str(_PROBE), case_name],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, (
        f"case={case_name} lane={native}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    )
    assert "ZERO-POLARS OK" in proc.stdout


def test_decline_ledger_entries_are_real_cases():
    """A decline must name a case that EXISTS, else the ledger rots into a list
    of excuses for configs nobody runs."""
    unknown = set(KNOWN_POLARS_DEPENDENT) - set(CASES)
    assert not unknown, f"declined cases not in the matrix: {sorted(unknown)}"


def test_cli_import_zero_polars():
    """The CLI entry (what the web server boots through) imports with polars
    absent -- module-level pl.* literals are the W0 lesson class (static grep
    misses them; this gate is the authority)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    code = (
        "import sys\n"
        "class B:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'polars' or name.startswith('polars.'):\n"
        "            raise ImportError('blocked')\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import goldenmatch.cli.main\n"
        "import goldenmatch.web.app\n"
        "print('CLI IMPORT ZERO-POLARS OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[-2000:]}"
    assert "CLI IMPORT ZERO-POLARS OK" in proc.stdout
