"""TypeScript and Rust candidacy.

Each returns [] when its tool is missing rather than raising, so a machine
without cargo-machete reports "nothing found" instead of failing the run -- but
the CI job installs the tools, so [] there means genuinely none.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.other_langs import (  # noqa: E402
    _parse_machete,
    _run,
    unused_rust_deps,
    unused_ts_exports,
)


def test_the_machete_invocation_actually_returns_output():
    """A wrong flag or cwd makes _run return None, which every other test would read as 'no findings'."""
    if shutil.which("cargo") is None:
        pytest.skip("cargo not on PATH")
    assert _run(["cargo", "machete", "--with-metadata"]) is not None


def test_a_missing_tool_is_empty_not_an_exception(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert unused_rust_deps() == []
    assert unused_ts_exports() == []


def test_parse_machete_real_output():
    """Test parser against verbatim real cargo-machete output with 13 findings."""
    real_output = """\
cargo-machete found the following unused dependencies in this directory:

gm-echarts-spike -- .\\packages\\python\\goldenmatch\\web\\frontend-dioxus-spike\\Cargo.toml:
	wasm-bindgen-futures
analysis-native -- .\\packages\\rust\\extensions\\analysis-native\\Cargo.toml:
	rustc-hash
goldenmatch-autoconfig-core -- .\\packages\\rust\\extensions\\autoconfig-core\\Cargo.toml:
	arrow
	rand
	rand_chacha
goldenmatch-datafusion-udf -- .\\packages\\rust\\extensions\\datafusion-udf\\Cargo.toml:
	alloc-stdlib
	arrow
	brotli-decompressor
	pyo3-build-config
goldencheck-native -- .\\packages\\rust\\extensions\\goldencheck-native\\Cargo.toml:
	rustc-hash
goldencheck-wasm -- .\\packages\\rust\\extensions\\goldencheck-wasm\\Cargo.toml:
	serde
goldenmatch-graph-layout -- .\\packages\\rust\\extensions\\graph-layout\\Cargo.toml:
	tiny-skia
goldenmatch_pg -- .\\packages\\rust\\extensions\\postgres\\Cargo.toml:
	pgrx-tests

If you believe cargo-machete has detected an unused dependency incorrectly,
you can add the dependency to the list of dependencies to ignore in the
`[package.metadata.cargo-machete]` section of the appropriate Cargo.toml.
For example:

[package.metadata.cargo-machete]
ignored = ["prost"]
"""
    result = _parse_machete(real_output)
    # Should extract the 11 unique dependencies (note: arrow and rustc-hash each appear twice)
    expected = sorted(
        {
            "wasm-bindgen-futures",
            "rustc-hash",
            "arrow",
            "rand",
            "rand_chacha",
            "alloc-stdlib",
            "brotli-decompressor",
            "pyo3-build-config",
            "serde",
            "tiny-skia",
            "pgrx-tests",
        }
    )
    assert result == expected, f"Expected {expected}, got {result}"


def test_parse_machete_empty_output():
    """Test parser with empty string."""
    assert _parse_machete("") == []


def test_parse_machete_no_unused_deps():
    """Test parser when cargo-machete reports no unused dependencies."""
    no_findings = "cargo-machete found no unused dependencies.\n"
    assert _parse_machete(no_findings) == []
