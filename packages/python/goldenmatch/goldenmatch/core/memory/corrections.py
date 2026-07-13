"""Apply pair-level corrections during scoring."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from goldenmatch.core.memory.store import Correction, _canon_pair

if TYPE_CHECKING:
    from goldenmatch.core.memory.store import MemoryStore

log = logging.getLogger("goldenmatch.memory")


@dataclass
class CorrectionStats:
    """Statistics from applying corrections."""
    applied: int = 0
    stale: int = 0
    total_pairs: int = 0
    stale_pairs: list[tuple[int, int]] = field(default_factory=list)
    stale_ambiguous: int = 0
    stale_unanchorable: int = 0
    failed: bool = False
    error: str | None = None

    def validate(self) -> bool:
        """Sanity check the count invariant after apply_corrections finishes.

        Returns True when applied + stale + stale_ambiguous +
        stale_unanchorable <= total_pairs. On violation logs a warning and
        returns False — callers should NOT crash on a counting bug, but a
        violation does indicate something accidentally double-counted a pair.
        """
        total_classified = (
            self.applied + self.stale + self.stale_ambiguous
            + self.stale_unanchorable
        )
        if self.total_pairs > 0 and total_classified > self.total_pairs:
            log.warning(
                "CorrectionStats invariant violated: applied=%d stale=%d "
                "stale_ambiguous=%d stale_unanchorable=%d (sum=%d) > "
                "total_pairs=%d",
                self.applied, self.stale, self.stale_ambiguous,
                self.stale_unanchorable, total_classified, self.total_pairs,
            )
            return False
        return True


def build_row_lookup(df, fields: list[str]) -> dict[int, tuple]:
    """Build row ID to field values lookup once for all pairs."""
    from goldenmatch.core.frame import to_frame

    _f = to_frame(df)
    available = [f for f in fields if f in _f.columns]
    if "__row_id__" not in _f.columns:
        log.warning("DataFrame missing __row_id__ column, corrections cannot be applied")
        return {}
    if not available:
        log.warning("No matchkey fields found in DataFrame: %s", fields)
        return {}
    rows = _f.select_dicts(["__row_id__"] + available)
    return {r["__row_id__"]: tuple(r[f] for f in available) for r in rows}


def compute_field_hash(row_a_vals: tuple, row_b_vals: tuple) -> str:
    """Hash matched field values for staleness detection."""
    combined = "|".join(str(v) for v in row_a_vals + row_b_vals)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def compute_record_hash(df, row_id: int) -> str:
    """Hash content fields (sorted by name) for entity identity check.

    Excludes ``__row_id__`` so two runs over the same content produce the
    same hash even if row order changes. A4: dual-rep via the seam; the
    ``str(v)``-over-raw-values format is the PERSISTED contract -- do not
    change it (stored corrections re-anchor against it).
    """
    from goldenmatch.core.frame import to_frame

    _f = to_frame(df)
    filtered = _f.filter_in("__row_id__", [row_id])
    if filtered.height == 0:
        log.warning("Row ID %d not found in DataFrame, returning empty hash", row_id)
        return ""
    content_cols = sorted(c for c in _f.columns if c != "__row_id__")
    row = filtered.select_dicts(content_cols)[0]
    return hashlib.sha256(
        "|".join(str(row[c]) for c in content_cols).encode()
    ).hexdigest()[:16]


def _build_hash_to_rids(df) -> dict[str, list[int]]:
    """record_hash to [row_ids] map (A4: dual-rep via the seam).

    Format contract (PERSISTED in memory stores -- byte-stable): per-column
    polars-Utf8-cast strings (the seam's ``cast_str`` pins those semantics
    on both lanes, incl. the float formatter), "|"-joined, sha256[:16].
    Rows with ANY null value hash to ``None`` (the old ``concat_str``
    null-propagation + ``map_elements`` null skip) and land under the
    ``None`` key exactly as before -- their rids still count as present.
    """
    from goldenmatch.core.frame import to_frame

    _f = to_frame(df)
    sorted_cols = sorted(c for c in _f.columns if c != "__row_id__")
    rids = _f.column("__row_id__").to_list()
    col_vals = [_f.column(c).cast_str().to_list() for c in sorted_cols]
    out: dict[str, list[int]] = {}
    for i, rid in enumerate(rids):
        parts = [cv[i] for cv in col_vals]
        if any(p is None for p in parts):
            h = None
        else:
            h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
        out.setdefault(h, []).append(int(rid))  # type: ignore[arg-type]
    return out


def apply_corrections(
    scored_pairs: list[tuple[int, int, float]],
    store: MemoryStore,
    df,  # pl.DataFrame | pa.Table (A4: dual-rep via the seam)
    matchkey_fields: list[str],
    dataset: str | None = None,
    reanchor: bool = True,
) -> tuple[list[tuple[int, int, float]], CorrectionStats]:
    """Apply pair-level corrections to scored pairs.

    Direct row-ID match takes precedence; falls back to record_hash re-anchor
    when the original IDs are no longer present. Ambiguous re-anchors (current
    df has duplicate rows for either side) refuse to apply and surface as
    stale_ambiguous.
    """
    stats = CorrectionStats(total_pairs=len(scored_pairs))

    all_corrections = store.get_corrections(dataset=dataset)
    if not all_corrections:
        return scored_pairs, stats

    from goldenmatch.core.frame import to_frame as _tf_a4

    if "__row_id__" not in _tf_a4(df).columns:
        log.warning("DataFrame missing __row_id__ column, corrections cannot be applied")
        return scored_pairs, stats

    hash_to_rids = _build_hash_to_rids(df)
    current_rids = {rid for rids in hash_to_rids.values() for rid in rids}

    active: dict[tuple[int, int], Correction] = {}
    for c in all_corrections:
        if c.id_a in current_rids and c.id_b in current_rids:
            active[_canon_pair(c.id_a, c.id_b)] = c
            continue
        if not reanchor:
            stats.stale_unanchorable += 1
            stats.stale_pairs.append((c.id_a, c.id_b))
            log.debug(
                "Correction unanchorable (row IDs gone, no usable record_hash): (%d, %d)",
                c.id_a, c.id_b,
            )
            continue
        rh = c.record_hash or ""
        if ":" not in rh:
            stats.stale_unanchorable += 1
            stats.stale_pairs.append((c.id_a, c.id_b))
            log.debug(
                "Correction unanchorable (row IDs gone, no usable record_hash): (%d, %d)",
                c.id_a, c.id_b,
            )
            continue
        ha, hb = rh.split(":", 1)
        cands_a = hash_to_rids.get(ha, []) if ha else []
        cands_b = hash_to_rids.get(hb, []) if hb else []
        if len(cands_a) == 1 and len(cands_b) == 1:
            active[_canon_pair(cands_a[0], cands_b[0])] = c
        elif cands_a and cands_b:
            stats.stale_ambiguous += 1
            stats.stale_pairs.append((c.id_a, c.id_b))
        else:
            stats.stale_unanchorable += 1
            stats.stale_pairs.append((c.id_a, c.id_b))
            log.debug(
                "Correction unanchorable (row IDs gone, no usable record_hash): (%d, %d)",
                c.id_a, c.id_b,
            )

    if not active:
        return scored_pairs, stats

    field_lookup = build_row_lookup(df, matchkey_fields)

    record_hashes: dict[int, str] = {}
    for (id_a, id_b) in active.keys():
        if id_a not in record_hashes:
            record_hashes[id_a] = compute_record_hash(df, id_a)
        if id_b not in record_hashes:
            record_hashes[id_b] = compute_record_hash(df, id_b)

    adjusted = []
    for id_a, id_b, score in scored_pairs:
        correction = active.get(_canon_pair(id_a, id_b))
        if correction is None:
            adjusted.append((id_a, id_b, score))
            continue

        if id_a not in field_lookup or id_b not in field_lookup:
            log.warning("Row ID(s) not in lookup for correction (%d, %d), marking stale",
                       id_a, id_b)
            adjusted.append((id_a, id_b, score))
            stats.stale += 1
            stats.stale_pairs.append((id_a, id_b))
            continue

        current_field_hash = compute_field_hash(
            field_lookup[id_a], field_lookup[id_b],
        )
        ca, cb = _canon_pair(id_a, id_b)
        current_record_hash = (
            f"{record_hashes.get(ca, '')}:{record_hashes.get(cb, '')}"
        )

        hashes_empty = (not correction.field_hash and not correction.record_hash)
        hashes_match = (
            current_field_hash == correction.field_hash
            and current_record_hash == correction.record_hash
        )

        if hashes_empty or hashes_match:
            new_score = 1.0 if correction.decision == "approve" else 0.0
            adjusted.append((id_a, id_b, new_score))
            stats.applied += 1
        else:
            adjusted.append((id_a, id_b, score))
            stats.stale += 1
            stats.stale_pairs.append((id_a, id_b))

    log.info(
        "Corrections: %d applied, %d stale, %d ambiguous, %d unanchorable, %d total pairs",
        stats.applied, stats.stale, stats.stale_ambiguous,
        stats.stale_unanchorable, stats.total_pairs,
    )
    stats.validate()
    return adjusted, stats
