"""Perceptual crawl-tier benchmark harness — accuracy, performance, robustness.

Dispatch-only (no per-PR gate): run it locally or via the `bench-perceptual`
workflow. Emits a structured JSON report + a markdown summary, both diffable
across runs so we can iterate on the metrics that matter — the scorer operating
point, the blocker recall-vs-reduction tradeoff, per-transform robustness, and
the native speedup.

    cd packages/python/goldenmatch
    uv run python scripts/bench_perceptual/run.py --suite all --out report.json
"""
# ruff: noqa: E402, I001 - run.py bootstraps sys.path before importing the sibling
# bench modules, so imports are intentionally not at the top / not isort-ordered.
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datasets
import hotspot
import metrics
import perf
import pipeline_bench

from goldenmatch.core import perceptual
from goldenmatch.core.perceptual_blocker import PerceptualLSHBlocker

_HASH_BITS = 64
# 1.0 down to ~0.55 in single-bit steps — the operating-point search space.
_THRESHOLDS = [i / _HASH_BITS for i in range(_HASH_BITS, 35, -1)]
_BAND_COUNTS = [2, 4, 8, 16, 32]
# Radial similarity is angular-aligned Pearson in [0,1]; sweep the high band.
_RADIAL_THRESHOLDS = [i / 100 for i in range(99, 49, -1)]


def _img_sim(ha: int, hb: int) -> float:
    return 1.0 - perceptual.hamming(ha, hb) / _HASH_BITS


