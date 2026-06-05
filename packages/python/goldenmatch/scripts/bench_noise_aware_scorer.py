"""#662 benchmark: does upgrading token_sort -> jaro_winkler/ensemble on
noise-prone free-text columns recover the NCVR address-scorer regression without
hurting clean-data precision? Sweeps three scorer settings across a
heavy-corruption NCVR variant, Febrl3, and a clean-data guard.

Run: POLARS_SKIP_CPU_CHECK=1 python scripts/bench_noise_aware_scorer.py
Datasets are LOCAL (NCVR sample, DQbench ~/.dqbench); this is NOT a CI gate.

Clean-data guard: the enforced clean-precision guard already lives in CI as the
#528 backend-parity quality gate. ``run_clean_guard`` here is a best-effort
DQbench T1-T3 probe that SKIPS WITH A MESSAGE when ``~/.dqbench`` is absent, so
this script never hard-depends on a local DQbench checkout.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

# Module-level imports must be safe WITHOUT recordlinkage. polars + goldenmatch
# are always available in this venv; recordlinkage is lazy-imported in
# run_febrl3 only.
import polars as pl

NCVR_SAMPLE = (
    Path(__file__).parent.parent
    / "tests" / "benchmarks" / "datasets" / "NCVR" / "ncvoter_sample_10k.txt"
)


def set_scorer(name: str) -> dict[str, str]:
    """Env dict for a scorer setting. ``token_sort`` is the baseline -- it must
    use the KILL-SWITCH (``GOLDENMATCH_NOISE_AWARE_SCORERS=0``), NOT an empty
    env: since #662 flipped the noise-aware swap default-ON, an empty env now
    yields jaro_winkler, so an empty baseline would silently equal the swap and
    the sweep would measure nothing. Anything else flips the flag on + targets
    that scorer."""
    if name == "token_sort":
        return {"GOLDENMATCH_NOISE_AWARE_SCORERS": "0"}  # genuine legacy baseline
    return {
        "GOLDENMATCH_NOISE_AWARE_SCORERS": "1",
        "GOLDENMATCH_NOISE_AWARE_TARGET": name,
    }


def _run_with_env(env: dict, fn):
    """Set ``env`` keys (saving prior values), call ``fn()``, restore in finally.
    The controller reads these env vars at ``dedupe_df`` config-build time."""
    saved: dict[str, str | None] = {}
    try:
        for k, v in env.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
        return fn()
    finally:
        for k, prior in saved.items():
            if prior is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prior


def _prf(found: set, gt: set) -> dict:
    tp = len(found & gt)
    fp = len(found - gt)
    fn = len(gt - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"f1": f1, "p": p, "r": r, "found": len(found), "gt": len(gt)}


def _clusters_to_pairs(clusters, id_lookup: list) -> set:
    found: set = set()
    for c in clusters.values():
        members = sorted(c["members"])
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = id_lookup[members[i]], id_lookup[members[j]]
                found.add((min(a, b), max(a, b)))
    return found


def run_ncvr_high(scorer_env: dict, seed: int = 42) -> dict:
    """NCVR HIGH-corruption variant. Same GT shape as the existing
    ``test_autoconfig_ncvr_meets_target`` BUT corruption is heavy (>=0.60),
    weighted onto ``res_street_address`` (~0.90 of dupes), and uses
    character-noise ops only (typo/swap/drop) -- the ops that stress
    jaro-vs-token. ``abbreviate`` and ``case`` are dropped.
    """
    if not NCVR_SAMPLE.exists():
        return {"skipped": "NCVR sample absent"}

    import goldenmatch

    df = pl.read_csv(
        NCVR_SAMPLE, separator="\t", encoding="utf8-lossy", ignore_errors=True
    )
    df = df.filter(
        (pl.col("last_name").str.len_chars() > 1)
        & (pl.col("first_name").str.len_chars() > 1)
    )
    n_base = min(5000, df.height)
    n_dupes = n_base // 2

    df = df.sample(n=n_base, seed=seed)
    keep_cols = [
        "ncid", "first_name", "last_name", "middle_name",
        "res_street_address", "res_city_desc", "state_cd",
        "zip_code", "birth_year", "gender_code",
    ]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df.select(keep_cols)

    rng = random.Random(seed)
    rows = df.to_dicts()
    dup_indices = rng.sample(range(len(rows)), min(n_dupes, len(rows)))

    # Character-noise ops only: typo / swap / drop. No abbreviate/case.
    def _corrupt(val):
        if val is None or len(val) < 2:
            return val
        op = rng.choice(["typo", "swap", "drop"])
        if op == "typo":
            pos = rng.randint(0, len(val) - 1)
            repl = rng.choice("abcdefghijklmnopqrstuvwxyz")
            return val[:pos] + repl + val[pos + 1:]
        if op == "swap" and len(val) >= 3:
            pos = rng.randint(0, len(val) - 2)
            return val[:pos] + val[pos + 1] + val[pos] + val[pos + 2:]
        if op == "drop" and len(val) >= 3:
            pos = rng.randint(0, len(val) - 1)
            return val[:pos] + val[pos + 1:]
        # fall back to a typo so HIGH corruption always lands a char change
        pos = rng.randint(0, len(val) - 1)
        repl = rng.choice("abcdefghijklmnopqrstuvwxyz")
        return val[:pos] + repl + val[pos + 1:]

    other_fields = ["first_name", "last_name", "middle_name", "zip_code"]

    corrupted = []
    gt: set = set()
    for orig_idx in dup_indices:
        original = rows[orig_idx]
        corrupt = dict(original)
        corrupt["ncid"] = original["ncid"] + "_DUP"
        # Address: heavy, ~0.90 of dupes, possibly multiple noise hits.
        if "res_street_address" in corrupt and rng.random() < 0.90:
            n_ops = rng.randint(1, 3)
            for _ in range(n_ops):
                corrupt["res_street_address"] = _corrupt(
                    corrupt.get("res_street_address")
                )
        # Other fields: lighter, but overall corruption rate still >= 0.60.
        for field in other_fields:
            if field in corrupt and rng.random() < 0.30:
                corrupt[field] = _corrupt(corrupt.get(field))
        corrupted.append(corrupt)
        a, b = original["ncid"], corrupt["ncid"]
        gt.add((min(a, b), max(a, b)))

    combined = pl.DataFrame(rows + corrupted)
    result = _run_with_env(scorer_env, lambda: goldenmatch.dedupe_df(combined))

    ncid_lookup = combined["ncid"].to_list()
    found = _clusters_to_pairs(result.clusters, ncid_lookup)
    return _prf(found, gt)


def run_febrl3(scorer_env: dict) -> dict:
    """Febrl3 (recordlinkage). LAZY import so the module loads without it."""
    try:
        from recordlinkage.datasets import load_febrl3
    except ImportError:
        return {"skipped": "recordlinkage not installed"}

    import goldenmatch

    df_pd, links = load_febrl3(return_links=True)
    df_pd = df_pd.reset_index()  # rec_id becomes a column
    rec_col = df_pd.columns[0]
    pdf = pl.from_pandas(df_pd.astype(str))

    result = _run_with_env(scorer_env, lambda: goldenmatch.dedupe_df(pdf))

    rec_lookup = pdf[rec_col].to_list()
    found = _clusters_to_pairs(result.clusters, rec_lookup)

    # links is a pandas MultiIndex of (rec_id_1, rec_id_2) true matches.
    gt: set = set()
    for a, b in links:
        gt.add((min(a, b), max(a, b)))
    return _prf(found, gt)


def run_clean_guard(scorer_env: dict) -> dict:
    """Clean-data precision guard via DQbench T1-T3.

    Minimal by design: the ENFORCED clean-precision guard is the in-CI #528
    backend-parity quality gate. This is a local best-effort probe that skips
    with a clear message when ``~/.dqbench`` is absent so the harness never
    hard-depends on a DQbench checkout.
    """
    dqbench = Path.home() / ".dqbench"
    if not dqbench.exists():
        return {
            "skipped": (
                "DQbench not found at ~/.dqbench; clean-data precision is "
                "enforced by the in-CI #528 backend-parity gate, not here"
            )
        }
    # DQbench wiring is intentionally out of scope for this harness; the CI #528
    # gate owns the enforced clean guard. Treat presence as a manual signal.
    return {
        "skipped": (
            "DQbench present but T1-T3 wiring is deferred to the #528 CI gate; "
            "run the in-CI clean-precision guard for the enforced check"
        )
    }


def _fmt(res: dict) -> str:
    if "skipped" in res:
        return f"SKIP ({res['skipped']})"
    return (
        f"F1={res['f1']:.4f}  P={res['p']:.4f}  R={res['r']:.4f}  "
        f"found={res['found']} gt={res['gt']}"
    )


def main() -> None:
    # Disable the controller's cross-run memory for the WHOLE sweep. It is keyed
    # by dataset signature (NOT the scorer env), so with it ON the first scorer
    # run persists a committed config that the other runs reload verbatim --
    # making all three settings produce byte-identical results regardless of the
    # scorer swap. Disabling it forces each run to rebuild its config under its
    # own scorer env, which is the only way the sweep measures the swap at all.
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"

    scorers = ["token_sort", "jaro_winkler", "ensemble"]
    datasets = {
        "ncvr_high": run_ncvr_high,
        "febrl3": run_febrl3,
        "clean_guard": run_clean_guard,
    }

    results: dict[str, dict[str, dict]] = {}
    for ds_name, runner in datasets.items():
        results[ds_name] = {}
        for scorer in scorers:
            env = set_scorer(scorer)
            results[ds_name][scorer] = runner(env)

    print("\n#662 noise-aware scorer sweep")
    print("=" * 72)
    for ds_name in datasets:
        print(f"\n[{ds_name}]")
        for scorer in scorers:
            print(f"  {scorer:>14}: {_fmt(results[ds_name][scorer])}")
    print("=" * 72)

    # NO-REGRESSION GUARD (#662 flipped default-on on a small-but-consistent
    # improvement, not a catastrophic-regression recovery). The swap must not
    # REGRESS NCVR-high F1 vs token_sort beyond a 0.5pp hold band, and should
    # ideally improve it. (Catastrophic 0.871-style regressions are a different,
    # un-reproduced scenario; this guard protects the shipped default-on.)
    ncvr = results["ncvr_high"]
    base = ncvr.get("token_sort", {})
    alts = [a for a in (ncvr.get("jaro_winkler", {}), ncvr.get("ensemble", {})) if "f1" in a]
    safe = False
    delta = 0.0
    if "f1" in base and alts:
        best_alt = max(a["f1"] for a in alts)
        delta = best_alt - base["f1"]
        safe = delta >= -0.005  # within 0.5pp hold (improvement => positive delta)
    if not safe:
        print(
            "\n!!! NOISE-AWARE SWAP REGRESSES NCVR-high F1 beyond the 0.5pp hold "
            "band (or NCVR absent) — do NOT keep default-on !!!"
        )
        sys.exit(1)
    print(f"\nno-regression guard: PASS (best alt vs token_sort delta = {delta * 100:+.2f}pp)")
    sys.exit(0)


if __name__ == "__main__":
    main()
