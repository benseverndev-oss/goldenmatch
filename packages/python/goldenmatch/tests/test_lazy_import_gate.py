# tests/test_lazy_import_gate.py
"""THE W0 gate: `import goldenmatch` must not load Polars.

Subprocess-based so this test is immune to other tests having already imported
polars into this process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def test_import_goldenmatch_does_not_load_polars():
    code = (
        "import json, sys\n"
        "import goldenmatch\n"
        "print(json.dumps({'polars_loaded': 'polars' in sys.modules,"
        " 'goldenmatch_file': goldenmatch.__file__}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ},
        check=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["polars_loaded"] is False, (
        f"polars was imported eagerly by `import goldenmatch` "
        f"(package at {payload['goldenmatch_file']}). Run "
        f"`python -X importtime -c 'import goldenmatch' 2>&1 | grep polars` "
        f"to find the offender."
    )


def test_polars_still_works_after_lazy_import():
    """The proxy must not break real use: first pl. access imports polars fine."""
    code = (
        "import sys\n"
        "import goldenmatch\n"
        "from goldenmatch._polars_lazy import pl\n"
        "df = pl.DataFrame({'a': [1]})\n"
        "assert 'polars' in sys.modules\n"
        "assert df.height == 1\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env={**os.environ}, check=True,
    )
    assert proc.stdout.strip().endswith("OK")
