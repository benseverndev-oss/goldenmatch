"""TypeScript and Rust candidacy.

Each returns None -- NOT MEASURED -- when its tool is missing rather than
raising or silently reporting an empty list, so a machine without
cargo-machete distinguishes "never looked" from "looked, found nothing"
instead of collapsing both into `0`. That collapse was itself the exact
defect class this detector exists to catch: it shipped inside the detector,
where the CI runner's missing cargo-machete/ts-prune made "unused rust deps: 0"
and "unused ts exports: 0" indistinguishable from a genuine clean result.
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
    result = _run(["cargo", "machete", "--with-metadata"])
    assert result is not None
    assert result.stdout


def test_a_missing_tool_is_not_measured_not_an_empty_list(monkeypatch):
    """With no PATH at all, `cargo` and `pnpm` can't be found -- _run returns
    None -- and the public functions must surface that as None (NOT
    MEASURED), not as [] (measured, clean). [] would be indistinguishable
    from a genuine zero-findings run, which is the whole bug this test
    guards against."""
    monkeypatch.setenv("PATH", "")
    assert unused_rust_deps() is None
    assert unused_ts_exports() is None


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


def test_machete_present_reports_a_real_nonzero_count():
    """SABOTAGE CHECK for the honest/real split: with cargo-machete actually
    on PATH, unused_rust_deps() must report a real non-empty finding (this
    repo genuinely has unused Cargo dependencies right now), not an empty
    list masquerading as clean and not None masquerading as absent."""
    if shutil.which("cargo") is None or shutil.which("cargo-machete") is None:
        pytest.skip("cargo-machete not on PATH")
    result = unused_rust_deps()
    assert result is not None
    assert len(result) > 0
