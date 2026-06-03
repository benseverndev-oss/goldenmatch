"""Quantify the blast radius of three Phase-2 parity relaxations.

Runs a realistic zero-config ``dedupe_df`` over the ``realistic_person``
fixture, captures the RAW ``all_pairs`` stream (canonicalized but NOT
max-deduped, exactly as fed to clustering) plus the cluster partition via a
monkeypatch on ``build_clusters``, then quantifies three relaxations:

  R1  LAST-WINS vs MAX dedup of per-cluster ``pair_scores``.
  R2  Order-free ``avg_edge`` epsilon-band near the weak boundary.
  R3  Bottleneck / MST tie-break ambiguity.

This RUNS IN CI (large-new-64GB), NOT locally -- the dev box hangs on
``import goldenmatch`` / ``import polars``. See
.github/workflows/count-max-vs-last.yml.

Capture mechanism
-----------------
The pipeline binds ``build_clusters`` as a module-level name in
``goldenmatch.core.pipeline`` (``from goldenmatch.core.cluster import
build_clusters``, pipeline.py:124) and calls it with
``build_clusters(all_pairs, all_ids, ...)`` on the default path
(pipeline.py:1521, the ``else`` branch -- columnar pipeline and frames-out are
both gate-OFF by default). We monkeypatch ``goldenmatch.core.pipeline.
build_clusters`` to stash its ``pairs`` arg (the raw ``all_pairs`` list) and the
returned ``dict[int, dict]`` (members / size / oversized -- cluster.py:807-812)
into module globals, then run a normal ``dedupe_df``.

``member_to_cid`` is rebuilt from the returned clusters' ``members``; each
cluster's pairs are restricted to in-cluster pairs (both endpoints same cid),
exactly as ``_bucket_pairs`` does (cluster_pairscores.py:12-26: keep a pair iff
``member_to_cid[a] == member_to_cid[b]``, keyed by ``(a, b)`` as given).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Reuse the realistic-person fixture (tests/fixtures/realistic_person.py).
# Surnames distribute across soundex codes (drawn from refdata.surnames) so
# blocking + scoring does NOT hang -- this is the fixture the Arrow-roadmap
# benches use. See packages/.../tests/fixtures/realistic_person.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))


# ── Capture globals (populated by the monkeypatch) ──────────────────────────
_CAPTURED_PAIRS: list[tuple[int, int, float]] | None = None
_CAPTURED_CLUSTERS: dict | None = None


def _run_dedupe_and_capture(n: int) -> None:
    """Run zero-config dedupe over an n-row fixture, capturing all_pairs +
    the cluster partition via a monkeypatch on pipeline.build_clusters."""
    global _CAPTURED_PAIRS, _CAPTURED_CLUSTERS
    _CAPTURED_PAIRS = None
    _CAPTURED_CLUSTERS = None

    import goldenmatch.core.pipeline as _pipeline
    from fixtures.realistic_person import realistic_person_df
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_df

    df = realistic_person_df(n)

    # Realistic matchkey config the customer actually gets (zero-config), with
    # rerank stripped: auto-config may enable a cross-encoder rerank that
    # downloads an HF model -> offline CI fails. Stripping rerank is safe here;
    # it doesn't change how often duplicate canonical pairs occur. Mirrors
    # tests/test_autoconfig_regressions.py
    # (test_dedupe_df_interaction_all_three_fixes_together).
    config = auto_configure_df(df)
    for mk in config.get_matchkeys():
        if getattr(mk, "rerank", False):
            mk.rerank = False

    _orig_build_clusters = _pipeline.build_clusters

    def _patched(pairs, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        global _CAPTURED_PAIRS, _CAPTURED_CLUSTERS
        # `pairs` is the raw all_pairs list (canonicalized, NOT max-deduped)
        # the pipeline passes positionally. Materialize a stable copy.
        _CAPTURED_PAIRS = list(pairs)
        result = _orig_build_clusters(pairs, *args, **kwargs)
        _CAPTURED_CLUSTERS = result
        return result

    _pipeline.build_clusters = _patched
    try:
        # df.height >= 100k raises ControllerNotConfidentError on a RED commit
        # when confidence_required=True (project memory). This is a measurement
        # run, not a production commit -- relax it so the realistic pipeline
        # actually completes and we capture the partition.
        dedupe_df(df, config=config, confidence_required=False)
    finally:
        _pipeline.build_clusters = _orig_build_clusters

    if _CAPTURED_PAIRS is None or _CAPTURED_CLUSTERS is None:
        raise RuntimeError(
            "monkeypatch did not fire -- pipeline.build_clusters was not called "
            "(columnar pipeline or frames-out path may be ON; this script "
            "expects the default dict path)."
        )


# ── R1: LAST-WINS vs MAX per-cluster pair_scores ────────────────────────────
def _member_to_cid(clusters: dict) -> dict[int, int]:
    m2c: dict[int, int] = {}
    for cid, info in clusters.items():
        for member in info["members"]:
            m2c[member] = cid
    return m2c


def _bucket_in_cluster_pairs(
    pairs: list[tuple[int, int, float]],
    m2c: dict[int, int],
) -> dict[int, list[tuple[int, int, float]]]:
    """Restrict pairs to in-cluster pairs (both endpoints same cid), preserving
    input order. Mirrors _bucket_pairs membership rule (cluster_pairscores.py).
    Returns cid -> ordered list of (a, b, score)."""
    by_cid: dict[int, list[tuple[int, int, float]]] = {}
    for a, b, s in pairs:
        ca = m2c.get(a)
        if ca is not None and ca == m2c.get(b):
            by_cid.setdefault(ca, []).append((a, b, s))
    return by_cid


def _cluster_stats(pair_scores: dict[tuple[int, int], float], size: int) -> dict:
    """min_edge / avg_edge / connectivity / confidence / quality / bottleneck,
    computed exactly as compute_cluster_confidence + the weak-quality test
    (cluster.py). bottleneck = first (a,b) at the min score in iteration order."""
    if size <= 1 or not pair_scores:
        return {
            "min_edge": 0.0,
            "avg_edge": 0.0,
            "connectivity": 1.0 if size <= 1 else 0.0,
            "confidence": 1.0 if size <= 1 else 0.0,
            "quality": "strong",
            "bottleneck": None,
        }
    scores = list(pair_scores.values())
    min_edge = min(scores)
    avg_edge = sum(scores) / len(scores)
    max_possible = size * (size - 1) / 2
    connectivity = len(pair_scores) / max_possible if max_possible > 0 else 0.0
    confidence = 0.4 * min_edge + 0.3 * avg_edge + 0.3 * connectivity
    quality = "weak" if (avg_edge - min_edge) > 0.3 else "strong"
    # First (a,b) at the minimum score, in pair_scores iteration order (ties
    # resolve to the first-seen key -- matches min(items(), key=itemgetter(1))).
    bottleneck = None
    best = None
    for key, val in pair_scores.items():
        if best is None or val < best:
            best = val
            bottleneck = key
    return {
        "min_edge": min_edge,
        "avg_edge": avg_edge,
        "connectivity": connectivity,
        "confidence": confidence,
        "quality": quality,
        "bottleneck": bottleneck,
    }


def _r1(
    by_cid: dict[int, list[tuple[int, int, float]]],
    clusters: dict,
) -> dict:
    total = len(clusters)
    diffscore_dup = 0
    conf_changed = 0
    quality_flipped = 0
    bottleneck_changed = 0

    for cid, ordered in by_cid.items():
        size = clusters[cid]["size"]

        ps_last: dict[tuple[int, int], float] = {}
        ps_max: dict[tuple[int, int], float] = {}
        # Track whether any canonical pair recurs with >=2 distinct scores.
        seen_scores: dict[tuple[int, int], set] = {}
        for a, b, s in ordered:
            key = (a, b)
            ps_last[key] = s  # last-wins overwrite
            prev = ps_max.get(key)
            ps_max[key] = s if prev is None else max(prev, s)
            seen_scores.setdefault(key, set()).add(s)

        if any(len(v) >= 2 for v in seen_scores.values()):
            diffscore_dup += 1

        st_last = _cluster_stats(ps_last, size)
        st_max = _cluster_stats(ps_max, size)

        if abs(st_last["confidence"] - st_max["confidence"]) > 1e-12:
            conf_changed += 1
        if st_last["quality"] != st_max["quality"]:
            quality_flipped += 1
        if st_last["bottleneck"] != st_max["bottleneck"]:
            bottleneck_changed += 1

    return {
        "total_clusters": total,
        "clusters_with_diffscore_dup": diffscore_dup,
        "clusters_confidence_changed": conf_changed,
        "clusters_quality_flipped": quality_flipped,
        "clusters_bottleneck_changed": bottleneck_changed,
    }


# ── R2: order-free avg_edge epsilon-band near the weak boundary ──────────────
def _r2(by_cid: dict[int, list[tuple[int, int, float]]], clusters: dict) -> dict:
    band = 0
    deltas: list[tuple[int, float]] = []  # (cid, avg_edge - min_edge)
    for cid, ordered in by_cid.items():
        size = clusters[cid]["size"]
        ps_last: dict[tuple[int, int], float] = {}
        for a, b, s in ordered:
            ps_last[(a, b)] = s  # last-wins
        st = _cluster_stats(ps_last, size)
        if size <= 1 or not ps_last:
            continue
        delta = st["avg_edge"] - st["min_edge"]
        deltas.append((cid, delta))
        if abs(delta - 0.30) <= 1e-6:
            band += 1
    # 10 clusters whose (avg_edge - min_edge) is closest to the 0.30 boundary.
    closest = sorted(deltas, key=lambda t: abs(t[1] - 0.30))[:10]
    return {
        "band_count": band,
        "closest": closest,
    }


# ── R3: bottleneck / MST tie-break ──────────────────────────────────────────
def _r3(
    by_cid: dict[int, list[tuple[int, int, float]]],
    clusters: dict,
    max_cluster_size: int,
) -> dict:
    min_edge_tie = 0
    oversized_equal_weight = 0
    for cid, ordered in by_cid.items():
        # Use last-wins per-cluster scores (canonical edge set).
        ps_last: dict[tuple[int, int], float] = {}
        for a, b, s in ordered:
            ps_last[(a, b)] = s
        scores = list(ps_last.values())
        if not scores:
            continue
        min_score = min(scores)
        if sum(1 for v in scores if v == min_score) >= 2:
            min_edge_tie += 1

        size = clusters[cid]["size"]
        if size > max_cluster_size:
            # >=2 edges of equal weight ANYWHERE in the cluster (upper bound on
            # MST-split-membership-on-tie ambiguity).
            counts: dict[float, int] = {}
            has_dup_weight = False
            for v in scores:
                counts[v] = counts.get(v, 0) + 1
                if counts[v] >= 2:
                    has_dup_weight = True
                    break
            if has_dup_weight:
                oversized_equal_weight += 1
    return {
        "min_edge_tie": min_edge_tie,
        "oversized_equal_weight": oversized_equal_weight,
    }


def _pct(num: int, denom: int) -> str:
    return f"{(100.0 * num / denom):.3f}%" if denom else "n/a"


def _summary_block(
    n: int,
    r1: dict,
    r2: dict,
    r3: dict,
    max_cluster_size: int,
) -> str:
    total = r1["total_clusters"]
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"BLAST-RADIUS SUMMARY  (N={n:,}  total_clusters={total:,})")
    lines.append("=" * 70)

    lines.append("")
    lines.append("R1  LAST-WINS -> MAX dedup of per-cluster pair_scores")
    lines.append(
        f"  clusters_with_diffscore_dup   : {r1['clusters_with_diffscore_dup']:>10,}"
        f"  ({_pct(r1['clusters_with_diffscore_dup'], total)})"
    )
    lines.append(
        f"  clusters_confidence_changed   : {r1['clusters_confidence_changed']:>10,}"
        f"  ({_pct(r1['clusters_confidence_changed'], total)})"
    )
    lines.append(
        f"  clusters_quality_flipped      : {r1['clusters_quality_flipped']:>10,}"
        f"  ({_pct(r1['clusters_quality_flipped'], total)})"
    )
    lines.append(
        f"  clusters_bottleneck_changed   : {r1['clusters_bottleneck_changed']:>10,}"
        f"  ({_pct(r1['clusters_bottleneck_changed'], total)})"
    )

    lines.append("")
    lines.append("R2  order-free avg_edge epsilon-band near weak boundary (|d-0.30|<=1e-6)")
    lines.append(
        f"  clusters_in_band              : {r2['band_count']:>10,}"
        f"  ({_pct(r2['band_count'], total)})"
    )
    lines.append("  10 closest (avg_edge - min_edge) values to 0.30:")
    if r2["closest"]:
        for cid, delta in r2["closest"]:
            lines.append(f"    cid={cid:<10} avg-min={delta:.9f}  (dist={abs(delta - 0.30):.2e})")
    else:
        lines.append("    (no multi-member clusters with edges)")

    lines.append("")
    lines.append("R3  bottleneck / MST tie-break")
    lines.append(
        f"  clusters_min_edge_tie         : {r3['min_edge_tie']:>10,}"
        f"  ({_pct(r3['min_edge_tie'], total)})"
    )
    lines.append(
        f"  oversized(>{max_cluster_size})_equal_weight : "
        f"{r3['oversized_equal_weight']:>10,}"
        f"  ({_pct(r3['oversized_equal_weight'], total)})"
    )
    lines.append("=" * 70)
    return "\n".join(lines)


def _resolve_max_cluster_size() -> int:
    """Default max_cluster_size (golden_rules default). 100 per build_clusters
    signature default; the realistic auto-config does not override it for this
    fixture shape."""
    return 100


def run_for_n(n: int) -> str:
    _run_dedupe_and_capture(n)
    pairs = _CAPTURED_PAIRS
    clusters = _CAPTURED_CLUSTERS
    assert pairs is not None and clusters is not None

    m2c = _member_to_cid(clusters)
    by_cid = _bucket_in_cluster_pairs(pairs, m2c)
    max_cluster_size = _resolve_max_cluster_size()

    r1 = _r1(by_cid, clusters)
    r2 = _r2(by_cid, clusters)
    r3 = _r3(by_cid, clusters, max_cluster_size)

    return _summary_block(n, r1, r2, r3, max_cluster_size)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--n",
        default="1000000,5000000",
        help="Comma-separated row counts (default: 1000000,5000000).",
    )
    args = parser.parse_args()

    ns = [int(x.strip()) for x in args.n.split(",") if x.strip()]
    blocks: list[str] = []
    for n in ns:
        block = run_for_n(n)
        print(block, flush=True)
        blocks.append(block)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
