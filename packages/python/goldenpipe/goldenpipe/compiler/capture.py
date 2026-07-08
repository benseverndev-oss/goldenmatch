"""Host: reconstruct each executed stage's concrete config for the IR record.
Flow from the manifest (what actually ran); Check from profile+findings; Match from
the deterministic contexts config. Returns (kind_hint, concrete, resolved) for lower().
Match/Check node sub-fields are a faithful RECORD (placeholders for SP1)."""
from __future__ import annotations


def capture_stage(planned, ctx, result):
    name = planned.name
    cfg = planned.config or {}
    resolved = not cfg  # auto stage when no explicit config

    if name == "load":
        return "source", {}, False

    if name == "goldenflow.transform":
        manifest = ctx.artifacts.get("manifest")
        by_col: dict[str, list] = {}
        order: list[str] = []
        for rec in (getattr(manifest, "records", None) or []):
            col = rec.column
            if col not in by_col:
                by_col[col] = []
                order.append(col)
            by_col[col].append(rec.transform)
        return "map", {"transforms": [{"column": c, "ops": by_col[c]} for c in order]}, resolved

    if name == "goldencheck.scan":
        profile = ctx.artifacts.get("profile")
        findings = ctx.artifacts.get("findings") or []
        by_col = {}
        order = []
        for col in _profile_columns(profile):
            if col not in by_col:
                by_col[col] = []
                order.append(col)
        for f in findings:
            col = f.get("column") if isinstance(f, dict) else getattr(f, "column", None)
            chk = f.get("check") if isinstance(f, dict) else getattr(f, "check", None)
            if col is None or chk is None:
                continue
            if col not in by_col:
                by_col[col] = []
                order.append(col)
            by_col[col].append(chk)
        return "scan", {"columns": [{"column": c, "ops": by_col[c]} for c in order]}, resolved

    if name == "goldenmatch.dedupe":
        concrete = dict(cfg) if cfg else _match_config_from_ctx(ctx)
        return "match", concrete, resolved

    return "barrier", dict(cfg), False


def _profile_columns(profile) -> list[str]:
    cols = getattr(profile, "columns", None) if profile is not None else None
    out: list[str] = []
    for c in (cols or []):
        nm = getattr(c, "name", None)
        if nm is None and isinstance(c, dict):
            nm = c.get("name")
        if nm is not None:
            out.append(nm)
    return out


def _match_config_from_ctx(ctx) -> dict:
    contexts = ctx.artifacts.get("column_contexts")
    if not contexts:
        return {}
    from goldenpipe.adapters.match import _build_config_from_contexts
    config = _build_config_from_contexts(contexts, ctx.df)
    if config is None:
        return {}
    if hasattr(config, "model_dump"):
        return config.model_dump()
    if hasattr(config, "dict"):
        return config.dict()
    return {}
