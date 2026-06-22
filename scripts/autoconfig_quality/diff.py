"""Scorecard diff + gate verdict.

Verdict rules (from the spec):
- anchor: ANY pinned signal that changed from baseline = FAIL; an error on an
  anchor = FAIL (anchors must always run).
- real: F1 below (baseline_f1 - tolerance) = FAIL; signal drift is informational
  (WARN, never fails); an error on a real dataset = NEUTRAL.
- a dataset present in baseline but skipped/absent in current = NEUTRAL.
The overall verdict is FAIL if any row is FAIL, else PASS.
"""
from __future__ import annotations

from typing import Any

_STATUS_FAIL = "FAIL"
_STATUS_OK = "OK"
_STATUS_WARN = "WARN"
_STATUS_NEUTRAL = "NEUTRAL"


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested signals dict to {dotted.path: leaf_value}."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = obj
    return out


def _row(dataset: str, field: str, before: Any, after: Any, status: str) -> dict[str, Any]:
    return {"dataset": dataset, "field": field, "before": before, "after": after,
            "status": status}


def diff_scorecards(
    current: dict, baseline: dict, *, tolerance: float = 0.01,
) -> tuple[list[dict], str]:
    """Compare current vs baseline -> (delta rows, overall verdict)."""
    rows: list[dict] = []
    cur = current.get("datasets", {})
    base = baseline.get("datasets", {})

    for name, c in cur.items():
        b = base.get(name)
        kind = c.get("kind", b.get("kind") if b else "real")

        if kind == "anchor":
            # An error on an anchor is a hard FAIL (anchors must always run).
            if "error" in c.get("signals", {}):
                rows.append(_row(name, "signals", None, c["signals"]["error"], _STATUS_FAIL))
                continue
            # Any pinned signal that changed from baseline = FAIL.
            cur_sig = _flatten(c.get("signals", {}))
            base_sig = _flatten(b.get("signals", {})) if b else {}
            for field in sorted(set(cur_sig) | set(base_sig)):
                before, after = base_sig.get(field), cur_sig.get(field)
                if before != after:
                    rows.append(_row(name, field, before, after, _STATUS_FAIL))
        else:  # real
            if "error" in c:
                rows.append(_row(name, "f1", None, c["error"], _STATUS_NEUTRAL))
                continue
            cur_f1 = c.get("f1", {}).get("f1")
            base_f1 = b.get("f1", {}).get("f1") if b else None
            if cur_f1 is not None and base_f1 is not None:
                status = _STATUS_FAIL if cur_f1 < base_f1 - tolerance else _STATUS_OK
                rows.append(_row(name, "f1", base_f1, cur_f1, status))
            elif cur_f1 is not None:
                rows.append(_row(name, "f1", None, cur_f1, _STATUS_OK))

    # Datasets in baseline but absent from current -> neutral (skipped).
    for name in base:
        if name not in cur:
            rows.append(_row(name, "*", "present", "absent", _STATUS_NEUTRAL))

    verdict = "FAIL" if any(r["status"] == _STATUS_FAIL for r in rows) else "PASS"
    return rows, verdict


def render_table(rows: list[dict]) -> str:
    """Aligned delta table. ✗ = FAIL, ⚠ = WARN, · = OK, ~ = NEUTRAL."""
    mark = {_STATUS_FAIL: "✗", _STATUS_WARN: "⚠", _STATUS_OK: "·", _STATUS_NEUTRAL: "~"}
    lines = []
    for r in rows:
        lines.append(
            f"{r['dataset']:<22} {r['field']:<18} {r['before']} → {r['after']}  "
            f"{mark.get(r['status'], '?')} ({r['status']})"
        )
    return "\n".join(lines) if lines else "(no differences)"
