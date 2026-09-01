"""The one rule both polars-free sweeps use to recognise a missing polars.

There are two sweeps -- `sweep_cli_polars_free.py` over the 66 CLI commands and
`sweep_mcp_polars_free.py` over the 97 MCP tools -- and each independently
started by recognising a RAISED ImportError and nothing else. That is a rule
about error-handling STYLE, not about the dependency:

* MCP tools wrap their body in a broad `except Exception` and return
  `{"error": str(exc)}`. `read_file` answered "Could not parse ...: No module
  named 'polars'" and the sweep scored it `ok`.
* CLI commands can catch, print and exit 0 for the same effect.

Both sweeps then reported a clean ZERO while a polars-bound surface sat in
plain sight. So the rule lives in one place, is applied to raised exceptions AND
to returned/printed output, and has its own tests -- a sweep is only ever as
honest as its classifier.
"""

from __future__ import annotations


def looks_like_polars_import_error(blob: str) -> bool:
    """Does this text carry the interpreter's missing-polars message?

    Deliberately NOT matched: a message that names an extra to install
    (`pip install goldenflow[polars]`). That is a documented capability limit
    raised on purpose, and the sweeps classify it separately as `needs_extra`.
    """
    b = blob.lower()
    return "no module named 'polars'" in b or 'no module named "polars"' in b
