"""Pure-Python fallbacks that no test executes with the native kernel off.

goldenflow's 108 `_py` functions and goldenmatch's 9 are DELIBERATE duplication
-- a supported execution mode (GOLDENFLOW_NATIVE=0), not dead code. The risk is
drift between two live implementations, and drift is only caught where a test
actually runs the pure path. This reports the ones nothing runs.

Measures EXECUTION, never mention: a function named in a test file but never
called is unguarded.
"""

from __future__ import annotations

import argparse
import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = (
    REPO / "packages" / "python" / "goldenflow" / "goldenflow",
    REPO / "packages" / "python" / "goldenmatch" / "goldenmatch",
)


def _py_function_spans() -> dict[str, list[tuple[str, int, int]]]:
    """Map a module's posix path suffix to its `_py` functions and line spans."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for root in PACKAGES:
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
            except SyntaxError:
                continue
            spans = [
                (n.name, n.lineno, n.end_lineno or n.lineno)
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.endswith("_py")
            ]
            if spans:
                out[path.as_posix()] = spans
    return out


def _load_executed(native_off_xml: Path) -> dict[str, set[int]]:
    """Parse a coverage.xml into {filename: {executed line numbers}}.

    A class with zero `<line>` elements at all (no per-line data recorded for
    that file) is dropped rather than treated as "every line unexecuted" --
    those are two different facts and conflating them misreports a module the
    run never touched as one whose functions were all confirmed unguarded.
    """
    if not native_off_xml.exists():
        raise FileNotFoundError(f"coverage report missing: {native_off_xml}")
    root = ET.parse(native_off_xml).getroot()
    executed: dict[str, set[int]] = {}
    for cls in root.iter("class"):
        name = (cls.get("filename") or "").replace("\\", "/")
        all_lines = list(cls.iter("line"))
        if not all_lines:
            continue
        hits = {int(ln.get("number", "0")) for ln in all_lines if int(ln.get("hits", "0")) > 0}
        executed.setdefault(name, set()).update(hits)
    return executed


def _match(mod_path: str, executed: dict[str, set[int]]) -> str | None:
    """The one `executed` filename that corresponds to `mod_path`, or None.

    More than one candidate is an error, not a pick-the-first: a short or
    source-relative `filename` in the XML would suffix-match any absolute
    path ending the same way, silently attributing one module's coverage to
    another module's functions. That is worse than no answer.
    """
    matches = sorted(k for k in executed if mod_path.endswith(k) or k.endswith(mod_path))
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"ambiguous coverage match for {mod_path!r}: candidates {matches}")
    return matches[0]


def unguarded_py_functions(
    native_off_xml: Path,
    spans: dict[str, list[tuple[str, int, int]]] | None = None,
) -> list[str]:
    """`module::function` for every `_py` function with no executed line.

    `spans` is injectable so the unit is testable against synthetic data. A
    version that could only be exercised against the real tree would be checked
    by nobody, and its silence would have to be trusted.

    A module with no matching coverage data at all is silently excluded from
    this list -- that is "no evidence either way", not "guarded". Use
    `modules_without_coverage_data` to see which modules that happened to.
    """
    if spans is None:
        spans = _py_function_spans()
    executed = _load_executed(native_off_xml)

    out: list[str] = []
    for mod_path, fn_spans in spans.items():
        match = _match(mod_path, executed)
        if match is None:
            continue
        ran = executed[match]
        for fn, start, end in fn_spans:
            if not any(start <= n <= end for n in ran):
                out.append(f"{match}::{fn}")
    return sorted(out)


def modules_without_coverage_data(
    native_off_xml: Path,
    spans: dict[str, list[tuple[str, int, int]]] | None = None,
) -> list[str]:
    """`spans` modules with no matching `<class>` in the coverage XML at all.

    Distinct from a module that matched and had every function's lines
    unexecuted: that case is a real, reportable finding from
    `unguarded_py_functions`. This case means the run produced no evidence at
    all for the module -- e.g. a whole package excluded from that run's
    `--source`. Silently dropping it (as `unguarded_py_functions` must, since
    it has nothing to report) would make the tool read as "fewer unguarded
    functions" or "zero", for a reason having nothing to do with coverage.
    """
    if spans is None:
        spans = _py_function_spans()
    executed = _load_executed(native_off_xml)
    return sorted(mod for mod in spans if _match(mod, executed) is None)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--native-off-xml", type=Path, required=True)
    ap.add_argument(
        "--max-no-data",
        type=int,
        default=None,
        help=(
            "Fail if more than this many modules have no coverage data at "
            "all. A no-data count above this floor doesn't mean more code "
            "went unguarded -- it means the measurement didn't happen for "
            "those modules (suite died early, a collection error truncated "
            "it, the runner OOMed), which makes the whole report "
            "untrustworthy. Unset by default so existing callers are "
            "unaffected."
        ),
    )
    args = ap.parse_args(argv)
    spans = _py_function_spans()
    items = unguarded_py_functions(args.native_off_xml, spans=spans)
    gaps = modules_without_coverage_data(args.native_off_xml, spans=spans)
    print(f"{len(items)} `_py` function(s) executed by no test with native off")
    for i in items:
        print(f"   {i}")
    print(f"{len(gaps)} module(s) had no coverage data at all (not counted above)")
    for g in gaps:
        print(f"   {g}")
    if args.max_no_data is not None and len(gaps) > args.max_no_data:
        print(
            f"FAIL: {len(gaps)} module(s) had no coverage data, exceeding "
            f"--max-no-data {args.max_no_data} -- the coverage run is "
            f"incomplete or mis-scoped, not just 'more unguarded code'"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
