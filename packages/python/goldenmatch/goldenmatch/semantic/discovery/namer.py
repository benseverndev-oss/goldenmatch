"""Advisory LLM namer (PR-7) — the OPTIONAL, non-authoritative half of discovery.

Structural discovery finds that `status` determines the row and is low-cardinality
(-> a dimension); it does NOT know `status='C'` means "churned." This module bolts a
business-naming layer on top, the same way the config-suggestion healer is bolted on
(context-network/decisions/0027-healer-wasm-ts.md): **opt-in, self-verified, never
authoritative.**

It annotates a *finished* `ProposedModel` with business names for entity types,
dimension columns, low-cardinality dimension VALUES (a glossary), and measures. The
names live only in `ProposedModel.naming` — the emitted YAML + certification are
computed BEFORE naming and are never altered, so structural discovery stays
byte-deterministic whether or not the namer runs.

**Two-pass self-critique.** Per table, one `propose` call (all targets + their
structural evidence) then one `verify` call that critiques each proposed name against
its evidence. A name that isn't supported, or falls below `_VERIFY_MIN_CONFIDENCE`, is
KEPT but flagged `verified=False` — surfaced for a human, never silently applied.

**Graceful abstain.** The backend is an injectable `NamerBackend` Protocol. The
default `load_namer_backend()` reuses the existing provider detection
(`core.llm_labeler`) behind the `goldenmatch[llm]` extra and returns `None` (so
`name_semantic_model` returns `[]`) when no provider/key resolves or
`GOLDENMATCH_SEMANTIC_NAMER=0`. It never raises into a discovery run.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import yaml

if TYPE_CHECKING:
    from goldenmatch.semantic.discovery.model import ProposedModel

logger = logging.getLogger(__name__)

# A verified name must clear this confidence floor in the self-critique pass.
_VERIFY_MIN_CONFIDENCE = 0.5
# Cap on distinct values glossed per categorical dimension (keeps the prompt bounded).
_MAX_GLOSS_VALUES = 30
# Line marker the prompt uses to list targets; `_targets_in_prompt` reads it back.
_TARGET_MARKER = "TARGET: "


@dataclass(frozen=True)
class NameSuggestion:
    """One advisory business-name suggestion for a discovered element.

    `target` is a stable address: ``entity:<name>`` / ``dimension:<table>.<col>`` /
    ``value:<table>.<col>=<value>`` / ``measure:<table>.<col>``. `verified` records
    whether the name survived the self-critique pass; an unverified suggestion is
    surfaced (not dropped) so a human sees what the model proposed and that it's weak.
    """

    target: str
    kind: str  # entity | dimension | value | measure
    suggested_name: str
    confidence: float
    verified: bool
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "kind": self.kind,
            "suggested_name": self.suggested_name,
            "confidence": self.confidence,
            "verified": self.verified,
            "evidence": self.evidence,
        }


@runtime_checkable
class NamerBackend(Protocol):
    """A text-in / text-out LLM the namer calls. `propose` returns the raw model text
    (JSON, per the prompt contract). Injectable so tests never hit a real provider."""

    def propose(self, prompt: str) -> str: ...


# --- target enumeration + value sampling ---------------------------------------


@dataclass(frozen=True)
class _Target:
    target: str
    kind: str
    evidence: str


def _sample_values(table: Any, column: str, cap: int = _MAX_GLOSS_VALUES) -> list[str]:
    """Distinct non-null values of `column` (sorted, capped) for the value glossary.

    Supports the pyarrow Table inputs discovery runs on; a table shape it can't read
    yields no values (the glossary just skips it) rather than raising."""
    try:
        import pyarrow as pa
        import pyarrow.compute as pc

        if isinstance(table, pa.Table):
            uniq = pc.unique(table.column(column)).to_pylist()
            vals = [str(v) for v in uniq if v is not None]
            return sorted(set(vals))[:cap]
    except Exception as exc:  # noqa: BLE001 - unreadable shape -> no glossary for it
        logger.debug("value sampling skipped for %s: %s", column, exc)
    return []


def _targets_for_table(pt: Any, table: Any) -> list[_Target]:
    """All naming targets for one discovered table: its entity, dimensions, the
    distinct values of each categorical dimension, and its measures."""
    targets: list[_Target] = []
    if pt.entity_type:
        targets.append(_Target(f"entity:{pt.entity_type}", "entity",
                               f"entity realized by table {pt.table}"))
    for d in pt.dimensions:
        targets.append(_Target(f"dimension:{pt.table}.{d.column}", "dimension",
                               f"{d.kind} dimension column"))
        if d.kind == "categorical":
            for v in _sample_values(table, d.column):
                targets.append(_Target(f"value:{pt.table}.{d.column}={v}", "value",
                                       f"a distinct value of {d.column}"))
    for m in pt.measures:
        aggs = ", ".join(m.aggregations)
        targets.append(_Target(f"measure:{pt.table}.{m.column}", "measure",
                               f"numeric measure, aggregations: {aggs}"))
    return targets


def _targets_in_prompt(prompt: str) -> list[str]:
    """Extract the target addresses a prompt lists (one per `TARGET:` line). Shared
    with test backends so a stand-in can answer exactly the targets it was asked."""
    return [m.group(1).strip() for m in re.finditer(rf"{_TARGET_MARKER}(.+)", prompt)]


# --- prompt construction + response parsing ------------------------------------


def _propose_prompt(table_name: str, targets: list[_Target]) -> str:
    lines = [
        "You are naming elements of a data model with concise business names.",
        f"For each TARGET below (from table `{table_name}`), propose a short, human "
        "business name grounded in its evidence.",
        "Reply with ONLY JSON: {\"names\": [{\"target\": <addr>, \"name\": <business "
        "name>, \"evidence\": <why>}]}.",
        "",
    ]
    for t in targets:
        lines.append(f"{_TARGET_MARKER}{t.target}")
        lines.append(f"    context: {t.evidence}")
    return "\n".join(lines)


def _verify_prompt(table_name: str, proposed: dict[str, dict[str, str]],
                   targets: list[_Target]) -> str:
    ev = {t.target: t.evidence for t in targets}
    lines = [
        f"VERIFY proposed business names for table `{table_name}`. For each TARGET, "
        "decide whether the proposed name is SUPPORTED by its evidence.",
        "Reply with ONLY JSON: {\"verdicts\": [{\"target\": <addr>, \"supported\": "
        "true|false, \"confidence\": 0..1}]}.",
        "",
    ]
    for target, info in proposed.items():
        lines.append(f"{_TARGET_MARKER}{target}")
        lines.append(f"    proposed name: \"{info.get('name', '')}\"")
        lines.append(f"    evidence: {ev.get(target, '')}")
    return "\n".join(lines)


def _parse_json_object(text: str) -> dict[str, Any]:
    """Lenient parse of a model reply into a dict (empty on failure — abstain)."""
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001 - fall through to a brace-scan
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001 - unparseable -> abstain
        return {}


# --- the namer ------------------------------------------------------------------


def name_semantic_model(
    model: ProposedModel,
    tables: dict[str, Any],
    *,
    backend: NamerBackend | None = None,
) -> list[NameSuggestion]:
    """Annotate `model` with advisory business names via two-pass self-critique.

    Returns a list of `NameSuggestion` (deduped by target). `backend=None` abstains to
    an empty list. The model itself is not mutated here; the orchestrator attaches the
    result to `ProposedModel.naming`.
    """
    if backend is None:
        return []

    out: dict[str, NameSuggestion] = {}
    for pt in model.tables:
        targets = _targets_for_table(pt, tables.get(pt.table))
        if not targets:
            continue
        kind_of = {t.target: t.kind for t in targets}
        ev_of = {t.target: t.evidence for t in targets}

        # Pass 1 — propose.
        proposed_raw = _parse_json_object(backend.propose(_propose_prompt(pt.table, targets)))
        proposed: dict[str, dict[str, str]] = {}
        for item in proposed_raw.get("names", []):
            tgt = str(item.get("target", ""))
            if tgt in kind_of and item.get("name"):
                proposed[tgt] = {"name": str(item["name"]),
                                 "evidence": str(item.get("evidence", ev_of.get(tgt, "")))}
        if not proposed:
            continue

        # Pass 2 — self-critique.
        verdicts_raw = _parse_json_object(
            backend.propose(_verify_prompt(pt.table, proposed, targets))
        )
        verdict: dict[str, tuple[bool, float]] = {}
        for item in verdicts_raw.get("verdicts", []):
            tgt = str(item.get("target", ""))
            try:
                conf = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
            except (TypeError, ValueError):
                conf = 0.0
            verdict[tgt] = (bool(item.get("supported", False)), conf)

        for tgt, info in proposed.items():
            supported, conf = verdict.get(tgt, (False, 0.0))
            verified = supported and conf >= _VERIFY_MIN_CONFIDENCE
            if tgt not in out:  # dedup (an entity shared across tables names once)
                out[tgt] = NameSuggestion(
                    target=tgt, kind=kind_of[tgt], suggested_name=info["name"],
                    confidence=conf, verified=verified, evidence=info["evidence"],
                )
    return list(out.values())


# --- applied catalog (PR-8): write verified names into the emitted YAML ---------


def _glossary(sm: dict[str, Any]) -> dict[str, Any]:
    """The `meta.goldenmatch.glossary` block for a semantic model, created on demand
    (a sibling of the key-integrity verdict already carried in `meta.goldenmatch`)."""
    return sm.setdefault("meta", {}).setdefault("goldenmatch", {}).setdefault("glossary", {})


def apply_names(model: ProposedModel) -> str:
    """Write the model's VERIFIED name suggestions into its emitted MetricFlow YAML,
    returning the labeled document. Pure + LLM-free (operates on `model.naming` +
    `model.yaml`), post-certification and cosmetic:

    - entity  -> the semantic_model's ``label:`` (structural ``name:`` unchanged)
    - measure -> that measure's ``label:``
    - dimension / value -> ``meta.goldenmatch.glossary`` (MetricFlow has no native slot)

    Only ``verified=True`` suggestions are applied. With no verified names (or no
    emitted YAML) the original `model.yaml` is returned unchanged.
    """
    verified = [s for s in model.naming if s.verified]
    if not verified or not model.yaml:
        return model.yaml

    doc = yaml.safe_load(model.yaml)
    if not isinstance(doc, dict) or "semantic_models" not in doc:
        return model.yaml
    sms = doc["semantic_models"]
    by_name = {sm.get("name"): sm for sm in sms}

    for s in verified:
        payload = s.target.split(":", 1)[1] if ":" in s.target else s.target
        if s.kind == "entity":
            for sm in sms:  # a multi-table entity labels each of its models
                if any(e.get("type") == "primary" and e.get("name") == payload
                       for e in sm.get("entities", [])):
                    sm["label"] = s.suggested_name
        elif s.kind == "measure":
            table, _, col = payload.partition(".")
            sm = by_name.get(table)
            for m in (sm.get("measures", []) if sm else []):
                if m.get("name") == col:
                    m["label"] = s.suggested_name
        elif s.kind == "dimension":
            table, _, col = payload.partition(".")
            sm = by_name.get(table)
            if sm is not None:
                _glossary(sm).setdefault("dimensions", {})[col] = s.suggested_name
        elif s.kind == "value":
            table_col, _, val = payload.partition("=")
            table, _, col = table_col.partition(".")
            sm = by_name.get(table)
            if sm is not None:
                _glossary(sm).setdefault("values", {}).setdefault(col, {})[val] = s.suggested_name

    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


# --- default backend (opt-in, graceful abstain) --------------------------------


class LLMNamerBackend:
    """Default `NamerBackend` over the existing provider clients (`core.llm_labeler`,
    the `goldenmatch[llm]` extra). Built only when a provider + key resolve."""

    def __init__(self, provider: str, api_key: str, model: str) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model = model

    def propose(self, prompt: str) -> str:
        from goldenmatch.core.llm_labeler import _call_llm_with_retry

        try:
            return _call_llm_with_retry(prompt, self._provider, self._api_key, self._model)
        except Exception as exc:  # noqa: BLE001 - inference failure -> abstain (empty)
            logger.warning("namer LLM call failed: %s", exc)
            return ""


def load_namer_backend() -> NamerBackend | None:
    """Resolve the default backend, or `None` to abstain.

    ``GOLDENMATCH_SEMANTIC_NAMER=0`` is a hard kill-switch. Otherwise a backend is
    built when `detect_provider()` finds an API key; no key -> `None` (abstain).
    """
    if os.environ.get("GOLDENMATCH_SEMANTIC_NAMER", "").strip() == "0":
        return None
    from goldenmatch.core.llm_labeler import detect_provider, get_default_model

    found = detect_provider()
    if found is None:
        return None
    provider, api_key = found
    return LLMNamerBackend(provider, api_key, get_default_model(provider))
