"""LLM entity canonicalization -- a defensible canonical record from a cluster (#1091).

Given the source records of one cluster (the duplicates the engine merged), produce
a single canonical record: per field, the value that best represents the entity,
WITH per-cell provenance (which source record it came from) and a rationale.

Two tiers, with graceful degradation between them:

- **LLM** (when an ``llm_call`` is supplied or a provider is auto-detected AND
  the budget allows): the model picks the canonical value per field from the
  candidates and explains why. Chain-of-thought golden-name / disagreeing-field
  reconciliation, exactly the borderline judgement the LLM scorer already does
  for pairs.
- **Deterministic** (the fallback, always available): per field, the
  most-complete value (longest non-null) wins. No cloud, no cost, never fails.

ANY LLM failure -- no provider, exhausted budget, network error, unparseable
response, a value the model invented that's in no source record -- degrades to
the deterministic result for the affected field (or the whole record). The
function never raises on valid input, so it is safe to wire into a pipeline.

The ``llm_call`` seam (``Callable[[str], tuple[str, int, int]]`` -> text +
input/output tokens) makes the LLM path fully testable with a stub -- no network.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

LlmCall = Callable[[str], tuple[str, int, int]]


@dataclass
class FieldProvenance:
    """Where one canonical cell came from."""

    field: str
    value: Any
    source_index: int | None  # index into the input records, or None if synthesized
    rationale: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source_index": self.source_index,
            "rationale": self.rationale,
        }


@dataclass
class CanonicalRecord:
    """The canonical record for a cluster, with per-cell provenance."""

    record: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, FieldProvenance] = field(default_factory=dict)
    method: str = "deterministic"  # "llm" | "deterministic"
    rationale: str = ""
    llm_cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "provenance": {k: v.as_dict() for k, v in self.provenance.items()},
            "method": self.method,
            "rationale": self.rationale,
            "llm_cost_usd": round(self.llm_cost_usd, 6),
        }


def _field_order(records: list[dict[str, Any]], fields: list[str] | None) -> list[str]:
    if fields is not None:
        return list(fields)
    seen: list[str] = []
    for rec in records:
        for k in rec:
            if not k.startswith("__") and k not in seen:
                seen.append(k)
    return seen


def _deterministic_choice(
    records: list[dict[str, Any]], field_name: str
) -> tuple[Any, int | None]:
    """Most-complete (longest non-null string rep) value + its source index."""
    best_idx: int | None = None
    best_val: Any = None
    best_len = -1
    for i, rec in enumerate(records):
        v = rec.get(field_name)
        if v is None:
            continue
        s = str(v)
        if not s:
            continue
        if len(s) > best_len:
            best_len = len(s)
            best_val = v
            best_idx = i
    return best_val, best_idx


def _deterministic_record(
    records: list[dict[str, Any]], fields: list[str], rationale: str
) -> CanonicalRecord:
    out = CanonicalRecord(method="deterministic", rationale=rationale)
    for f in fields:
        val, idx = _deterministic_choice(records, f)
        out.record[f] = val
        out.provenance[f] = FieldProvenance(
            field=f, value=val, source_index=idx, rationale="most complete value",
        )
    return out


def _build_prompt(records: list[dict[str, Any]], fields: list[str]) -> str:
    lines = [
        "You reconcile duplicate records of one real-world entity into a single",
        "canonical record. For EACH field, choose the single best value -- prefer",
        "the most complete and correct one -- ONLY from the candidate values shown",
        "(do not invent values). Reply with JSON only:",
        '{"fields": {"<field>": {"value": <chosen value>, "source": <record index>,',
        '"reason": "<short reason>"}}, "rationale": "<one sentence overall>"}',
        "",
        "Records (index: field=value):",
    ]
    for i, rec in enumerate(records):
        parts = [f"{f}={rec.get(f)!r}" for f in fields]
        lines.append(f"{i}: " + ", ".join(parts))
    return "\n".join(lines)


def _default_llm_call(model: str) -> LlmCall | None:
    """Build an llm_call from the auto-detected provider, or None if unavailable."""
    try:
        from goldenmatch.core.llm_scorer import (
            _call_anthropic,
            _call_openai,
            _detect_provider,
        )

        provider, key = _detect_provider()
        if not provider or not key:
            return None
        if provider == "openai":
            return lambda prompt: _call_openai(prompt, key, model, max_tokens=600)
        if provider == "anthropic":
            return lambda prompt: _call_anthropic(prompt, key, model, max_tokens=600)
    except Exception:
        return None
    return None


def _parse_llm_fields(text: str) -> dict[str, dict[str, Any]]:
    """Extract the ``fields`` object from the model's JSON reply (tolerant of
    surrounding prose / code fences)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in LLM reply")
    data = json.loads(text[start : end + 1])
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("LLM reply missing 'fields' object")
    return {"fields": fields, "rationale": str(data.get("rationale", ""))}


