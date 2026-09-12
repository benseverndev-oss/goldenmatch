"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

Why does zero-config's learned blocking give dblp_scholar zero candidates? Rebuilds the zero-config config,
runs build_blocks with goldenmatch logging at INFO, and prints the learned rules' outcome.

Usage, from the repo root:  python -m scripts.spike.learned_check [dataset]
"""

from __future__ import annotations

import logging
import os
import sys

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

logging.basicConfig(level=logging.WARNING, format="LOG %(name)s %(levelname)s %(message)s")
for name in (
    "goldenmatch.core.learned_blocking",
    "goldenmatch.core.blocker",
    "goldenmatch.core.autoconfig",
):
    logging.getLogger(name).setLevel(logging.INFO)


def main() -> int:
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.domain import detect_domain, extract_features

    from scripts.autoconfig_quality.rule_sweep import resolve_loader

    dataset = sys.argv[1] if len(sys.argv) > 1 else "dblp_scholar"
    df, gt = resolve_loader(dataset)()
    print(f"CHECK {dataset} rows={df.height} gt={len(gt)} columns={df.columns}", flush=True)
    cfg = auto_configure_df(df, confidence_required=False)
    print("CHECK blocking", cfg.blocking.model_dump(mode="json", exclude_defaults=True), flush=True)
    frame = df.with_row_index("__row_id__")
    frame, _ = extract_features(frame, detect_domain(list(frame.columns)))
    blocks = build_blocks(frame, cfg.blocking)
    sizes = []
    for b in blocks:
        native = b.materialize().native if hasattr(b, "materialize") else b.df
        if hasattr(native, "collect"):
            native = native.collect()
        sizes.append(native.height if hasattr(native, "height") else native.num_rows)
    pairs = sum(s * (s - 1) // 2 for s in sizes)
    print(
        f"CHECK blocks={len(sizes)} max_block={max(sizes, default=0)} candidate_pairs={pairs}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
