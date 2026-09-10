"""Pass-signature candidate pruning for multi-pass FS blocking.

``GOLDENMATCH_FS_SIGNATURE_PRUNE`` (default OFF). A measurement lever, not a
default: it exists to answer whether pruning multi-pass candidates by WHICH
passes a pair co-fires in improves end-to-end Fellegi-Sunter F1.

Why the signature and not the count. Standard meta-blocking weights a pair by
how many blocks it shares. Auto-config's passes are correlated -- on
historical_50k five of eight derive from name fields -- so namesakes collect
several shared blocks from one fact while typo'd true duplicates fall out of
the name passes. Measured there, count-based pruning loses ~20 points of pair
completeness (``scripts/bench_er_headtohead/measure_blocking_signatures.py``).

How it decides, without labels:

1. Per pass, compute each row's block key; a pair co-fires in pass k when both
   rows have the same valid key. Oversized-block splitting is ignored, so a pair
   separated only by an auto-split still counts as co-firing.
2. Estimate how many DISTINCT candidate pairs carry each signature by sampling
   (pass, block, pair) instances uniformly and weighting each by 1/popcount --
   a pair reachable from c passes is c times as likely to be drawn.
3. Merge passes whose phi coefficient over the candidate pairs is at least
   ``_PHI_GROUP`` into one "any member fired" group, so correlated passes count
   once.
4. Apply a rule over the groups a pair fires:

   * ``dominant`` (``1`` / ``on``): drop a pair that fires exactly one group
     when that group fires on at least ``_DOMINANT_SHARE`` of all candidates --
     the namesake-sized group whose lone agreement is mostly noise. Everything
     else is kept.
   * ``multi``: keep only pairs that fire at least two groups.

An EM mixture over the same groups was tried first and rejected: its
rare-match mode existed on historical_50k but not on a small synthetic world
with namesakes, where every start converged to "match = fired anything but the
name group". The rules above have no modes to land in.

The pruner declines -- returns ``None`` and logs why -- when there is nothing to
decide: fewer than two passes, every pass merged into one group, or (for
``dominant``) no group large enough to count as dominant. A declined pruner
changes nothing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

ENV = "GOLDENMATCH_FS_SIGNATURE_PRUNE"
RULES = ("dominant", "multi")
_MAX_PASSES = 16
_PHI_GROUP = 0.2
_DOMINANT_SHARE = 0.3
_SAMPLE_INSTANCES = 200_000
_SEED = 1729
_SENTINELS = ("nan", "null", "none")


def signature_prune_rule() -> str | None:
    """The pruning rule, or ``None`` when the lever is off.

    Unset / ``0`` / ``off`` -> off. ``1`` / ``on`` / ``dominant`` -> ``dominant``.
    ``multi`` -> ``multi``. Anything else logs a warning and stays off.
    """
    raw = os.environ.get(ENV, "").strip().lower()
    if raw in ("", "0", "false", "off", "no"):
        return None
    if raw in ("1", "true", "on", "yes", "dominant"):
        return "dominant"
    if raw == "multi":
        return "multi"
    logger.warning("%s=%r is not one of off/on/%s; pruning stays OFF", ENV, raw, "/".join(RULES))
    return None


def _pass_key_codes(frame: Any, key: Any) -> np.ndarray:
    """Integer block-key code per row for one pass; -1 where the key is invalid.

    Invalid mirrors ``Frame.filter_valid_key``: null, or a stringified missing
    sentinel. Codes are only comparable within one pass.
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    field_transforms = getattr(key, "field_transforms", None) or None
    col = frame.derive_block_key(
        list(key.fields), list(key.transforms or []), field_transforms=field_transforms
    )
    arr = col.to_arrow()
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    if not pa.types.is_string(arr.type) and not pa.types.is_large_string(arr.type):
        arr = pc.cast(arr, pa.large_string())  # pyright: ignore[reportAttributeAccessIssue]
    normalized = pc.utf8_lower(pc.utf8_trim_whitespace(arr))  # pyright: ignore[reportAttributeAccessIssue]
    sentinel = pc.fill_null(  # pyright: ignore[reportAttributeAccessIssue]
        pc.is_in(normalized, value_set=pa.array(list(_SENTINELS))), False  # pyright: ignore[reportAttributeAccessIssue]
    )
    invalid = pc.or_(pc.is_null(arr), sentinel).to_numpy(zero_copy_only=False)  # pyright: ignore[reportAttributeAccessIssue]
    indices = pc.dictionary_encode(arr).indices  # pyright: ignore[reportAttributeAccessIssue]
    codes = pc.fill_null(indices, -1).to_numpy(zero_copy_only=False).astype(np.int64)  # pyright: ignore[reportAttributeAccessIssue]
    codes[invalid] = -1
    return codes


