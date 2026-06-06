"""Emit cross-language parity fixtures for PPRL.

Part A -- CLK byte-parity: hex bloom filters for a battery of (value,
transform) cases covering the plain, parametric, parametric+HMAC, and
security-preset forms. The TS `applyTransform` must byte-match every CLK
(pure-TS SHA-256/HMAC vs Python hashlib/hmac).

Part B -- linkage end-to-end: run_pprl over two small parties in both
protocol modes (trusted_third_party with real scores; smc revealing only
match bits, score == threshold). The emitter ASSERTS every pairwise score
sits >= 1e-3 from the threshold so Python's float32 matmul vs TS float64
cannot flip a match decision.

Output: packages/typescript/goldenmatch/tests/parity/fixtures/pprl.json
Run:    .venv/Scripts/python.exe packages/python/goldenmatch/scripts/emit_pprl_fixture.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
from goldenmatch.core.scorer import _hex_to_bits
from goldenmatch.pprl.protocol import PPRLConfig, compute_bloom_filters, run_pprl
from goldenmatch.utils.transforms import apply_transform

CLK_CASES = [
    ("john smith", "bloom_filter"),
    ("john smith", "bloom_filter:2:30:1024"),
    ("john smith", "bloom_filter:2:30:1024:sharedkey"),
    ("john smith", "bloom_filter:3:40:2048:sharedkey"),
    ("john smith", "bloom_filter:standard"),
    ("john smith", "bloom_filter:high"),
    ("john smith", "bloom_filter:paranoid"),
    ("li", "bloom_filter:paranoid"),       # balanced padding path (len < 8)
    ("", "bloom_filter:2:20:512"),          # empty -> '__' ngram
    ("MARY  JOHNSON ", "bloom_filter:2:30:1024"),  # lower+strip inside transform
]

THRESHOLD = 0.85


def part_a() -> list[dict]:
    return [
        {"value": v, "transform": t, "clk": apply_transform(v, t)}
        for v, t in CLK_CASES
    ]


def _dice(bf_a: str, bf_b: str) -> float:
    a = np.unpackbits(_hex_to_bits(bf_a)).astype(np.float64)
    b = np.unpackbits(_hex_to_bits(bf_b)).astype(np.float64)
    denom = a.sum() + b.sum()
    return float(2.0 * (a * b).sum() / denom) if denom > 0 else 0.0


def part_b() -> dict:
    rows_a = [
        {"name": "john smith"},
        {"name": "mary johnson"},
        {"name": "robert brown"},
    ]
    rows_b = [
        {"name": "john smith"},
        {"name": "mary jonson"},
        {"name": "zachary quinn"},
    ]
    df_a = pl.DataFrame(rows_a)
    df_b = pl.DataFrame(rows_b)

    cfg = PPRLConfig(
        fields=["name"],
        threshold=THRESHOLD,
        security_level="high",
        bloom_filter_size=1024,
        hash_functions=30,
        ngram_size=2,
        scorer="dice",
    )

    # Margin assertion: no pair's true dice may sit within 1e-3 of the
    # threshold (f32 vs f64 safety), checked over the SHARED-KEY filters
    # (the SMC case) and the no-key filters (the TTP case).
    for hmac_key in (None, "sharedkey"):
        fa = compute_bloom_filters(df_a, ["name"], cfg, hmac_key=hmac_key)
        fb = compute_bloom_filters(df_b, ["name"], cfg, hmac_key=hmac_key)
        for i, ba in fa.items():
            for j, bb in fb.items():
                s = _dice(ba, bb)
                assert abs(s - THRESHOLD) >= 1e-3, (
                    f"pair ({i},{j}) dice {s:.6f} within 1e-3 of threshold "
                    f"{THRESHOLD} (hmac={hmac_key}) -- pick different names"
                )

    def serialize(result) -> dict:
        clusters = sorted(
            sorted(f"{party}:{rid}" for party, rid in members)
            for members in result.clusters.values()
        )
        return {
            "matches": sorted(
                # run_pprl doesn't return raw pairs; reconstruct from clusters
                # is lossy -- instead recompute? No: LinkageResult lacks pairs.
                []
            ),
            "match_count": result.match_count,
            "total_comparisons": result.total_comparisons,
            "clusters": clusters,
        }

    ttp = run_pprl(df_a, df_b, PPRLConfig(
        fields=["name"], threshold=THRESHOLD, protocol="trusted_third_party",
        bloom_filter_size=1024, hash_functions=30, ngram_size=2, scorer="dice",
    ))
    smc = run_pprl(df_a, df_b, PPRLConfig(
        fields=["name"], threshold=THRESHOLD, protocol="smc",
        bloom_filter_size=1024, hash_functions=30, ngram_size=2, scorer="dice",
    ), hmac_key_a="sharedkey", hmac_key_b="sharedkey")

    # True pair scores for the TTP case (no hmac), for match-score parity.
    fa = compute_bloom_filters(df_a, ["name"], cfg, hmac_key=None)
    fb = compute_bloom_filters(df_b, ["name"], cfg, hmac_key=None)
    ttp_scores = {
        f"{i},{j}": _dice(fa[i], fb[j])
        for i in sorted(fa) for j in sorted(fb)
        if _dice(fa[i], fb[j]) >= THRESHOLD
    }

    return {
        "rows_a": rows_a,
        "rows_b": rows_b,
        "threshold": THRESHOLD,
        "shared_key": "sharedkey",
        "ttp": serialize(ttp) | {"match_scores": ttp_scores},
        "smc": serialize(smc),
    }


def main() -> None:
    fixture = {"clk_cases": part_a(), "linkage": part_b()}
    out = (
        Path(__file__).resolve().parents[3]
        / "typescript" / "goldenmatch" / "tests" / "parity" / "fixtures"
        / "pprl.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"Wrote {out}")
    print("ttp:", json.dumps(fixture["linkage"]["ttp"], indent=2))
    print("smc:", json.dumps(fixture["linkage"]["smc"], indent=2))


if __name__ == "__main__":
    main()
