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
