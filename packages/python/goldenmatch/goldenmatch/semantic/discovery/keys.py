"""Certified key discovery (semantic-model discovery, Phase 1).

The generative half of the semantic wedge: given a source table, PROPOSE its
candidate entity keys and — the differentiator — PROVE each one against the data
with `certify_key_integrity`. So a discovered key comes **pre-graded**: a
`KeyCandidate` carries the trust verdict, not just a guess.

Discovery is hypothesis generation; certification is the falsification test. Three
cheap signals propose candidates; the certifier decides which are real:

  * **identifier** — the column classifies as an id (`col_type == "identifier"`).
  * **cardinality** — near-unique (`cardinality_ratio >= min_cardinality`), the
    shape of a primary key.
  * **fd** — a functional-dependency determinant (`fd_identity_scores`): a column
    that determines the rest of the row is, by construction, a key backbone. (FD
    discovery excludes perfectly-unique columns as trivial, so it COMPLEMENTS the
    cardinality signal rather than duplicating it.)

Every proposed column is then certified: unique at grain? measure fan-out? The
result is ranked trustworthy-first. Scoped to SINGLE-column keys for this slice;
compound/grain-ambiguous keys are a documented follow-on (see the design spec
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`).

Design: advisory only — it proposes + grades; a human approves. Never a black box.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate

# A near-unique column is primary-key-shaped. 0.9 (not 1.0) admits keys with a few
# dup rows so the certifier can REPORT the fan-out rather than the candidate being
# silently dropped before it's graded.
_DEFAULT_MIN_CARDINALITY = 0.9
# A functional-dependency determinant is only a key-backbone signal when the FD is
# (near-)strict; a weak approximate FD is a dimension, not a key.
_DEFAULT_FD_MIN_CONFIDENCE = 0.98

# An entity key is not a measure or a timestamp. A numeric/date/geo/description
# column that happens to be unique in a small sample is a MEASURE or attribute, not
# a key — so the cardinality signal skips these col_types. (The `identifier` and
# `fd` signals are unaffected; a genuine id classifies as `identifier`.)
_NON_KEY_COL_TYPES = frozenset({"numeric", "date", "geo", "description"})


@dataclass
class KeyCandidate:
    """A proposed entity key for a table, already certified against the data.

    `signals` records which cheap heuristics proposed it (`identifier` /
    `cardinality` / `fd`); `certificate` is the proof. The candidate is only
    trustworthy if the certifier says so — the signals are hypotheses, not verdicts.
    """

    columns: list[str]
    signals: list[str]
    certificate: KeyIntegrityCertificate
    fd_confidence: float | None = None  # the FD determinant strength, when the fd signal fired
    _profile: dict[str, Any] = field(default_factory=dict)  # cardinality_ratio / null_rate for context

    @property
    def is_trustworthy(self) -> bool:
        """The certifier's advisory pass/fail — unique at grain, no fan-out."""
        return self.certificate.is_trustworthy()

    @property
    def score(self) -> float:
        """Ranking score in [0, 1]: the structural cleanliness estimate, lightly
        rewarded for corroborating signals. Trustworthiness is the primary sort
        key (see `discover_keys`); this breaks ties among same-verdict candidates."""
        base = self.certificate.estimate
        # up to +0.05 for multiple corroborating signals (bounded, never overtakes
        # a genuinely cleaner key).
        return min(1.0, base + 0.025 * (len(self.signals) - 1))


def _to_polars(table: Any):
    """Best-effort polars view for the FD signal, which is polars-typed. Returns
    None when polars is unavailable or the table can't be coerced (the fd signal is
    then skipped — fail-open, the other two signals still fire)."""
    try:
        import polars as pl
    except ImportError:
        return None
    try:
        if isinstance(table, pl.DataFrame):
            return table
        import pyarrow as pa

        if isinstance(table, pa.Table):
            return pl.from_arrow(table)
        return pl.DataFrame(table)
    except Exception:  # noqa: BLE001 - fd is advisory; never break discovery
        return None


def discover_keys(
    table: Any,
    *,
    min_cardinality: float = _DEFAULT_MIN_CARDINALITY,
    fd_min_confidence: float = _DEFAULT_FD_MIN_CONFIDENCE,
    max_candidates: int = 8,
) -> list[KeyCandidate]:
    """Propose and certify single-column entity keys for one table.

    Args:
        table: the source table (pyarrow / polars / pandas / dict) — same inputs
            `certify_key_integrity` accepts.
        min_cardinality: near-unique threshold for the cardinality signal.
        fd_min_confidence: minimum FD determinant strength for the fd signal.
        max_candidates: cap on returned candidates.

    Returns:
        `KeyCandidate`s ranked trustworthy-first, then by `score` (cleaner keys),
        then fan-out (lower is better), then signal count. A table with no clean
        key returns candidates all flagged `is_trustworthy == False` — the loud
        signal that a metric on this grain would double-count.
    """
    from goldenmatch.core.autoconfig import profile_columns
    from goldenmatch.core.quality import fd_identity_scores
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    pl_frame = _to_polars(table)
    # profile_columns coerces via the Frame seam (accepts arrow/polars); prefer the
    # polars view when we have it so profiling + FD see the same frame.
    profiles = profile_columns(pl_frame if pl_frame is not None else table)
    by_name = {p.name: p for p in profiles}

    # signal -> per-column. Single-column candidates only for this slice.
    signals: dict[str, set[str]] = {}
    for p in profiles:
        col_signals: set[str] = set()
        if p.col_type == "identifier":
            col_signals.add("identifier")
        if p.cardinality_ratio >= min_cardinality and p.col_type not in _NON_KEY_COL_TYPES:
            col_signals.add("cardinality")
        if col_signals:
            signals.setdefault(p.name, set()).update(col_signals)

    # FD signal (fail-open): a strong determinant is a key backbone. FD discovery
    # excludes cardinality-1.0 columns, so this ADDS anchors the cardinality signal
    # can't see — but only admit strict-ish FDs as *key* candidates.
    fd_conf: dict[str, float] = {}
    fd_scores = fd_identity_scores(pl_frame) if pl_frame is not None else None
    if fd_scores:
        for det, conf in fd_scores.items():
            if det in by_name and conf >= fd_min_confidence:
                signals.setdefault(det, set()).add("fd")
                fd_conf[det] = conf

    candidates: list[KeyCandidate] = []
    for col, sigs in signals.items():
        try:
            cert = certify_key_integrity(table, key=col)
        except Exception:  # noqa: BLE001 - a bad column never breaks the sweep
            continue
        prof = by_name.get(col)
        candidates.append(
            KeyCandidate(
                columns=[col],
                signals=sorted(sigs),
                certificate=cert,
                fd_confidence=fd_conf.get(col),
                _profile=(
                    {"cardinality_ratio": prof.cardinality_ratio, "null_rate": prof.null_rate}
                    if prof is not None
                    else {}
                ),
            )
        )

    # Trustworthy first, then cleaner (score desc), then lower fan-out, then more
    # corroborating signals, then name for determinism.
    candidates.sort(
        key=lambda k: (
            not k.is_trustworthy,
            -k.score,
            k.certificate.max_fan_out,
            -len(k.signals),
            k.columns[0],
        )
    )
    return candidates[:max_candidates]
