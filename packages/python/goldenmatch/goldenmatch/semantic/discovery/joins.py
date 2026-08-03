"""Certified join / foreign-key discovery (semantic-model discovery, Phase 3).

Given a set of source tables and the certified keys discovered per table (Phase 1
`discover_keys`), PROPOSE the foreign-key joins between them and — the differentiator
— PROVE each join's cardinality against the data with the SAME certifier the certify
wedge uses. So a discovered join comes **pre-graded**: a `JoinCandidate` carries the
trust verdict on its "one" side (is the referenced key actually unique at grain?), not
just a structural guess.

Discovery is hypothesis generation; certification is the falsification test:

  * **FK inference (the hypothesis).** A column in table B whose non-null values are a
    SUBSET of table A's certified key values — and whose semantic type is compatible —
    is a candidate foreign key `B.fk -> A.key`, a `many_to_one` join (many B rows point
    at one A row).
  * **Cardinality certification (the test).** The join is only sound if the "one" side
    (A's key) is genuinely unique at grain — otherwise a metric joined across it
    fans out and double-counts. That's exactly what `certify_cube_joins` checks, so the
    proposed join is re-proven in join context via a minimal `Cube` model.

Scoped to SINGLE-column foreign keys referencing the caller-supplied per-table keys;
compound FKs and self-referential joins are documented follow-ons. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.

Design: advisory only — it proposes + grades; a human approves. Never a black box.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate
from goldenmatch.semantic.discovery.keys import _NON_KEY_COL_TYPES, KeyCandidate

# A candidate FK column must carry real referential signal: enough distinct values to
# be a key reference, not a near-constant flag that trivially sits inside any key set.
_DEFAULT_MIN_FK_DISTINCT = 2
# The referenced key values a candidate FK covers must be a genuine containment, not a
# one-row accident. Require the FK's distinct values to be a (near-)subset of the key.
_DEFAULT_MIN_CONTAINMENT = 1.0


@dataclass
class JoinCandidate:
    """A proposed foreign-key join between two tables, already certified.

    `B.fk_column -> A.key_column` as a `many_to_one` join. `certificate` grades the
    "one" side (A's referenced key): the join is only trustworthy if that key is
    genuinely unique at grain. `signals` records the corroborating evidence
    (`value_subset` always; `type_match` when the FK and key share a semantic type).
    """

    from_table: str          # the "many" side — holds the foreign key
    from_column: str         # the foreign-key column on the many side
    to_table: str            # the "one" side — holds the referenced key
    to_column: str           # the referenced (certified) key column
    relationship: str        # always "many_to_one" for this slice
    certificate: KeyIntegrityCertificate  # the proof on the one-side key
    signals: list[str] = field(default_factory=list)
    containment: float = 1.0  # fraction of the FK's distinct values found in the key
    fk_null_rate: float = 0.0  # coverage context: how many many-side rows have no FK
    _profile: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trustworthy(self) -> bool:
        """The certifier's advisory pass/fail on the join's one-side key — unique at
        grain, no fan-out. An untrustworthy join would silently double-count a metric."""
        return self.certificate.is_trustworthy()

    @property
    def score(self) -> float:
        """Ranking score in [0, 1]: the one-side key's structural cleanliness,
        rewarded for corroborating signals + full containment. Trustworthiness is the
        primary sort key (see `discover_joins`); this breaks ties."""
        base = self.certificate.estimate
        bonus = 0.025 * (len(self.signals) - 1) + 0.025 * (self.containment >= 1.0)
        return min(1.0, base + bonus)


def _distinct_nonnull(table: Any, column: str) -> set:
    """Distinct non-null values of a column, as a set (best-effort; empty on error)."""
    from goldenmatch.core.frame import to_frame

    try:
        frame = to_frame(table)
        if column not in frame.columns:
            return set()
        col = frame.column(column)
        return {v for v in col.drop_nulls().unique().to_list() if v is not None}
    except Exception:  # noqa: BLE001 - a bad column never breaks the sweep
        return set()


def _null_rate(table: Any, column: str) -> float:
    from goldenmatch.core.frame import to_frame

    try:
        frame = to_frame(table)
        if column not in frame.columns or frame.height == 0:
            return 0.0
        col = frame.column(column)
        return col.null_count() / len(col)
    except Exception:  # noqa: BLE001
        return 0.0


def _key_columns(entry: Any) -> list[tuple[str, KeyCandidate | None]]:
    """Normalize a per-table `keys` entry into `[(column, candidate_or_None)]`.

    Accepts the output of `discover_keys` (`list[KeyCandidate]`), a single
    `KeyCandidate`, a bare column name, or a list of column names — so a caller can
    hand us either graded candidates or plain declared keys.
    """
    if entry is None:
        return []
    if isinstance(entry, KeyCandidate):
        entry = [entry]
    if isinstance(entry, str):
        return [(entry, None)]
    out: list[tuple[str, KeyCandidate | None]] = []
    for item in entry:
        if isinstance(item, KeyCandidate):
            # single-column keys only for this slice
            if len(item.columns) == 1:
                out.append((item.columns[0], item))
        elif isinstance(item, str):
            out.append((item, None))
    return out


def discover_joins(
    tables: dict[str, Any],
    keys: dict[str, Any],
    *,
    resolve: bool = False,
    min_fk_distinct: int = _DEFAULT_MIN_FK_DISTINCT,
    min_containment: float = _DEFAULT_MIN_CONTAINMENT,
    max_candidates: int = 64,
) -> list[JoinCandidate]:
    """Propose and certify foreign-key joins across a set of tables.

    Args:
        tables: `{table_name: table}` — the source tables (any input
            `certify_key_integrity` accepts).
        keys: `{table_name: keys}` — the referenced ("one" side) keys per table.
            Each value may be the `discover_keys` output (`list[KeyCandidate]`), a
            single `KeyCandidate`, a column name, or a list of column names. Only
            single-column keys are used as join targets in this slice; a `KeyCandidate`
            that is not trustworthy is skipped (an unsound grain makes an unsound join).
        resolve: forwarded to `certify_cube_joins` — also measures entity
            fragmentation / undercount on the one-side key via ER (fail-open).
        min_fk_distinct: minimum distinct non-null values for a column to be a
            candidate foreign key (filters near-constant flags).
        min_containment: minimum fraction of the FK's distinct values that must be
            found in the referenced key set (1.0 = strict subset).
        max_candidates: cap on returned candidates.

    Returns:
        `JoinCandidate`s ranked trustworthy-first, then by `score` (cleaner one-side
        key), then lower fan-out, then more corroborating signals. A join whose
        one-side key does NOT certify unique is returned flagged
        `is_trustworthy == False` — the loud signal that joining across it double-counts.
    """
    from goldenmatch.core.autoconfig import profile_columns
    from goldenmatch.semantic.cube import Cube, CubeJoin, certify_cube_joins

    # Profile every table once, so the semantic-type match is cheap.
    col_types: dict[str, dict[str, str]] = {}
    for name, table in tables.items():
        try:
            col_types[name] = {p.name: p.col_type for p in profile_columns(table)}
        except Exception:  # noqa: BLE001 - profiling is advisory; type_match just won't fire
            col_types[name] = {}

    candidates: list[JoinCandidate] = []
    for to_table, key_entry in keys.items():
        if to_table not in tables:
            continue
        for key_col, key_cand in _key_columns(key_entry):
            # An unsound grain makes an unsound join — skip a graded-but-untrustworthy
            # target key (a plain declared column, candidate None, is trusted to the
            # certifier below).
            if key_cand is not None and not key_cand.is_trustworthy:
                continue
            key_values = _distinct_nonnull(tables[to_table], key_col)
            if len(key_values) < min_fk_distinct:
                continue
            key_type = col_types.get(to_table, {}).get(key_col)

            for from_table, source in tables.items():
                if from_table == to_table:
                    continue  # self-joins are a documented follow-on
                from goldenmatch.core.frame import to_frame

                try:
                    source_cols = to_frame(source).columns
                except Exception:  # noqa: BLE001
                    continue
                for fk_col in source_cols:
                    fk_values = _distinct_nonnull(source, fk_col)
                    if len(fk_values) < min_fk_distinct:
                        continue
                    # A pure measure/date/geo column is a value, not a reference.
                    fk_type = col_types.get(from_table, {}).get(fk_col)
                    if fk_type in _NON_KEY_COL_TYPES:
                        continue
                    contained = len(fk_values & key_values)
                    containment = contained / len(fk_values)
                    if containment < min_containment:
                        continue

                    signals = ["value_subset"]
                    if key_type is not None and fk_type == key_type:
                        signals.append("type_match")

                    # Re-prove the one-side key's cardinality IN JOIN CONTEXT.
                    many = Cube(
                        name=from_table,
                        joins=[CubeJoin(
                            name=to_table,
                            relationship="many_to_one",
                            sql=f"{{CUBE}}.{fk_col} = {{{to_table}.{key_col}}}",
                        )],
                    )
                    try:
                        certified = certify_cube_joins(
                            [many], {from_table: source, to_table: tables[to_table]},
                            resolve=resolve,
                        )
                    except Exception:  # noqa: BLE001 - a bad pair never breaks the sweep
                        continue
                    if not certified:
                        continue
                    cert = certified[0]["certificate"]
                    candidates.append(
                        JoinCandidate(
                            from_table=from_table,
                            from_column=fk_col,
                            to_table=to_table,
                            to_column=key_col,
                            relationship="many_to_one",
                            certificate=cert,
                            signals=signals,
                            containment=containment,
                            fk_null_rate=_null_rate(source, fk_col),
                            _profile={"fk_type": fk_type, "key_type": key_type},
                        )
                    )

    # Trustworthy first, then cleaner one-side key (score desc), then lower fan-out,
    # then more corroborating signals, then names for determinism.
    candidates.sort(
        key=lambda j: (
            not j.is_trustworthy,
            -j.score,
            j.certificate.max_fan_out,
            -len(j.signals),
            j.from_table,
            j.from_column,
            j.to_table,
        )
    )
    return candidates[:max_candidates]
