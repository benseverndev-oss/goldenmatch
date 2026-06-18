"""Deterministic config-weakness generator (``diagnose_config``).

After auto-config builds a config and a first run produces a result, this
module explains — in plain English — what the auto-built rules *did* and where
they are risky. It is the read-only, deterministic core; MCP wiring lives in a
separate task and imports ``diagnose_config`` from here.

Design (spec 2026-06-?? §5):

- One pure entry point, ``diagnose_config(df, config, result)``, returns
  ``{"findings": [...], "summary_plain": str}``.
- Each *detector* maps engine signals (the resolved ``GoldenMatchConfig``, the
  column profile from ``profile_columns``, and the postflight signals on
  ``result.postflight_report``) to at most one finding.
- Detectors are **defensive**: any signal can be absent (no postflight report,
  empty profile, weird config). A missing signal means "skip this detector,"
  never raise. Each detector body is wrapped so one failure can't kill the
  rest — ``diagnose_config`` never raises on a valid ``(df, config, result)``.
- Findings are ranked high→low by severity, then truncated to ``max_findings``.
- Wording is template-driven and deterministic. ``phrasing="plain"`` (default)
  is non-technical; ``phrasing="technical"`` may name columns/metrics directly.
  Neither requires an LLM.
- An OPTIONAL one-paragraph LLM summary is gated behind
  ``GOLDENMATCH_WEAKNESS_LLM=1`` AND a detected provider, and is sent ONLY a
  compact structured digest (ids + severities + short labels — tens of tokens,
  never raw data). Any failure falls back to the deterministic template
  summary, so the function is fully usable offline.

The deterministic detectors reuse existing engine signals rather than
recomputing: ``_collect_referenced_columns`` from ``autoconfig_verify`` for the
column walk, ``profile_columns`` for null-rate / cardinality / col_type, and
``PostflightReport.signals`` (``block_size_percentiles`` / ``oversized_clusters``
/ ``preliminary_cluster_sizes``) for the live-run block + cluster shape.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from goldenmatch.core.autoconfig_verify import _collect_referenced_columns

if TYPE_CHECKING:
    import polars as pl

    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.autoconfig import ColumnProfile

# ── Tunables (deterministic thresholds) ──────────────────────────────────────

# A column with more than this fraction of nulls is mostly empty: matching on
# it is rarely doing useful work. Mirrors the spec's null_rate > 0.2 cutoff.
_NULL_SINK_RATE = 0.2
# A matchkey column whose distinct-value ratio is below this discriminates
# almost nothing (e.g. a near-constant "country" column).
_LOW_SIGNAL_CARD = 0.01
# An id-like column has a near-unique distinct ratio.
_ID_CARD = 0.98
# Profiled column types that are legitimate identity attributes — high
# cardinality is EXPECTED and GOOD for these, so cardinality alone must not
# mark them as an admitted identifier. (``profile_columns`` col_type vocab.)
_IDENTITY_COL_TYPES = frozenset(
    {"email", "name", "phone", "address", "zip", "geo"}
)
# Block-size sanity ceilings. The engine's own preflight (Check 4) warns when a
# block P99 exceeds 5000 ("mega-blocks dominate runtime"); we reuse the same
# ceiling and escalate to HIGH when it's an order of magnitude past it.
_BLOCK_P99_WARN = 5000
_BLOCK_P99_HIGH = 50_000

# severity → rank for stable high→low ordering.
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# A column named exactly this (case-insensitive), or clearly a source-system /
# provenance label, just records WHERE a row came from. Matching on it merges
# rows that happen to share an origin and splits the same person across feeds.
_SOURCE_NAME_RE = re.compile(
    r"^(source|source_system|src|origin|feed|dataset|system|provider|channel)$",
    re.IGNORECASE,
)

# A per-row identifier column. These are unique by construction, so as an exact
# key they never agree and as a fuzzy signal they inject noise. Pattern mirrors
# the foreign-id / pk family the quality-exclusion detectors already recognise.
_ID_NAME_RE = re.compile(
    r"(^|_)(id|uuid|guid|pk)$"
    r"|^(record_id|row_id|external_id|legacy_id|source_id|source_pk)$",
    re.IGNORECASE,
)


# ── Finding construction ──────────────────────────────────────────────────────


def _finding(
    *,
    id: str,
    severity: str,
    evidence: dict,
    fix_config_hint: dict,
    title_plain: str,
    detail_plain: str,
    fix_plain: str,
) -> dict:
    """Assemble one finding dict in the public contract shape.

    ``phrasing`` selection happens in the detector by choosing which strings to
    pass here, so this helper stays phrasing-agnostic.
    """
    return {
        "id": id,
        "severity": severity,
        "title_plain": title_plain,
        "detail_plain": detail_plain,
        "evidence": evidence,
        "fix_plain": fix_plain,
        "fix_config_hint": fix_config_hint,
    }


# ── Detectors ──────────────────────────────────────────────────────────────────
#
# Each detector returns ``list[dict]`` (0+ findings). They take only the
# already-computed context (config, referenced columns, profiles-by-name,
# signals) so they're cheap and independently testable.


def _detect_source_admitted(
    referenced: set[str], excluded: set[str], phrasing: str
) -> list[dict]:
    out: list[dict] = []
    for col in sorted(referenced):
        if col in excluded:
            continue
        if not _SOURCE_NAME_RE.match(col):
            continue
        if phrasing == "technical":
            title = f"Provenance column '{col}' is used as a matching signal"
            detail = (
                f"'{col}' looks like a source/provenance label, not an "
                f"identity attribute. Using it in a matchkey or blocking pass "
                f"makes records from the same feed look more alike and splits "
                f"one entity across feeds."
            )
        else:
            title = "Two different companies may be merged"
            detail = (
                f"The matcher is using the '{col}' label as a matching signal, "
                f"but that label only records where a row came from. Records "
                f"from the same system get treated as more similar, and the "
                f"same person split across systems looks less similar."
            )
        out.append(
            _finding(
                id="source_admitted",
                severity="high",
                evidence={"column": col, "reason": "source/provenance label"},
                fix_config_hint={"action": "exclude_column", "column": col},
                title_plain=title,
                detail_plain=detail,
                fix_plain=(
                    f"Stop matching on '{col}'; it just records where a row "
                    f"came from."
                ),
            )
        )
    return out


def _detect_id_admitted(
    referenced: set[str],
    excluded: set[str],
    profiles_by_name: dict[str, ColumnProfile],
    phrasing: str,
) -> list[dict]:
    out: list[dict] = []
    for col in sorted(referenced):
        if col in excluded:
            continue
        prof = profiles_by_name.get(col)
        by_name = bool(_ID_NAME_RE.search(col))
        by_type = prof is not None and prof.col_type == "identifier"
        # Near-unique cardinality alone is NOT enough: email / name / phone /
        # address are legitimately high-cardinality and ARE the columns you
        # want to match on. Only infer "id by cardinality" for columns the
        # profiler did NOT recognise as an identity-bearing attribute.
        by_card = (
            prof is not None
            and prof.cardinality_ratio >= _ID_CARD
            and prof.col_type not in _IDENTITY_COL_TYPES
        )
        if not (by_name or by_type or by_card):
            continue
        # Don't double-fire with source_admitted on the same column.
        if _SOURCE_NAME_RE.match(col):
            continue
        why = (
            "name looks like a per-row id"
            if by_name
            else "profiled as an identifier"
            if by_type
            else "nearly every value is unique"
        )
        evidence: dict = {"column": col, "reason": why}
        if prof is not None:
            evidence["cardinality_ratio"] = round(prof.cardinality_ratio, 4)
        if phrasing == "technical":
            title = f"Identifier column '{col}' is used as a matching signal"
            detail = (
                f"'{col}' is a per-row id ({why}). Identifiers don't repeat "
                f"across true duplicates, so as an exact key it never agrees, "
                f"and as a fuzzy signal it only adds noise."
            )
        else:
            title = "An ID number is being used to decide who matches"
            detail = (
                f"The matcher is using '{col}' to compare records, but that "
                f"column is a unique ID (every row has a different value). It "
                f"can't tell you which records are the same person."
            )
        out.append(
            _finding(
                id="id_admitted",
                severity="high",
                evidence=evidence,
                fix_config_hint={"action": "exclude_column", "column": col},
                title_plain=title,
                detail_plain=detail,
                fix_plain=(
                    f"Stop matching on '{col}'; it's a unique ID, not a shared "
                    f"trait."
                ),
            )
        )
    return out


def _detect_null_sink(
    matchkey_cols: set[str],
    profiles_by_name: dict[str, ColumnProfile],
    phrasing: str,
) -> list[dict]:
    out: list[dict] = []
    for col in sorted(matchkey_cols):
        prof = profiles_by_name.get(col)
        if prof is None or prof.null_rate <= _NULL_SINK_RATE:
            continue
        pct = round(prof.null_rate * 100)
        if phrasing == "technical":
            title = f"Matchkey column '{col}' is {pct}% null"
            detail = (
                f"'{col}' has null_rate {prof.null_rate:.2f} (> "
                f"{_NULL_SINK_RATE:.2f}). Most pairs have nothing to compare "
                f"on this field, so it contributes little to the score."
            )
        else:
            title = f"The matcher relies on a mostly-empty column ('{col}')"
            detail = (
                f"About {pct}% of rows have no value in '{col}', so for most "
                f"records there's nothing to compare. Matching leans on it "
                f"anyway, which weakens the result."
            )
        out.append(
            _finding(
                id="null_sink",
                severity="medium",
                evidence={"column": col, "null_rate": round(prof.null_rate, 4)},
                fix_config_hint={"action": "demote_to_blocking", "column": col},
                title_plain=title,
                detail_plain=detail,
                fix_plain=(
                    f"Stop matching on '{col}'; it's empty for most rows."
                ),
            )
        )
    return out


def _detect_low_signal_key(
    matchkey_cols: set[str],
    profiles_by_name: dict[str, ColumnProfile],
    phrasing: str,
) -> list[dict]:
    out: list[dict] = []
    for col in sorted(matchkey_cols):
        prof = profiles_by_name.get(col)
        if prof is None:
            continue
        # An id-like / near-unique column is handled by id_admitted; here we
        # only flag the OPPOSITE extreme — almost no distinct values.
        if prof.cardinality_ratio >= _LOW_SIGNAL_CARD:
            continue
        if phrasing == "technical":
            title = f"Matchkey column '{col}' has near-zero cardinality"
            detail = (
                f"'{col}' has cardinality_ratio {prof.cardinality_ratio:.4f} "
                f"(< {_LOW_SIGNAL_CARD}). Almost every row shares the same "
                f"value, so the field barely separates matches from non-matches."
            )
        else:
            title = f"A column ('{col}') is almost the same for every row"
            detail = (
                f"Nearly all rows share the same '{col}' value, so it does "
                f"little to tell records apart. It's mostly along for the ride."
            )
        out.append(
            _finding(
                id="low_signal_key",
                severity="low",
                evidence={
                    "column": col,
                    "cardinality_ratio": round(prof.cardinality_ratio, 4),
                },
                fix_config_hint={"action": "demote_to_blocking", "column": col},
                title_plain=title,
                detail_plain=detail,
                fix_plain=(
                    f"Drop '{col}' from matching; it barely varies."
                ),
            )
        )
    return out


def _detect_oversized_block(signals: dict | None, phrasing: str) -> list[dict]:
    if not signals:
        return []
    pct = signals.get("block_size_percentiles") or {}
    p99 = pct.get("p99")
    max_size = pct.get("max")
    if not isinstance(p99, (int, float)):
        return []
    if p99 <= _BLOCK_P99_WARN:
        return []
    severity = "high" if p99 >= _BLOCK_P99_HIGH else "medium"
    evidence: dict = {"p99": int(p99)}
    if isinstance(max_size, (int, float)):
        evidence["max"] = int(max_size)
    action = "compound_blocking" if severity == "high" else "tighten_blocking"
    if phrasing == "technical":
        title = f"A blocking key produces oversized blocks (P99={int(p99)})"
        detail = (
            f"Block-size P99 is {int(p99)} (ceiling {_BLOCK_P99_WARN}); a "
            f"shared value is grouping too many rows into one block, which "
            f"both slows scoring and risks over-merging within the block."
        )
    else:
        title = "A common value is lumping too many records together"
        detail = (
            f"One blocking value groups thousands of records at once (the "
            f"biggest group is around {int(p99)} rows). That makes the run "
            f"slow and risks merging records that only share that one value."
        )
    return [
        _finding(
            id="shared_value_block",
            severity=severity,
            evidence=evidence,
            fix_config_hint={"action": action},
            title_plain=title,
            detail_plain=detail,
            fix_plain=(
                "Block on a more specific combination of fields so each group "
                "stays small."
            ),
        )
    ]


def _detect_over_merge(
    signals: dict | None, clusters: dict | None, phrasing: str
) -> list[dict]:
    max_size = 0
    n_oversized = 0
    if signals:
        oversized = signals.get("oversized_clusters") or []
        n_oversized = len(oversized)
        sizes = [
            c.get("size", 0)
            for c in oversized
            if isinstance(c, dict) and isinstance(c.get("size"), (int, float))
        ]
        if sizes:
            max_size = max(int(s) for s in sizes)
        prelim = signals.get("preliminary_cluster_sizes") or {}
        pmax = prelim.get("max")
        if isinstance(pmax, (int, float)):
            max_size = max(max_size, int(pmax))
    # Fallback when postflight is absent but raw clusters are present.
    if max_size == 0 and clusters:
        try:
            for cid, info in clusters.items():
                members = info.get("members") if isinstance(info, dict) else None
                size = len(members) if members is not None else 0
                if size > max_size:
                    max_size = size
        except Exception:
            return []
    # Only flag genuine mega-clusters — mirrors postflight's >100 oversized cut.
    if n_oversized == 0 and max_size <= 100:
        return []
    if phrasing == "technical":
        title = f"Cluster sizes show over-merging (max={max_size})"
        detail = (
            f"The largest cluster has {max_size} records "
            f"({n_oversized} oversized cluster(s) flagged). A few mega-clusters "
            f"usually mean the threshold is too loose or a weak signal is "
            f"chaining records together."
        )
    else:
        title = "Too many records got merged into one giant group"
        detail = (
            f"The biggest merged group has about {max_size} records, which is "
            f"far larger than a real duplicate set. The rules are probably "
            f"merging records that aren't actually the same."
        )
    return [
        _finding(
            id="over_merge",
            severity="high",
            evidence={"max_cluster_size": max_size, "oversized_clusters": n_oversized},
            fix_config_hint={"action": "raise_threshold"},
            title_plain=title,
            detail_plain=detail,
            fix_plain=(
                "Raise the match threshold (or tighten the rules) so only "
                "strong evidence merges records."
            ),
        )
    ]


# ── Orchestration ──────────────────────────────────────────────────────────────


def _matchkey_columns(config: GoldenMatchConfig) -> set[str]:
    """Columns referenced specifically by matchkey fields (not blocking).

    null_sink / low_signal_key only care about columns that feed the score, so
    they look here rather than at the full referenced set (which includes
    blocking-only columns).
    """
    cols: set[str] = set()
    try:
        for mk in config.get_matchkeys():
            for f in mk.fields:
                if f.field is not None and f.field != "__record__":
                    cols.add(f.field)
                if f.column is not None:
                    cols.add(f.column)
                if f.columns:
                    cols.update(f.columns)
    except Exception:
        return set()
    return cols


def _safe(fn, *args) -> list[dict]:
    """Run one detector, swallowing any error so a single bad detector can't
    take down the whole diagnosis. Returns ``[]`` on failure."""
    try:
        result = fn(*args)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _signals_of(result: Any) -> dict | None:
    """Extract the postflight signals dict from a result, defensively."""
    report = getattr(result, "postflight_report", None)
    if report is None:
        return None
    signals = getattr(report, "signals", None)
    return signals if isinstance(signals, dict) else None


def _clusters_of(result: Any) -> dict | None:
    clusters = getattr(result, "clusters", None)
    return clusters if isinstance(clusters, dict) else None


def _profiles_by_name(df: pl.DataFrame) -> dict[str, ColumnProfile]:
    """Profile the frame once (no LLM) and key by column name. Empty on error."""
    try:
        from goldenmatch.core.autoconfig import profile_columns

        profiles = profile_columns(df, llm_provider=None)
        return {p.name: p for p in profiles}
    except Exception:
        return {}


def _template_summary(findings: list[dict]) -> str:
    """Deterministic one-paragraph summary of the auto-built rules' risk."""
    if not findings:
        return (
            "The auto-built matching rules look solid for this data; no "
            "risky signals stood out. Zero-config nailed this one. Review the "
            "matches and adjust only if something looks off."
        )
    n = len(findings)
    highs = sum(1 for f in findings if f["severity"] == "high")
    titles = "; ".join(f["title_plain"] for f in findings[:3])
    lead = (
        f"The auto-built rules ran, but {n} thing(s) look risky"
        + (f" ({highs} serious)" if highs else "")
        + ". "
    )
    tail = (
        f"Top issues: {titles}. Each finding below says what to change in "
        f"plain terms."
    )
    return lead + tail


