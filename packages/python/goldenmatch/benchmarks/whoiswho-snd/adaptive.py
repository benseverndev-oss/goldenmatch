"""Per-name adaptive co-author threshold -- the recall fix.

The recall leak in the global-threshold `relational` engine: a single SHARED
specific co-author is the atomic same-person signal, but its Jaccard value
depends on set sizes. Two papers with a combined `U` distinct co-authors that
share exactly one collaborator score `1/U`. A GLOBAL threshold (0.15) therefore
misses every single-shared pair in big-collaboration blocks (U > ~7) -- which is
exactly where a prolific author's papers fail to link and their cluster shatters.

Per-name adaptivity sets the bar near `alpha / U_typical` for each name, so
"share >= ~alpha specific co-authors" clears it at ANY block density. It is
UNSUPERVISED (reads only the block's own co-author-size distribution, never the
labels), and clamped to `[t_min, t_max]` so it can never drop so low that a
coincidental overlap triggers a transitive union-find over-merge (precision
guard). Small-block names get a HIGHER bar (precision), big-collaboration names a
LOWER bar (recall) -- adaptive in both directions.
"""
from __future__ import annotations

import os
import statistics

from normalize import decode_set


def per_name_coauthor_threshold(
    coauthor_cells,
    *,
    alpha: float | None = None,
    t_min: float | None = None,
    t_max: float | None = None,
) -> float:
    """Adaptive co-author Jaccard threshold for one name-block.

    ``coauthor_cells`` are the block's ``coauthors`` column values ("|"-delimited
    sets). Returns a threshold in ``[t_min, t_max]``. Env overrides (for tuning):
    ``SND_ADAPT_ALPHA`` / ``SND_ADAPT_TMIN`` / ``SND_ADAPT_TMAX``.
    """
    alpha = float(os.environ.get("SND_ADAPT_ALPHA", "1.0")) if alpha is None else alpha
    t_min = float(os.environ.get("SND_ADAPT_TMIN", "0.06")) if t_min is None else t_min
    t_max = float(os.environ.get("SND_ADAPT_TMAX", "0.20")) if t_max is None else t_max

    sizes = [len(decode_set(c)) for c in coauthor_cells]
    nz = [s for s in sizes if s > 0]
    if not nz:
        # no co-author signal in this block -> keep the strict bar; the orgtext
        # matchkey (in the relational engine) carries these.
        return t_max
    med = statistics.median(nz)
    # typical union of two papers' co-author sets ~= 2*median - (small overlap).
    # one shared co-author over U distinct -> Jaccard 1/U, so alpha/U targets the
    # "share >= alpha co-authors" accept rule independent of block density.
    u_typical = max(2.0 * med - 1.0, 1.0)
    t = alpha / u_typical
    return max(t_min, min(t_max, t))