def _row_signatures(codes: list[np.ndarray], rows_a: np.ndarray, rows_b: np.ndarray) -> np.ndarray:
    sig = np.zeros(len(rows_a), dtype=np.int64)
    for k, c in enumerate(codes):
        ca = c[rows_a]
        sig |= ((ca >= 0) & (ca == c[rows_b])).astype(np.int64) << k
    return sig


def _popcount(values: np.ndarray, n_bits: int) -> np.ndarray:
    out = np.zeros(len(values), dtype=np.int64)
    for k in range(n_bits):
        out += (values >> k) & 1
    return out


def _sample_signature_counts(
    codes: list[np.ndarray], n_instances: int, rng: np.random.Generator
) -> tuple[dict[int, float], float]:
    """Estimated DISTINCT candidate pairs per signature, and the pass-instance total.

    A (pass, block, pair) instance is drawn uniformly from all within-block pairs
    of all passes. A distinct pair with signature popcount c has exactly c
    instances, so weighting each draw by 1/c and scaling by total/draws is an
    unbiased estimate of distinct-pair counts per signature.
    """
    per_pass: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None] = []
    totals: list[float] = []
    for c in codes:
        idx = np.flatnonzero(c >= 0)
        order = idx[np.argsort(c[idx], kind="stable")]
        keys = c[order]
        if len(keys) < 2:
            per_pass.append(None)
            totals.append(0.0)
            continue
        starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
        sizes = np.diff(np.r_[starts, len(keys)])
        big = sizes >= 2
        starts, sizes = starts[big], sizes[big]
        pairs = sizes.astype(np.float64) * (sizes - 1) / 2.0
        if len(sizes) == 0:
            per_pass.append(None)
            totals.append(0.0)
            continue
        per_pass.append((order, starts, sizes, np.cumsum(pairs)))
        totals.append(float(pairs.sum()))
    grand = float(sum(totals))
    if grand <= 0:
        return {}, 0.0

    alloc = rng.multinomial(n_instances, np.asarray(totals) / grand)
    sig_parts: list[np.ndarray] = []
    weight_parts: list[np.ndarray] = []
    for k, entry in enumerate(per_pass):
        s = int(alloc[k])
        if entry is None or s == 0:
            continue
        order, starts, sizes, cdf = entry
        blk = np.searchsorted(cdf, rng.random(s) * cdf[-1], side="right")
        blk = np.minimum(blk, len(sizes) - 1)
        n = sizes[blk]
        i = np.minimum((rng.random(s) * n).astype(np.int64), n - 1)
        j = np.minimum((rng.random(s) * (n - 1)).astype(np.int64), n - 2)
        j = j + (j >= i)
        rows_a = order[starts[blk] + i]
        rows_b = order[starts[blk] + j]
        sig = _row_signatures(codes, rows_a, rows_b)
        sig_parts.append(sig)
        weight_parts.append(1.0 / _popcount(sig, len(codes)))
    sigs = np.concatenate(sig_parts)
    weights = np.concatenate(weight_parts)
    uniq, inverse = np.unique(sigs, return_inverse=True)
    est = np.bincount(inverse, weights=weights) * (grand / n_instances)
    return {int(u): float(e) for u, e in zip(uniq, est)}, grand


def _phi_matrix(masks: np.ndarray, counts: np.ndarray, n: int) -> np.ndarray:
    bits = ((masks[:, None] >> np.arange(n)) & 1).astype(np.float64)
    total = counts.sum()
    p = (counts @ bits) / total
    joint = (bits * counts[:, None]).T @ bits / total
    var = p * (1 - p)
    den = np.sqrt(np.outer(var, var))
    with np.errstate(divide="ignore", invalid="ignore"):
        phi = np.where(den > 0, (joint - np.outer(p, p)) / den, 0.0)
    # phi is undefined for a pass with no variance. Two passes that fire on
    # EXACTLY the same candidates (both on all, or both on none) carry the same
    # information, so they merge; otherwise a constant pass stays apart.
    constant = var <= 0
    same_constant = np.outer(constant, constant) & np.isclose(p[:, None], p[None, :])
    return np.where(same_constant, 1.0, phi)


def _groups(phi: np.ndarray, threshold: float) -> list[list[int]]:
    """Single-linkage merge of passes whose phi clears the threshold."""
    n = phi.shape[0]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(n):
        for b in range(a + 1, n):
            if phi[a, b] >= threshold:
                parent[find(a)] = find(b)
    by_root: dict[int, list[int]] = {}
    for k in range(n):
        by_root.setdefault(find(k), []).append(k)
    return sorted(by_root.values())


