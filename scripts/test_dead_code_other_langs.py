"""TypeScript and Rust candidacy.

Each returns [] when its tool is missing rather than raising, so a machine
without cargo-machete reports "nothing found" instead of failing the run -- but
the CI job installs the tools, so [] there means genuinely none.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.other_langs import (  # noqa: E402
    unused_rust_deps,
    unused_ts_exports,
    unwired_rust_exports,
)


def test_all_three_return_lists_of_strings():
    for fn in (unused_rust_deps, unwired_rust_exports, unused_ts_exports):
        out = fn()
        assert isinstance(out, list)
        assert all(isinstance(x, str) for x in out)


def test_a_missing_tool_is_empty_not_an_exception(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert unused_rust_deps() == []
    assert unused_ts_exports() == []