def _maybe_llm_summary(findings: list[dict], fallback: str) -> str:
    """Optional compact LLM summary, gated + fail-closed.

    Returns ``fallback`` unless ``GOLDENMATCH_WEAKNESS_LLM=1`` AND a provider is
    detected AND the call succeeds. The payload is a tiny structured digest
    (id + severity + title only) — never raw data — so this can't leak rows.
    Any error returns the deterministic template summary.
    """
    if os.environ.get("GOLDENMATCH_WEAKNESS_LLM") != "1":
        return fallback
    if not findings:
        return fallback
    try:
        from goldenmatch.core.llm_scorer import (
            _call_anthropic,
            _call_openai,
            _detect_provider,
        )

        provider, key = _detect_provider()
        if not provider or not key:
            return fallback

        digest = [
            {"id": f["id"], "severity": f["severity"], "label": f["title_plain"]}
            for f in findings
        ]
        prompt = (
            "You summarize data-matching config weaknesses for a non-technical "
            "reader. Given this JSON list of findings (id, severity, label), "
            "write ONE short plain-English paragraph (no jargon, no bullet "
            "points) describing what the auto-built rules did and where they "
            "are risky. Do not invent findings beyond the list.\n\n"
            + json.dumps(digest)
        )
        if provider == "openai":
            model = os.environ.get("GOLDENMATCH_WEAKNESS_LLM_MODEL", "gpt-4o-mini")
            text, _, _ = _call_openai(prompt, key, model, max_tokens=200)
        elif provider == "anthropic":
            model = os.environ.get(
                "GOLDENMATCH_WEAKNESS_LLM_MODEL", "claude-3-5-haiku-latest"
            )
            text, _, _ = _call_anthropic(prompt, key, model, max_tokens=200)
        else:
            return fallback
        text = (text or "").strip()
        return text or fallback
    except Exception:
        return fallback