def _to_group_masks(masks: np.ndarray, groups: list[list[int]]) -> np.ndarray:
    out = np.zeros(len(masks), dtype=np.int64)
    for g, members in enumerate(groups):
        member_bits = 0
        for k in members:
            member_bits |= 1 << k
        out |= ((masks & member_bits) != 0).astype(np.int64) << g
    return out


@dataclass
class SignaturePruner:
    """Keeps or drops a candidate pair by the pass signature of its two rows."""

    codes: list[np.ndarray]
    row_ids: np.ndarray
    keep_table: np.ndarray
    report: dict[str, Any] = field(default_factory=lambda: {})
    _dense_pos: np.ndarray | None = None
    _sorted_ids: np.ndarray | None = None
    _sorted_pos: np.ndarray | None = None

    def __post_init__(self) -> None:
        ids = np.asarray(self.row_ids, dtype=np.int64)
        if len(ids) and ids.min() >= 0 and ids.max() < 4 * len(ids) + 1024:
            pos = np.full(int(ids.max()) + 1, -1, dtype=np.int64)
            pos[ids] = np.arange(len(ids), dtype=np.int64)
            self._dense_pos = pos
        else:
            order = np.argsort(ids, kind="stable")
            self._sorted_ids = ids[order]
            self._sorted_pos = order.astype(np.int64)

    def _positions(self, ids: np.ndarray) -> np.ndarray:
        ids = np.asarray(ids, dtype=np.int64)
        if self._dense_pos is not None:
            out = np.full(len(ids), -1, dtype=np.int64)
            inside = (ids >= 0) & (ids < len(self._dense_pos))
            out[inside] = self._dense_pos[ids[inside]]
            return out
        assert self._sorted_ids is not None and self._sorted_pos is not None
        if len(self._sorted_ids) == 0:
            return np.full(len(ids), -1, dtype=np.int64)
        at = np.searchsorted(self._sorted_ids, ids)
        at = np.minimum(at, len(self._sorted_ids) - 1)
        found = self._sorted_ids[at] == ids
        return np.where(found, self._sorted_pos[at], -1)

    def keep_mask(self, id_a: Any, id_b: Any) -> np.ndarray:
        """True for pairs to keep. A pair with a row the pruner never saw is kept."""
        a = self._positions(np.asarray(id_a))
        b = self._positions(np.asarray(id_b))
        known = (a >= 0) & (b >= 0)
        keep = np.ones(len(a), dtype=bool)
        if known.any():
            sig = _row_signatures(self.codes, a[known], b[known])
            keep[known] = self.keep_table[sig]
        return keep

    def filter_pairs(self, pairs: list[Any]) -> list[Any]:
        """Filter ``(id_a, id_b, ...)`` tuples, preserving order."""
        if not pairs:
            return pairs
        a = np.fromiter((p[0] for p in pairs), dtype=np.int64, count=len(pairs))
        b = np.fromiter((p[1] for p in pairs), dtype=np.int64, count=len(pairs))
        mask = self.keep_mask(a, b)
        self._log_scored(int(mask.sum()), len(pairs))
        return [p for p, k in zip(pairs, mask) if k]

    def _log_scored(self, kept: int, total: int) -> None:
        # WARNING while the lever is under evaluation: a filter that keeps every
        # scored pair and one that never ran must not look the same in a log.
        logger.warning(
            "FS signature prune (%s): scored pairs kept %d of %d", self.report.get("rule"), kept, total
        )

    def filter_table(self, table: Any) -> Any:
        """Filter a pair-stream ``pa.Table`` with ``id_a`` / ``id_b`` columns."""
        import pyarrow as pa

        if table.num_rows == 0:
            self._log_scored(0, 0)
            return table
        a = table.column("id_a").to_numpy()
        b = table.column("id_b").to_numpy()
        mask = self.keep_mask(a, b)
        self._log_scored(int(mask.sum()), table.num_rows)
        return table.filter(pa.array(mask))


