from __future__ import annotations

import os
import subprocess
import sys


def test_synonym_registered_on_package_import():
    # Clean subprocess: `import goldenmatch` alone must register `synonym`
    # (proves package-init registration, not just importing the subpackage).
    code = (
        "import goldenmatch;"
        "from goldenmatch.plugins.registry import PluginRegistry;"
        "s=PluginRegistry.instance().get_scorer('synonym');"
        "assert s is not None and type(s).__name__=='SynonymScorer', repr(s)"
    )
    env = {**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "GOLDENMATCH_ANALYTICS": "0"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


def test_score_field_dispatches_synonym():
    import goldenmatch  # noqa: F401 - triggers registration
    from goldenmatch.core.scorer import score_field

    v = score_field("Advil", "Advel", "synonym")
    assert isinstance(v, float)


def test_valid_scorers_unchanged():
    from goldenmatch.config.schemas import VALID_SCORERS

    assert "synonym" not in VALID_SCORERS
