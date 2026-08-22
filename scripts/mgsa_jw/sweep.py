"""Hyperparameter sweep for the MGSA-JW prototype.

``bench.py`` runs one configuration. The defaults in ``mgsa.py`` are partly
CHOSEN HERE rather than published (see that module's docstring: the ``Q``
measure, the misalignment convention), so a verdict drawn from those defaults
alone would be a verdict on guesses. This sweeps the knobs and reports the best
configuration per dataset, so any conclusion is against MGSA-JW's best showing
rather than its first.

Sweeps: sigma, p, token-quality measure, and the misalignment convention. The
baseline row is the strongest incumbent available on each dataset, so the
comparison is against what goldenmatch already ships, not against Jaro-Winkler.

    PYTHONPATH=. uv run python -m scripts.mgsa_jw.sweep
"""

from __future__ import annotations

import argparse
import itertools

from scripts.mgsa_jw.bench import NAME_FIELDS, _row_text, build_pairs, max_f1
from scripts.mgsa_jw.mgsa import mgsa_jw

SIGMAS = (1.0, 2.0, 3.0, 4.0)
PS = (0.5, 1.0, 2.0, 4.0)
QUALITIES = ("jaro_winkler", "convj")
MISALIGN = (True, False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets", default="orgs_hard,febrl3,historical_50k")
    ap.add_argument("--max-pos", type=int, default=250)
    ap.add_argument("--neg-ratio", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args()

    from goldenmatch.core import strsim

    from scripts.suggest_quality.datasets import REGISTRY

    wanted = {d.strip() for d in args.datasets.split(",")}
    for dataset in [d for d in REGISTRY if d.name in wanted]:
        loaded = dataset.loader()
        if loaded is None:
            print(f"{dataset.name}: absent")
            continue
        df, gt = loaded
        texts = _row_text(df, NAME_FIELDS[dataset.name])
        pairs, labels = build_pairs(
            texts, gt, max_pos=args.max_pos, neg_ratio=args.neg_ratio, seed=args.seed
        )

        jw = [float(strsim.jaro_winkler_similarity(a, b)) for a, b in pairs]
        ts = [float(strsim.token_sort_similarity(a, b)) for a, b in pairs]
        best_incumbent = max(
            ("jaro_winkler", max_f1(jw, labels)[0]),
            ("token_sort", max_f1(ts, labels)[0]),
            ("ens_max", max_f1([max(x, y) for x, y in zip(jw, ts)], labels)[0]),
            ("ens_mean", max_f1([(x + y) / 2 for x, y in zip(jw, ts)], labels)[0]),
            key=lambda kv: kv[1],
        )

        print(f"\n=== {dataset.name} ({sum(labels)} pos / {len(labels) - sum(labels)} neg)")
        print(f"best incumbent: {best_incumbent[0]} = {best_incumbent[1]:.4f}")

        rows = []
        for sigma, p, quality, peak_align in itertools.product(SIGMAS, PS, QUALITIES, MISALIGN):
            scores = [
                mgsa_jw(
                    a,
                    b,
                    sigma,
                    p=p,
                    token_quality=quality,
                    misalign_vs_peak=peak_align,
                )
                for a, b in pairs
            ]
            rows.append((max_f1(scores, labels)[0], sigma, p, quality, peak_align))

        rows.sort(reverse=True)
        print(f"{'f1':>8}  {'sigma':>5} {'p':>4} {'Q':>13} {'peak-align':>10}   vs incumbent")
        for f1, sigma, p, quality, peak_align in rows[:8]:
            delta = f1 - best_incumbent[1]
            print(
                f"{f1:>8.4f}  {sigma:>5.1f} {p:>4.1f} {quality:>13} "
                f"{str(peak_align):>10}   {delta:+.4f}"
            )


if __name__ == "__main__":
    main()
