#!/usr/bin/env python3
"""Reconcile goldenmatch host references to its native kernel against the kernel's
registered exports. Box-safe: pure source parse — no cargo build, no import.

FAIL (exit 1) if a host reference has no matching kernel export (the #688
silent-fallback class); REPORT (non-fatal) exports no host references.
Run from the repo root: python scripts/check_native_symbols.py goldenmatch"""
from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import dataclass

# goldenpipe is intentionally NOT gated: its `goldenpipe-native` binding is a
# REFERENCE-MODE parity oracle (see goldenpipe/core/_native_loader.py) — the
# pure-Python planner (_planner_json.py) is the runtime, and the kernel exists only
# so the planner parity gate (#1424) can compare byte-identity. There are no
# host-accelerated references to reconcile; drift is caught by that parity gate.
REGISTRY = {
    "goldenmatch": {
        "crate_reg": ["packages/rust/extensions/native/src/lib.rs"],
        "py_root": "packages/python/goldenmatch/goldenmatch",
        "loader_tokens": ("native_module", "_ensure_native"),
        "idiom": "runtime",
        "allow": "parity/native_symbols/goldenmatch.allow",
    },
    "infermap": {
        # infermap-native registers `wrap_pyfunction!(self::detect_domain, m)` -- the
        # `self::` qualifier is REQUIRED for _WRAP's `(?:\w+::)+` to scan the export.
        "crate_reg": ["packages/rust/extensions/infermap-native/src/lib.rs"],
        "py_root": "packages/python/infermap/infermap",
        "loader_tokens": ("native_module",),
        "allow": "parity/native_symbols/infermap.allow",
    },
    "goldencheck": {
        "crate_reg": ["packages/rust/extensions/goldencheck-native/src/lib.rs"],
        "py_root": "packages/python/goldencheck/goldencheck",
        "loader_tokens": ("native_module", "_ensure_native"),
        "idiom": "runtime",
        "allow": "parity/native_symbols/goldencheck.allow",
    },
    "goldenanalysis": {
        "crate_reg": ["packages/rust/extensions/analysis-native/src/lib.rs"],
        "py_root": "packages/python/goldenanalysis/goldenanalysis",
        "loader_tokens": ("native_module", "_ensure_native"),
        "idiom": "runtime",
        "allow": "parity/native_symbols/goldenanalysis.allow",
    },
    "goldenflow": {
        "crate_reg": ["packages/rust/extensions/native-flow/src/lib.rs"],
        "py_root": "packages/python/goldenflow/goldenflow",
        "loader_tokens": ("native_module",),
        "idiom": "literal",
        "literal_pattern": r'"(\w+_arrow)"',
        "allow": "parity/native_symbols/goldenflow.allow",
    },
}

# wrap_pyfunction!( <optional module:: paths> <symbol> , m )   -- \s spans newlines
_WRAP = re.compile(r"wrap_pyfunction!\(\s*(?:\w+::)*(\w+)")
_BIND = re.compile(r"(\w+)\s*=\s*(?:native_module\(\)|_ensure_native\(\))(?!\s*\.)")


def parse_registrations_text(text: str) -> set[str]:
    return set(_WRAP.findall(text))


def parse_registrations(paths) -> set[str]:
    out: set[str] = set()
    for p in paths:
        out |= parse_registrations_text(pathlib.Path(p).read_text(encoding="utf-8"))
    return out


def scan_file_refs(text: str) -> set[str]:
    aliases = {r"native_module\(\)", r"_ensure_native\(\)"}
    aliases |= {re.escape(name) for name in _BIND.findall(text)}
    alt = "|".join(sorted(aliases))
    syms: set[str] = set()
    syms |= set(re.findall(rf"(?:{alt})\.(\w+)", text))
    syms |= set(re.findall(rf"getattr\(\s*(?:{alt})\s*,\s*[\"'](\w+)[\"']", text))
    syms |= set(re.findall(rf"hasattr\(\s*(?:{alt})\s*,\s*[\"'](\w+)[\"']", text))
    return syms


def scan_references_text(text: str, idiom: str = "runtime",
                         literal_pattern: str | None = None) -> set[str]:
    """One file's referenced-symbol set, by idiom. Pure (testable)."""
    if idiom == "literal":
        return set(re.findall(literal_pattern, text)) if literal_pattern else set()
    return scan_file_refs(text)


def scan_references(py_root: str, loader_tokens, idiom: str = "runtime",
                    literal_pattern: str | None = None) -> set[str]:
    out: set[str] = set()
    for py in pathlib.Path(py_root).rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if not any(tok in text for tok in loader_tokens):
            continue
        out |= scan_references_text(text, idiom, literal_pattern)
    return out


def load_allow(path: str) -> set[str]:
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


@dataclass
class Result:
    missing: set
    unwired: set


def reconcile(registered: set[str], referenced: set[str], allow: set[str]) -> Result:
    return Result(missing=referenced - registered - allow,
                  unwired=registered - referenced)


def run(package: str) -> int:
    spec = REGISTRY.get(package)
    if spec is None:
        sys.stderr.write(f"no native-symbol registry entry for '{package}'\n")
        return 2
    registered = parse_registrations(spec["crate_reg"])
    referenced = scan_references(spec["py_root"], spec["loader_tokens"],
                                 spec.get("idiom", "runtime"),
                                 spec.get("literal_pattern"))
    if not referenced:
        sys.stderr.write(f"FAIL: scanned zero kernel references for {package} — "
                         f"the reference idiom is wrong (falsely-green guard)\n")
        return 1
    res = reconcile(registered, referenced, load_allow(spec["allow"]))
    print(f"{package}: {len(registered)} registered, {len(referenced)} referenced")
    if res.unwired:
        print("unwired (exported, no host reference — informational):")
        for s in sorted(res.unwired):
            print(f"  - {s}")
    if res.missing:
        sys.stderr.write("MISSING (host references a symbol the kernel does not "
                         "export — a silent-fallback / drift bug):\n")
        for s in sorted(res.missing):
            sys.stderr.write(f"  - {s}\n")
        return 1
    print("native-symbol reconciliation OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_native_symbols.py <package>")
    raise SystemExit(run(sys.argv[1]))
