"""Strict functional-dependency discovery (cross-column relation profiler).

Reports single-column **strict** functional dependencies that hold on the
scanned data: ``det -> dep`` means every value of ``det`` maps to exactly one
value of ``dep``, so ``dep`` is redundant given ``det`` (a lookup / derived
relationship -- e.g. ``zip -> city``, ``dept_id -> dept_name``). This is
distinct from the *approximate* FD mining in ``baseline/constraints.py``
(confidence < 1.0, opt-in via ``create_baseline``); here we surface only exact
dependencies, in the normal scan path, as INFO.

Strict FD `det -> dep` holds iff ``n_distinct(det, dep) == n_distinct(det)``.
When ``goldencheck[native]`` is installed, discovery runs in the Rust kernel
(``discover_functional_dependencies`` -- interns each column once, reuses it
across every pair, and early-exits on the first violation, which beats Polars
recomputing a full two-column distinct per pair). The fallback (no native wheel)
is pure Python — ``set``-based distinct counts, no Polars engine. Both are
integer-exact -> identical results.

Guards against noise: needs >= _MIN_ROWS rows of support, skips constant
dependents and unique determinants (trivial), caps candidate columns, and
reports a bounded number of findings.
"""
from __future__ import annotations

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity

_MIN_ROWS = 50          # enough support that a strict FD isn't a small-sample fluke
_MAX_CANDIDATES = 12    # bound the O(k^2) pair space
_MAX_FINDINGS = 10

_SUPPORTED = frozenset({"str", "int", "uint", "bool"})


def _select_candidates(frame, n_rows: int) -> list[str]:
    """Supported-dtype, non-constant columns; lowest-cardinality first (the
    interesting determinants), capped."""
    frame = to_frame(frame)
    scored: list[tuple[int, str]] = []
    for col in frame.columns:
        series = frame.column(col)
        if series.dtype not in _SUPPORTED:
            continue
        nu = series.n_unique()
        if nu <= 1:
            continue
        scored.append((nu, col))
    scored.sort(key=lambda t: t[0])
    return [c for _nu, c in scored[:_MAX_CANDIDATES]]


def _discover_strict_ids(cols_values: list[list], n_rows: int) -> list[tuple[int, int]]:
    """List-based, Polars-free strict FD discovery: ``det -> dep`` holds iff
    ``n_distinct(det, dep) == n_distinct(det)``. Returns ``(det_idx, dep_idx)``
    pairs into ``cols_values``; skips trivial pairs (unique determinant, constant
    dependent) identically to the kernel.

    This is the shared compute core: the frame-based :func:`_discover_python`
    below delegates to it after pulling columns to lists, and
    ``core.kernels.discover_functional_dependencies`` calls it directly on its
    list input — so the fallback never routes through the Polars engine (the
    baseline / relations Polars-eviction discipline)."""
    distinct = [len(set(c)) for c in cols_values]
    out: list[tuple[int, int]] = []
    for i in range(len(cols_values)):
        if distinct[i] == n_rows:  # unique determinant -> trivial
            continue
        for j in range(len(cols_values)):
            if i == j or distinct[j] <= 1:
                continue
            if len(set(zip(cols_values[i], cols_values[j]))) == distinct[i]:
                out.append((i, j))
    return out


def _discover_python(frame, cols: list[str], n_rows: int) -> list[tuple[int, int]]:
    """Polars-free mirror of the kernel: det->dep holds iff
    n_distinct(det, dep) == n_distinct(det). Skips trivial pairs identically.

    The distinct-counting compute runs in pure Python (``set`` over the column
    values / value-tuples) rather than Polars ``n_unique`` — the Rust kernel is
    the fast reference; this is the correctness fallback when the native wheel
    isn't installed (roughly ~4x slower than the old Polars path, acceptable for
    a safety net). Values are pulled out of the Polars frame once with
    ``to_list``; the engine no longer runs the distinct counts."""
    frame = to_frame(frame)
    values = [frame.column(c).to_list() for c in cols]
    return _discover_strict_ids(values, n_rows)


class FunctionalDependencyProfiler:
    """Dataset-level relation profiler: discover strict single-column FDs."""

    def profile(self, frame) -> list[Finding]:
        frame = to_frame(frame)
        n_rows = frame.height
        if n_rows < _MIN_ROWS or len(frame.columns) < 2:
            return []

        cols = _select_candidates(frame, n_rows)
        if len(cols) < 2:
            return []

        pairs: list[tuple[int, int]]
        if native_enabled("functional_dependencies"):
            try:
                arrays = [frame.column(c).to_arrow() for c in cols]
                pairs = native_module().discover_functional_dependencies(arrays)
            except Exception:  # noqa: BLE001 - any native failure -> pure-Python path
                pairs = _discover_python(frame, cols, n_rows)
        else:
            pairs = _discover_python(frame, cols, n_rows)

        if not pairs:
            return []

        # Merge by determinant (det -> {deps}) so A->B and A->C are one finding.
        det_to_deps: dict[str, list[str]] = {}
        for i, j in pairs:
            det_to_deps.setdefault(cols[i], []).append(cols[j])

        findings: list[Finding] = []
        for det in sorted(det_to_deps, key=lambda d: (len(det_to_deps[d]), d), reverse=True):
            deps = sorted(det_to_deps[det])
            deps_str = ", ".join(deps)
            findings.append(Finding(
                severity=Severity.INFO,
                column=det,
                check="functional_dependency",
                message=(
                    f"Column '{det}' determines ({deps_str}) — each '{det}' value maps "
                    f"to a single value of {'these columns' if len(deps) > 1 else 'this column'}, "
                    f"so {'they are' if len(deps) > 1 else 'it is'} redundant given '{det}'."
                ),
                affected_rows=n_rows,
                sample_values=[],
                suggestion=(
                    "If this is a lookup relationship, consider normalizing "
                    f"({deps_str} into a table keyed by '{det}') to remove redundancy."
                ),
                confidence=0.55,
                metadata={"technique": "functional_dependency", "determinant": det, "dependents": deps},
            ))
            if len(findings) >= _MAX_FINDINGS:
                break
        return findings