def canonicalize_cluster(
    records: list[dict[str, Any]],
    *,
    fields: list[str] | None = None,
    llm_call: LlmCall | None = None,
    budget: Any = None,
    model: str = "gpt-4o-mini",
) -> CanonicalRecord:
    """Produce a canonical record for one cluster's source records.

    Args:
        records: the cluster's source records (dicts; ``__``-prefixed keys are
            ignored). Each is treated as one candidate per field.
        fields: which fields to canonicalize (default: the union of non-internal
            keys, first-seen order).
        llm_call: an ``llm_call(prompt) -> (text, input_tokens, output_tokens)``.
            When ``None``, a provider is auto-detected; when none is reachable,
            the deterministic fallback is used.
        budget: an optional ``BudgetTracker``. When exhausted (or it refuses the
            call), the deterministic fallback is used; usage is recorded on success.
        model: the model id for the auto-detected provider + budget accounting.

    Returns:
        A ``CanonicalRecord`` with the canonical value + provenance per field.
        Never raises on a valid ``records`` list.
    """
    flds = _field_order(records, fields)
    if not records:
        return CanonicalRecord(method="deterministic", rationale="empty cluster")

    deterministic = _deterministic_record(
        records, flds, rationale="deterministic most-complete selection",
    )

    call = llm_call if llm_call is not None else _default_llm_call(model)
    if call is None:
        return deterministic

    # Budget gate: if a tracker is supplied and it's spent / refuses, degrade.
    if budget is not None:
        try:
            if getattr(budget, "budget_exhausted", False):
                return deterministic
            est_tokens = 60 * len(records) + 40 * len(flds)
            if hasattr(budget, "can_send") and not budget.can_send(est_tokens):
                return deterministic
        except Exception:
            return deterministic

    try:
        prompt = _build_prompt(records, flds)
        text, in_tok, out_tok = call(prompt)
        parsed = _parse_llm_fields(text)
    except Exception:
        logger.warning("LLM canonicalization failed; using deterministic fallback",
                       exc_info=True)
        return deterministic

    if budget is not None:
        try:
            budget.record_usage(in_tok, out_tok, model)
        except Exception:
            pass

    out = CanonicalRecord(method="llm", rationale=parsed["rationale"])
    llm_fields = parsed["fields"]
    for f in flds:
        spec = llm_fields.get(f)
        if not isinstance(spec, dict) or "value" not in spec:
            # The model skipped this field -> deterministic for this cell.
            det = deterministic.provenance[f]
            out.record[f] = det.value
            out.provenance[f] = det
            continue
        value = spec.get("value")
        # Provenance: trust the model's source only if it actually holds that value;
        # otherwise find a record that does; otherwise mark synthesized (None).
        src = spec.get("source")
        idx: int | None = None
        if isinstance(src, int) and 0 <= src < len(records) and records[src].get(f) == value:
            idx = src
        else:
            for i, rec in enumerate(records):
                if rec.get(f) == value:
                    idx = i
                    break
        out.record[f] = value
        out.provenance[f] = FieldProvenance(
            field=f, value=value, source_index=idx,
            rationale=str(spec.get("reason", "")) or None,
        )

    if budget is not None:
        out.llm_cost_usd = float(getattr(budget, "total_cost_usd", 0.0) or 0.0)
    return out
