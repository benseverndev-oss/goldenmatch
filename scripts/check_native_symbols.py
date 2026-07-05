#!/usr/bin/env python3
"""Reconcile goldenmatch host references to its native kernel against the kernel's
registered exports. Box-safe: pure source parse — no cargo build, no import.

FAIL (exit 1) if a host reference has no matching kernel export (the #688
silent-fallback class); REPORT (non-fatal) exports no host references.
Run from the repo root: python scripts/check_native_symbols.py goldenmatch"""
from __future__ import annotations
import re, sys, pathlib
from dataclasses import dataclass

REGISTRY = {
    "goldenmatch": {
        "crate_reg": ["packages/rust/extensions/native/src/lib.rs"],
        "py_root": "packages/python/goldenmatch/goldenmatch",
        "loader_tokens": ("native_module", "_ensure_native"),
        "allow": "parity/native_symbols/goldenmatch.allow",
    },
}

# wrap_pyfunction!( <optional module:: paths> <symbol> , m )   -- \s spans newlines
_WRAP = re.compile(r"wrap_pyfunction!\(\s*(?:\w+::)+(\w+)")
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


def scan_references(py_root: str, loader_tokens) -> set[str]:
    out: set[str] = set()
    for py in pathlib.Path(py_root).rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if not any(tok in text for tok in loader_tokens):
            continue
        out |= scan_file_refs(text)
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
    referenced = scan_references(spec["py_root"], spec["loader_tokens"])
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
