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

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import dead_code.other_langs as other_langs  # noqa: E402
from dead_code.other_langs import (  # noqa: E402
    _parse_machete,
    _run,
    _split_ts_prune_findings,
    _ts_prune_finding_file,
    _ts_public_entry_files,
    ts_public_export_inventory,
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
    assert ts_public_export_inventory() is None


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


def test_machete_present_measures_for_real():
    """SABOTAGE CHECK for the honest/real split: with cargo-machete actually
    on PATH, unused_rust_deps() must return a real MEASURED result (a list,
    even an empty one) -- never None, which would mean the invocation
    silently failed and got misread as "measured, clean" instead of "didn't
    run". `result is not None` is the assertion this test exists for; a
    non-empty list is not, on its own, evidence the invocation worked (an
    empty list from a broken invocation would look identical).

    Originally this asserted `len(result) > 0` because the repo genuinely
    had unused dependencies at the time (the 11 findings the phase-A dead-
    code audit's Rust triage went on to resolve on 2026-09-01: 7 removed, 4
    kept `ignored` with a reason). Now that they're triaged, a real measured
    run legitimately comes back empty, so that assertion would fail on
    correctly-clean output -- hardcoding it back to `== []` would make this
    test double as an unrelated regrowth ratchet, which is
    test_no_new_dead_code.py's job, not this one's.
    """
    if shutil.which("cargo") is None or shutil.which("cargo-machete") is None:
        pytest.skip("cargo-machete not on PATH")
    result = unused_rust_deps()
    assert result is not None


def test_ts_public_entry_files_reads_the_real_exports_map():
    """Against the real package.json (no ts-prune invocation needed), the
    resolved entry-file set must contain the main barrel, the `core` and
    `node` sub-barrels, and a single-file wasm leaf entry -- not just the
    obvious `src/node/index.ts`, which is the exact hardcoding this function
    exists to avoid (other sub-entries like `./core/documents-wasm` deserve
    the same public-API treatment, and previously did not get it)."""
    entries = _ts_public_entry_files()
    assert entries is not None
    for expected in (
        "src/index.ts",
        "src/core/index.ts",
        "src/node/index.ts",
        "src/core/documentsWasm.ts",
        "src/core/sketchWasm.ts",
        "src/core/suggestWasm.ts",
    ):
        assert expected in entries, f"{expected} missing from resolved entry files"
    # Internal sub-barrels that are NOT in package.json's exports map must
    # not be swept in as public API just because they look like barrels.
    for not_public in ("src/node/connectors/index.ts", "src/node/tui/index.ts"):
        assert not_public not in entries


def test_ts_public_entry_files_none_when_package_json_missing(tmp_path, monkeypatch):
    """No package.json at all -- NOT MEASURED, not 'zero public entries'
    (which would silently reclassify every finding as actionable)."""
    monkeypatch.setattr(other_langs, "TS_ROOT", tmp_path)
    assert _ts_public_entry_files() is None


def test_ts_public_entry_files_none_when_exports_map_missing(tmp_path, monkeypatch):
    """A package.json without an `exports` map is also NOT MEASURED."""
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    monkeypatch.setattr(other_langs, "TS_ROOT", tmp_path)
    assert _ts_public_entry_files() is None


def test_ts_public_entry_files_none_when_package_json_is_invalid_json(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(other_langs, "TS_ROOT", tmp_path)
    assert _ts_public_entry_files() is None


def test_ts_public_entry_files_derives_src_path_from_dist_types_path(tmp_path, monkeypatch):
    """The dist -> src derivation: `./dist/core/foo.d.ts` becomes
    `src/core/foo.ts`, for every subpath in the exports map, not just `.`."""
    pkg = {
        "exports": {
            ".": {"types": "./dist/index.d.ts"},
            "./core": {"types": "./dist/core/index.d.ts"},
            "./core/leaf": {"types": "./dist/core/leaf.d.ts"},
            # Malformed / irrelevant entries must be skipped, not crash the resolver.
            "./no-types": {"import": "./dist/no-types.js"},
            "./not-a-dict": "./dist/oops.js",
        }
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    monkeypatch.setattr(other_langs, "TS_ROOT", tmp_path)
    assert _ts_public_entry_files() == {
        "src/index.ts",
        "src/core/index.ts",
        "src/core/leaf.ts",
    }


def test_ts_prune_finding_file_normalizes_windows_and_posix_paths():
    """ts-prune emits platform-native separators (backslash on Windows, where
    this detector was profiled; forward slash on the Linux CI runner) and a
    leading separator that must be stripped before comparing against the
    forward-slash entry-file set."""
    assert _ts_prune_finding_file("\\src\\node\\index.ts:19 - readCsv") == "src/node/index.ts"
    assert _ts_prune_finding_file("./src/node/index.ts:19 - readCsv") == "src/node/index.ts"
    assert _ts_prune_finding_file("src/node/index.ts:19 - readCsv") == "src/node/index.ts"


def test_split_ts_prune_findings_partitions_public_from_actionable():
    """The three-way split this whole fix exists for: a finding tagged
    `(used in module)` is dropped entirely; a finding in a published entry
    file goes to the public-export inventory (reported, never actioned); and
    everything else is actionable."""
    raw = "\n".join(
        [
            "\\src\\node\\index.ts:5 - publicThing",
            "\\src\\node\\index.ts:6 - alsoUsedInternally (used in module)",
            "\\src\\core\\internal.ts:10 - deadThing",
            "\\src\\core\\internal.ts:11 - liveThing (used in module)",
            "\\src\\core\\documentsWasm.ts:185 - documentsWasmAvailable",
        ]
    )
    entry_files = {"src/node/index.ts", "src/core/documentsWasm.ts"}
    actionable, inventory = _split_ts_prune_findings(raw, entry_files)
    assert actionable == ["\\src\\core\\internal.ts:10 - deadThing"]
    assert inventory == [
        "\\src\\core\\documentsWasm.ts:185 - documentsWasmAvailable",
        "\\src\\node\\index.ts:5 - publicThing",
    ]


def test_split_ts_prune_findings_empty_stdout_is_empty_not_none():
    """The split itself never returns None for either bucket -- that
    distinction belongs to `_ts_prune_split()` (tool ran or not), not the
    pure parsing/partitioning step."""
    assert _split_ts_prune_findings("", {"src/node/index.ts"}) == ([], [])