def _fit_from_codes(
    codes: list[np.ndarray],
    row_ids: np.ndarray,
    rule: str,
    *,
    n_instances: int = _SAMPLE_INSTANCES,
    seed: int = _SEED,
    labels: list[str] | None = None,
) -> SignaturePruner | None:
    if rule not in RULES:
        raise ValueError(f"unknown signature-prune rule {rule!r}; expected one of {RULES}")
    n = len(codes)
    if n < 2:
        logger.info("FS signature prune: DECLINED -- %d blocking pass(es), nothing to decide", n)
        return None
    if n > _MAX_PASSES:
        logger.warning(
            "FS signature prune: DECLINED -- %d passes exceeds the %d-pass signature table",
            n, _MAX_PASSES,
        )
        return None
    rng = np.random.default_rng(seed)
    counts_by_sig, instances = _sample_signature_counts(codes, n_instances, rng)
    if not counts_by_sig:
        logger.info("FS signature prune: DECLINED -- no within-block pairs")
        return None
    masks = np.fromiter(counts_by_sig.keys(), dtype=np.int64)
    counts = np.fromiter(counts_by_sig.values(), dtype=np.float64)

    groups = _groups(_phi_matrix(masks, counts, n), _PHI_GROUP)
    names = labels or [f"pass{k}" for k in range(n)]
    group_names = [[names[k] for k in g] for g in groups]
    if len(groups) < 2:
        logger.warning(
            "FS signature prune: DECLINED -- all %d passes co-fire (phi >= %.2f) into one group %s",
            n, _PHI_GROUP, group_names,
        )
        return None

    gmasks = _to_group_masks(masks, groups)
    total = float(counts.sum())
    n_groups = len(groups)
    fire = np.array(
        [float(counts[((gmasks >> g) & 1) == 1].sum()) / total for g in range(n_groups)]
    )
    all_group_masks = np.arange(1 << n_groups, dtype=np.int64)
    pop = _popcount(all_group_masks, n_groups)
    if rule == "multi":
        group_keep = pop >= 2
    else:
        dominant = fire >= _DOMINANT_SHARE
        if not dominant.any():
            logger.warning(
                "FS signature prune: DECLINED -- no pass group fires on >= %.0f%% of candidates "
                "(groups %s, fire rates %s)",
                100 * _DOMINANT_SHARE, group_names, [round(float(f), 3) for f in fire],
            )
            return None
        lone_dominant = np.zeros(len(all_group_masks), dtype=bool)
        for g in np.flatnonzero(dominant):
            lone_dominant |= all_group_masks == (1 << int(g))
        group_keep = ~lone_dominant
    group_keep[0] = True  # a pair that fires nothing is not a candidate; never drop by accident

    keep_table = group_keep[_to_group_masks(np.arange(1 << n, dtype=np.int64), groups)]
    kept = float(counts[keep_table[masks]].sum())
    report: dict[str, Any] = {
        "rule": rule,
        "passes": n,
        "groups": group_names,
        "fire_rates": [float(f) for f in fire],
        "est_candidates": total,
        "est_kept": kept,
        "sample_instances": n_instances,
        "pass_instances": instances,
    }
    logger.warning(
        "FS signature prune (%s): %d passes -> %d groups %s; group fire rates %s; "
        "keeps an estimated %.0f of %.0f candidate pairs (%.1f%%)",
        rule, n, n_groups, group_names, [round(float(f), 3) for f in fire],
        kept, total, 100.0 * kept / total if total else 0.0,
    )
    return SignaturePruner(codes=codes, row_ids=row_ids, keep_table=keep_table, report=report)


def fit_signature_pruner(
    frame: Any,
    blocking_config: Any,
    rule: str,
    *,
    n_instances: int = _SAMPLE_INSTANCES,
    seed: int = _SEED,
) -> SignaturePruner | None:
    """Fit a pruner on the frame the blocks are built from. ``None`` = declined."""
    from goldenmatch.core.frame import to_frame

    strategy = getattr(blocking_config, "strategy", None)
    if strategy not in ("static", "multi_pass"):
        logger.info(
            "FS signature prune: DECLINED -- strategy %r is not field-key blocking", strategy
        )
        return None
    keys = list(blocking_config.resolved_keys())
    if len(keys) < 2:
        logger.info("FS signature prune: DECLINED -- %d blocking pass(es)", len(keys))
        return None
    if hasattr(frame, "collect") and not hasattr(frame, "height"):
        frame = frame.collect()
    fr = to_frame(frame)
    codes = [_pass_key_codes(fr, key) for key in keys]
    row_ids = np.asarray(fr.column("__row_id__").to_numpy(), dtype=np.int64)
    labels = [
        "{}[{}]".format(
            "/".join(k.fields),
            "+".join(t for t in (k.transforms or []) if t not in ("lowercase", "strip")) or "raw",
        )
        for k in keys
    ]
    return _fit_from_codes(codes, row_ids, rule, n_instances=n_instances, seed=seed, labels=labels)