def accuracy_image(n_bases: int) -> dict:
    suite = datasets.build_image_suite(n_bases)
    hashes = [perceptual.phash_image(it.payload) for it in suite.items]  # item_id == index
    n = len(hashes)

    labeled: list[tuple[bool, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            labeled.append(((i, j) in suite.gt_pairs, _img_sim(hashes[i], hashes[j])))

    points, best = metrics.threshold_sweep(labeled, _THRESHOLDS)
    disc = metrics.discrimination(labeled)
    per_transform = {
        t: metrics.prf_at_threshold(
            [(True, _img_sim(hashes[a], hashes[b])) for (a, b) in pairs], best.threshold
        ).recall
        for t, pairs in suite.transform_pairs.items()
    }
    band_sweep = []
    for nb in _BAND_COUNTS:
        cand = PerceptualLSHBlocker(nb, _HASH_BITS).candidate_pairs(hashes)
        band_sweep.append({"num_bands": nb, **metrics.blocking_eval(cand, suite.gt_pairs, n).as_dict()})

    return {
        "items": n,
        "base_entities": n_bases,
        "best_operating_point": best.as_dict(),
        "threshold_sweep": [p.as_dict() for p in points],
        "discrimination": disc.as_dict(),
        "per_transform_recall_at_best": per_transform,
        "blocking_band_sweep": band_sweep,
    }


def accuracy_audio(n_bases: int) -> dict:
    suite = datasets.build_audio_suite(n_bases)
    fps = [perceptual.fingerprint_audio(*it.payload) for it in suite.items]
    n = len(fps)

    def sim(a: int, b: int) -> float:
        return 1.0 - perceptual.audio_ber_aligned(fps[a], fps[b])

    labeled = [
        ((i, j) in suite.gt_pairs, sim(i, j)) for i in range(n) for j in range(i + 1, n)
    ]
    points, best = metrics.threshold_sweep(labeled, _THRESHOLDS)
    disc = metrics.discrimination(labeled)
    per_transform = {
        t: metrics.prf_at_threshold(
            [(True, sim(a, b)) for (a, b) in pairs], best.threshold
        ).recall
        for t, pairs in suite.transform_pairs.items()
    }
    return {
        "items": n,
        "base_entities": n_bases,
        "best_operating_point": best.as_dict(),
        "threshold_sweep": [p.as_dict() for p in points],
        "discrimination": disc.as_dict(),
        "per_transform_recall_at_best": per_transform,
        "note": "audio has no LSH blocker (variable-length fingerprint); scoring uses offset-aligned BER",
    }


def accuracy_radial(n_bases: int) -> dict:
    """Rotation/crop-aware radial-variance feature -- the geometric counterpart to
    image pHash, on the SAME image-variant suite. Scored by angular-aligned
    similarity, so rotate/crop (which pHash scores ~0) are recalled here."""
    suite = datasets.build_image_suite(n_bases)
    profiles = [perceptual.radial_variance(it.payload) for it in suite.items]
    n = len(profiles)

    def sim(a: int, b: int) -> float:
        return perceptual.radial_align_similarity(profiles[a], profiles[b])

    labeled = [
        ((i, j) in suite.gt_pairs, sim(i, j)) for i in range(n) for j in range(i + 1, n)
    ]
    points, best = metrics.threshold_sweep(labeled, _RADIAL_THRESHOLDS)
    disc = metrics.discrimination(labeled)
    per_transform = {
        t: metrics.prf_at_threshold(
            [(True, sim(a, b)) for (a, b) in pairs], best.threshold
        ).recall
        for t, pairs in suite.transform_pairs.items()
    }
    return {
        "items": n,
        "base_entities": n_bases,
        "best_operating_point": best.as_dict(),
        "threshold_sweep": [p.as_dict() for p in points],
        "discrimination": disc.as_dict(),
        "per_transform_recall_at_best": per_transform,
        "note": "angular-aligned similarity; no LSH blocking (rotation breaks "
        "banded-LSH). Compare rotate/crop recall here vs image pHash's ~0.",
    }


def perf_suite(n_image: int, n_audio: int) -> dict:
    img = [it.payload for it in datasets.build_image_suite(n_image).items]
    aud = [it.payload for it in datasets.build_audio_suite(n_audio).items]
    return {
        "image_hash": perf.bench_image_hash(img),
        "audio_hash": perf.bench_audio_hash(aud),
        "radial_hash": perf.bench_radial_hash(img),
    }


def hotspot_suite(n_image: int, n_audio: int) -> dict:
    """Per-kernel self-time hotspots (Python path; native is opaque to cProfile)."""
    imgs = [it.payload for it in datasets.build_image_suite(n_image).items]
    auds = [it.payload for it in datasets.build_audio_suite(n_audio).items]
    return {
        "image_hash": hotspot.profile_top(lambda: perceptual.phash_image_batch(imgs)),
        "audio_hash": hotspot.profile_top(
            lambda: [perceptual.fingerprint_audio(s, sr) for s, sr in auds]
        ),
        "radial_hash": hotspot.profile_top(
            lambda: [perceptual.radial_variance(g) for g in imgs]
        ),
    }


def e2e_suite(n_bases: int) -> dict:
    """End-to-end dedupe F1 + wall over a synthetic image-pHash column."""
    return {"image_dedupe": pipeline_bench.e2e_image_dedupe(n_bases)}


def robustness_suite(n_image: int, n_audio: int) -> dict:
    """Determinism (recompute is identical) + native==python parity over the suite."""
    from goldenmatch.core._native_loader import native_available

    imgs = [it.payload for it in datasets.build_image_suite(n_image).items]
    auds = [it.payload for it in datasets.build_audio_suite(n_audio).items]

    os.environ["GOLDENMATCH_NATIVE"] = "0"
    py_img = [perceptual.phash_image(g) for g in imgs]
    py_aud = [perceptual.fingerprint_audio(*a) for a in auds]
    determinism = (
        py_img == [perceptual.phash_image(g) for g in imgs]
        and py_aud == [perceptual.fingerprint_audio(*a) for a in auds]
    )

    native_parity = None
    if native_available():
        os.environ["GOLDENMATCH_NATIVE"] = "1"
        native_parity = (
            py_img == [perceptual.phash_image(g) for g in imgs]
            and py_aud == [perceptual.fingerprint_audio(*a) for a in auds]
        )
    os.environ["GOLDENMATCH_NATIVE"] = "0"
    return {
        "determinism_holds": determinism,
        "native_available": native_available(),
        "native_equals_python": native_parity,
    }


def _markdown(report: dict) -> str:
    lines = ["# Perceptual crawl-tier bench\n"]
    ai = report.get("accuracy_image")
    if ai:
        bop = ai["best_operating_point"]
        d = ai["discrimination"]
        lines += [
            "## Image accuracy",
            f"- items: {ai['items']} ({ai['base_entities']} entities)",
            f"- **best operating point**: threshold={bop['threshold']:.4f} "
            f"F1={bop['f1']:.4f} P={bop['precision']:.4f} R={bop['recall']:.4f}",
            f"- discrimination: match_mean={d['match_mean']:.4f} "
            f"nonmatch_mean={d['nonmatch_mean']:.4f} separation={d['separation']:.4f} "
            f"overlap={d['overlap_count']}",
            "- per-transform recall @ best: "
            + ", ".join(f"{k}={v:.3f}" for k, v in ai["per_transform_recall_at_best"].items()),
            "",
            "| num_bands | recall | reduction | candidates |",
            "|---|---|---|---|",
        ]
        for b in ai["blocking_band_sweep"]:
            lines.append(
                f"| {b['num_bands']} | {b['recall']:.4f} | {b['reduction_ratio']:.4f} | {b['candidate_pairs']} |"
            )
        lines.append("")
    ar = report.get("accuracy_radial")
    if ar:
        bop = ar["best_operating_point"]
        d = ar["discrimination"]
        lines += [
            "## Image accuracy — radial (rotation/crop-aware)",
            f"- items: {ar['items']} ({ar['base_entities']} entities)",
            f"- **best operating point**: threshold={bop['threshold']:.4f} "
            f"F1={bop['f1']:.4f} P={bop['precision']:.4f} R={bop['recall']:.4f}",
            f"- discrimination: match_mean={d['match_mean']:.4f} "
            f"nonmatch_mean={d['nonmatch_mean']:.4f} separation={d['separation']:.4f} "
            f"overlap={d['overlap_count']}",
            "- per-transform recall @ best: "
            + ", ".join(f"{k}={v:.3f}" for k, v in ar["per_transform_recall_at_best"].items()),
            "",
        ]
    aa = report.get("accuracy_audio")
    if aa:
        bop = aa["best_operating_point"]
        lines += [
            "## Audio accuracy",
            f"- items: {aa['items']} ({aa['base_entities']} entities)",
            f"- **best operating point**: threshold={bop['threshold']:.4f} F1={bop['f1']:.4f}",
            "- per-transform recall @ best: "
            + ", ".join(f"{k}={v:.3f}" for k, v in aa["per_transform_recall_at_best"].items()),
            "",
        ]
    pf = report.get("perf")
    if pf:
        lines.append("## Performance")
        for name, r in pf.items():
            py = r.get("python")
            nat = r.get("native")
            sp = r.get("speedup")
            seg = f"python={py['units_per_sec']:.0f}/s" if py else "python=?"
            seg += f" native={nat['units_per_sec']:.0f}/s" if nat else " native=unavailable"
            seg += f" speedup={sp:.1f}x" if sp else ""
            lines.append(f"- {name}: {seg}")
        lines.append("")
    rb = report.get("robustness")
    if rb:
        lines += [
            "## Robustness",
            f"- determinism holds: {rb['determinism_holds']}",
            f"- native available: {rb['native_available']}; native==python: {rb['native_equals_python']}",
            "",
        ]
    e2e = report.get("e2e")
    if e2e:
        r = e2e["image_dedupe"]
        lines += [
            "## End-to-end pipeline (image dedupe)",
            f"- {r['records']} records ({r['base_entities']} entities), threshold={r['threshold']}",
            f"- **F1={r['f1']:.4f}** P={r['precision']:.4f} R={r['recall']:.4f} "
            f"(tp={r['tp']} fp={r['fp']} fn={r['fn']})",
            f"- **wall={r['wall_sec']:.4f}s** ({r['throughput_rec_per_sec']} rec/s)",
        ]
        st = r.get("stage_timings_seconds") or {}
        if st:
            lines.append(
                "- stage wall: "
                + ", ".join(f"{k}={v:.4f}s" for k, v in sorted(st.items(), key=lambda x: -x[1]))
            )
        lines += [f"- _{r['note']}_", ""]
    hs = report.get("hotspots")
    if hs:
        lines.append("## Hotspots (Python path; self-time)")
        for kernel, rows in hs.items():
            lines += [f"### {kernel}", "| tottime (s) | cumtime (s) | ncalls | function |", "|---|---|---|---|"]
            for row in rows:
                lines.append(
                    f"| {row['tottime']:.4f} | {row['cumtime']:.4f} | {row['ncalls']} | {row['func']} |"
                )
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--suite",
        choices=["accuracy", "perf", "robustness", "e2e", "hotspot", "all"],
        default="all",
    )
    ap.add_argument("--n-image-bases", type=int, default=30)
    ap.add_argument("--n-audio-bases", type=int, default=12)
    # radial scoring is an O(angles^2)-per-pair alignment search, so it uses a
    # smaller default suite than the bit-hamming image/audio paths.
    ap.add_argument("--n-radial-bases", type=int, default=12)
    ap.add_argument("--e2e-bases", type=int, default=30)
    ap.add_argument("--out", default=None, help="write JSON report here")
    args = ap.parse_args()

    report: dict = {"config": vars(args)}
    if args.suite in ("accuracy", "all"):
        report["accuracy_image"] = accuracy_image(args.n_image_bases)
        report["accuracy_radial"] = accuracy_radial(args.n_radial_bases)
        report["accuracy_audio"] = accuracy_audio(args.n_audio_bases)
    if args.suite in ("perf", "all"):
        report["perf"] = perf_suite(args.n_image_bases, args.n_audio_bases)
    if args.suite in ("robustness", "all"):
        report["robustness"] = robustness_suite(args.n_image_bases, args.n_audio_bases)
    if args.suite in ("e2e", "all"):
        report["e2e"] = e2e_suite(args.e2e_bases)
    if args.suite in ("hotspot", "all"):
        report["hotspots"] = hotspot_suite(args.n_image_bases, args.n_audio_bases)

    md = _markdown(report)
    print(md)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
