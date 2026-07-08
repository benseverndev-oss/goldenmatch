"""Host glue for repair-plan: sample polars columns, build ColumnInputs, call the
pure kernel, attach the advisory artifact + reasoning. Advisory ONLY — never
mutates the stage list."""
from __future__ import annotations

from goldenpipe.repair import build_repair_plan

_SAMPLE_LIMIT = 20


def sample_column(series, limit: int = _SAMPLE_LIMIT) -> list[str]:
    out: list[str] = []
    for v in series:                       # polars Series iterates values in row order
        if v is None:
            continue
        s = str(v)
        if s.strip() == "":
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _coarse_str(t) -> str:
    # ColumnType is `str, Enum` with lowercase values, BUT str(ColumnType.EMAIL) ==
    # "ColumnType.EMAIL" (NOT "email") — only .value gives "email". The context
    # builder can also leave inferred_type a raw str (column_context.py coercion),
    # which has no .value. getattr handles both.
    return getattr(t, "value", t)


def build_column_inputs(contexts, df) -> list[dict]:
    cols = []
    names = set(df.columns)
    for ctx in contexts:
        if ctx.name not in names:
            continue
        cols.append({
            "name": ctx.name,
            "coarse_type": _coarse_str(ctx.inferred_type),
            "samples": sample_column(df[ctx.name]),
        })
    return cols


def attach_repair_plan(ctx, findings, contexts, df) -> dict:
    columns = build_column_inputs(contexts, df)
    plan = build_repair_plan(findings, columns)
    ctx.artifacts["repair_plan"] = plan
    lines = [
        f"repair: {item['column']} ({item['check']}) -> "
        f"{','.join(item['suggested_transforms'])} [{item['reason']}]"
        for item in plan["repairs"]
    ]
    if lines:
        ctx.reasoning["repair_plan"] = "\n".join(lines)
    return plan


# ── Phase 2: active application (fixer allowlist + config conversion) ─────
# FIXERS are transforms that CLEAN in place. Everything else the kernel can
# suggest is a *_validate ASSERTION that returns a boolean series — applying it
# as a column op would overwrite the column with True/False, so it is skipped.
# Host policy, kept identical in repairHost.ts (NOT the parity-gated kernel).
FIXERS = frozenset({
    "fix_mojibake", "normalize_unicode", "date_parse", "email_normalize",
    "email_canonical", "name_proper", "phone_national", "zip_normalize",
})


def repair_transform_specs(plan: dict) -> tuple[list[dict], list[dict]]:
    """repair_plan -> (specs, skipped). specs = [{column, ops}] fixer ops grouped
    per column, deduped, order-preserving. skipped = [{column, op}] assertion ops."""
    by_col: dict[str, list[str]] = {}
    order: list[str] = []
    skipped: list[dict] = []
    for item in plan.get("repairs", []):
        col = item["column"]
        for op in item.get("suggested_transforms", []):
            if op in FIXERS:
                if col not in by_col:
                    by_col[col] = []
                    order.append(col)
                if op not in by_col[col]:
                    by_col[col].append(op)
            else:
                skipped.append({"column": col, "op": op})
    specs = [{"column": c, "ops": by_col[c]} for c in order]
    return specs, skipped


def merge_transforms(user: list[dict], repair: list[dict]) -> list[dict]:
    """Per-column merge, user ops first then repair ops, dedup exact dupes,
    preserving first-seen column + op order."""
    by_col: dict[str, list[str]] = {}
    order: list[str] = []
    for spec in list(user) + list(repair):
        col = spec["column"]
        if col not in by_col:
            by_col[col] = []
            order.append(col)
        by_col[col].extend(spec.get("ops") or [])
    result = []
    for col in order:
        seen: set[str] = set()
        ops: list[str] = []
        for op in by_col[col]:
            if op not in seen:
                seen.add(op)
                ops.append(op)
        result.append({"column": col, "ops": ops})
    return result