def diagnose_config(
    df: pl.DataFrame,
    config: GoldenMatchConfig,
    result: Any,
    *,
    max_findings: int = 6,
    phrasing: str = "plain",
) -> dict:
    """Explain where an auto-built matching config is risky, in plain English.

    Args:
        df: the input frame the config was built from (used for profiling).
        config: the resolved ``GoldenMatchConfig``.
        result: a ``DedupeResult``-like object; ``.postflight_report`` and
            ``.clusters`` are read defensively (either may be absent/None).
        max_findings: cap on returned findings AFTER severity ranking.
        phrasing: ``"plain"`` (default, non-technical) or ``"technical"``.

    Returns:
        ``{"findings": list[dict], "summary_plain": str}``. ``findings`` is
        ranked high→low by severity and truncated to ``max_findings``. When no
        weakness fires, ``findings`` is empty and ``summary_plain`` says the
        rules look solid.

    Never raises on a valid ``(df, config, result)``: every detector runs
    inside ``_safe`` and missing signals are skipped, not treated as errors.
    """
    if phrasing not in ("plain", "technical"):
        phrasing = "plain"

    try:
        referenced = _collect_referenced_columns(config)
    except Exception:
        referenced = set()
    try:
        excluded = set(getattr(config, "exclude_columns", None) or [])
    except Exception:
        excluded = set()
    matchkey_cols = _matchkey_columns(config) - excluded

    profiles_by_name = _profiles_by_name(df)
    signals = _signals_of(result)
    clusters = _clusters_of(result)

    findings: list[dict] = []
    findings += _safe(_detect_source_admitted, referenced, excluded, phrasing)
    findings += _safe(
        _detect_id_admitted, referenced, excluded, profiles_by_name, phrasing
    )
    findings += _safe(_detect_oversized_block, signals, phrasing)
    findings += _safe(_detect_over_merge, signals, clusters, phrasing)
    findings += _safe(_detect_null_sink, matchkey_cols, profiles_by_name, phrasing)
    findings += _safe(
        _detect_low_signal_key, matchkey_cols, profiles_by_name, phrasing
    )

    # Rank high→low by severity (stable: preserves detector order within a tier
    # so output is fully deterministic), then truncate.
    findings.sort(key=lambda f: _SEVERITY_RANK.get(f.get("severity"), 99))
    if max_findings is not None and max_findings >= 0:
        findings = findings[:max_findings]

    summary = _template_summary(findings)
    summary = _maybe_llm_summary(findings, summary)

    return {"findings": findings, "summary_plain": summary}
