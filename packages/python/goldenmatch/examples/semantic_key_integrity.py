#!/usr/bin/env python
"""Certify a semantic-model key -- catch a silent metric double-count.

A semantic layer (dbt/MetricFlow, Cube, OSI) is a join graph, and every measure
is defined *relative to* an entity key. If that declared key does not uniquely
identify one real entity, every `SUM`/`COUNT(DISTINCT)` built on it is silently
wrong -- and the semantic layer never tells you, because it ASSUMES the key is
clean.

This walkthrough plants two defects an `orders` model can carry and shows
`certify_key_integrity` quantifying both without ever mutating a number:

  1. STRUCTURAL fan-out -- the declared key `customer_id` is duplicated at grain,
     so a `SUM(revenue)` per customer double-counts. The certificate reports the
     fan-out ratio (how inflated the measure is) before you trust the metric.

  2. RESOLUTION undercount (opt-in `resolve=True`) -- two DISTINCT declared keys
     that are really the same customer, so `COUNT(DISTINCT customer_id)` over-
     counts entities. Entity resolution collapses them and the certificate
     reports the fragmentation rate with a 95% confidence interval + a
     statistically-conservative trust floor.

Then it applies the fix (dedupe the key at grain) and re-certifies to a clean
bill of health.

Usage:
    pip install goldenmatch
    python examples/semantic_key_integrity.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.4f}"


def print_certificate(cert, title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"  key columns:        {cert.key_columns}")
    print(f"  rows / key groups:  {cert.n_rows} / {cert.n_key_groups}")
    print(f"  unique at grain:    {cert.is_unique_at_grain}")
    print(f"  duplicate groups:   {cert.duplicate_key_groups}")
    print(f"  max fan-out:        {cert.max_fan_out:.2f}x")
    if cert.measure_fan_out:
        pretty = ", ".join(f"{m}={r:.2f}x" for m, r in cert.measure_fan_out.items())
        print(f"  measure fan-out:    {pretty}   (SUM inflation per measure)")
    if cert.resolved_entities is not None:
        print(f"  resolved entities:  {cert.resolved_entities}")
        print(f"  fragmented:         {cert.fragmented_entities}")
        print(f"  undercount est.:    {_fmt(cert.undercount_estimate)}")
        print(
            f"  undercount 95% CI:  [{_fmt(cert.undercount_ci_low)}, "
            f"{_fmt(cert.undercount_ci_high)}]   (sampling uncertainty)"
        )
    print(f"  estimate (clean %): {_fmt(cert.estimate)}")
    print(f"  safe_bound:         {_fmt(cert.safe_bound)}")
    print(f"  conservative bound: {_fmt(cert.safe_bound_conservative)}")
    print(f"  trustworthy:        {cert.is_trustworthy()}")


if __name__ == "__main__":
    import pyarrow as pa
    from goldenmatch.semantic import certify_key_integrity

    print("=" * 64)
    print("GoldenMatch -- Semantic-layer key-integrity certificate")
    print("=" * 64)

    # An `orders` semantic model. `customer_id` is the declared entity key that
    # every per-customer measure joins + aggregates on. TWO defects are planted:
    #   * customer_id "c1" appears twice at grain      -> structural fan-out
    #   * "c3" / "c4" are the same person, two keys     -> resolution undercount
    orders = pa.table(
        {
            "customer_id": ["c1", "c1", "c2", "c3", "c4"],
            "name": [
                "Ada Lovelace",
                "Ada Lovelace",
                "Alan Turing",
                "Grace Hopper",
                "Grace M. Hopper",
            ],
            "email": [
                "ada@ex.com",
                "ada@ex.com",
                "alan@ex.com",
                "grace@ex.com",
                "grace@ex.com",
            ],
            "revenue": [100.0, 100.0, 50.0, 70.0, 30.0],
        }
    )
    print(f"\nInput `orders` model: {orders.num_rows} rows")
    print(f"  SUM(revenue)              = {sum(orders.column('revenue').to_pylist()):.0f}")
    print(f"  declared distinct keys    = {len(set(orders.column('customer_id').to_pylist()))}")

    # --- 1. Structural certificate (cheap, no ER) ---
    cert = certify_key_integrity(orders, key="customer_id", measures=["revenue"])
    print_certificate(cert, "1. STRUCTURAL certificate (customer_id, measure=revenue)")
    print(
        "\n  -> customer_id is NOT unique at grain: c1's two rows inflate "
        f"SUM(revenue) by {cert.measure_fan_out['revenue']:.1f}x. A per-customer "
        "revenue metric double-counts before any ER."
    )

    # --- 2. Resolution certificate (opt-in ER: does the key FRAGMENT an entity?) ---
    # ER prints controller/quality progress to stdout; quiet it so the
    # certificate stands alone in the output.
    with contextlib.redirect_stdout(io.StringIO()):
        rcert = certify_key_integrity(
            orders, key="customer_id", measures=["revenue"], resolve=True
        )
    print_certificate(rcert, "2. RESOLUTION certificate (resolve=True)")
    print(
        "\n  -> entity resolution finds distinct declared keys that are really "
        "ONE customer (Grace Hopper under c3 + c4), so COUNT(DISTINCT customer_id) "
        "OVERCOUNTS entities. The undercount estimate carries a 95% Wilson CI "
        "(wide here -- few resolved entities), and `safe_bound_conservative` "
        "discounts the CI-UPPER undercount for a statistically honest trust floor."
    )

    # --- 3. The fix: dedupe the key at grain, then re-certify ---
    # Keep the first row per declared key (a stand-in for the real survivorship
    # step) so customer_id is unique at grain again.
    seen: set[str] = set()
    keep = []
    for i, cid in enumerate(orders.column("customer_id").to_pylist()):
        if cid not in seen:
            seen.add(cid)
            keep.append(i)
    fixed = orders.take(keep)
    fcert = certify_key_integrity(fixed, key="customer_id", measures=["revenue"])
    print_certificate(fcert, "3. AFTER FIX -- deduped key, re-certified")
    print(
        f"\n  -> customer_id is now unique at grain (fan-out {fcert.max_fan_out:.1f}x), "
        f"SUM(revenue) = {sum(fixed.column('revenue').to_pylist()):.0f} is trustworthy. "
        "The structural double-count is gone; the c3/c4 fragmentation stays a "
        "resolution-tier finding for a steward to merge."
    )

    print("\n" + "=" * 64)
    print("The certificate REPORTS and QUANTIFIES trust; it never mutates a metric.")
    print("=" * 64)
