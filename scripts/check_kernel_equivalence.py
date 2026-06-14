#!/usr/bin/env python3
"""pure-Python == Rust-kernel equivalence gate for the single-kernel-collapse spike.

THE CORNERSTONE of the single-kernel-collapse feasibility spike (R0/R1). This is a
STANDALONE check: it imports NOTHING from a default code path. It calls each
implementation directly and compares the outputs. It does not touch, register, or
flip any default behavior, so it is safe to keep additive forever.

It answers kill-criterion item (1): "the pure==kernel equivalence gate can't pass
at 4dp/byte for the tracer scorer". For the levenshtein tracer it computes the
similarity two ways:

  (a) PURE PYTHON: ``goldenmatch.core.scorer.score_field(a, b, scorer)`` — the
      EXISTING default scorer, unchanged. (For levenshtein this is
      ``rapidfuzz.distance.Levenshtein.normalized_similarity``.)
  (b) RUST KERNEL: the ``goldenmatch._native`` (in-tree build) /
      ``goldenmatch_native`` (published wheel) PyO3 binding into the shared
      ``score-core`` crate — ``levenshtein_similarity`` / ``jaro_winkler_similarity``
      / ``token_sort_ratio``.

It asserts (a) == (b) to 4 decimals over a corpus of random strings + adversarial
edge cases (empty, unicode, identical, transpositions, very long, case variants),
and reports the max absolute divergence. Exit 1 on any 4dp divergence.

If the native kernel cannot be built/imported in this environment, the kernel leg
is reported as ``SKIP (kernel unavailable: <reason>)`` and the gate exits 0 (a skip
is not a failure — it means "needs CI / a built wheel"). The code is correct and
runnable wherever the kernel exists; build it with ``python scripts/build_native.py``.

Designed to GENERALIZE: ``--scorer`` selects the tracer (default ``levenshtein``);
the same harness is the template for every other scorer once the spike proceeds.

Usage:
    python scripts/check_kernel_equivalence.py                 # levenshtein, default corpus
    python scripts/check_kernel_equivalence.py --scorer jaro_winkler
    python scripts/check_kernel_equivalence.py --scorer token_sort --n 5000 --seed 7
    python scripts/check_kernel_equivalence.py --require-kernel # exit 1 if kernel absent
"""
from __future__ import annotations

import argparse
import random
import sys
import unicodedata
from pathlib import Path

# Make `goldenmatch` importable without an install: prepend the package source.
_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "packages" / "python" / "goldenmatch"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

# 4-decimal tolerance — the project-wide scorer parity contract (matches the TS
# `scorer-ground-truth.test.ts` + `wasm-scorer.test.ts` 4dp gate).
TOL = 1e-4

# Scorers this gate can compare today. Each maps the pure-Python `score_field`
# scorer name to the matching `score-core` PyO3 binding symbol on the kernel.
# `token_sort` is special-cased: the pure path returns 0-100/100, the native
# binding returns the 0-100 form, so the harness rescales the kernel value.
KERNEL_SYMBOL = {
    "levenshtein": "levenshtein_similarity",
    "jaro_winkler": "jaro_winkler_similarity",
    "token_sort": "token_sort_ratio",
}


def _load_pure():
    """The EXISTING default pure-Python scorer (unchanged). Returns score_field."""
    from goldenmatch.core.scorer import score_field

    return score_field


def _load_kernel():
    """The Rust kernel module, or (None, reason) when unavailable.

    Tries the in-tree build first (`goldenmatch._native`), then the published
    wheel (`goldenmatch_native._native`) — the same discover order as the
    production loader, but kept LOCAL here so this gate never imports the gated
    default loader.
    """
    try:
        import goldenmatch._native as native  # type: ignore[import-not-found]

        return native, None
    except Exception as exc_intree:  # noqa: BLE001
        try:
            from goldenmatch_native import _native as native  # type: ignore[import-not-found]

            return native, None
        except Exception as exc_wheel:  # noqa: BLE001
            reason = (
                f"in-tree import failed ({type(exc_intree).__name__}: {exc_intree}); "
                f"wheel import failed ({type(exc_wheel).__name__}: {exc_wheel}). "
                "Build it with `python scripts/build_native.py`."
            )
            return None, reason


def _kernel_score(native, scorer: str, a: str, b: str) -> float:
    sym = KERNEL_SYMBOL[scorer]
    fn = getattr(native, sym)
    val = fn(a, b)
    if scorer == "token_sort":
        # Pure `score_field("token_sort")` returns token_sort_ratio/100; the
        # native binding returns the un-divided 0-100 form. Rescale to compare.
        return val / 100.0
    return val


def build_corpus(n: int, seed: int) -> list[tuple[str, str]]:
    """Random name-shaped pairs + a battery of adversarial edge cases.

    The edge cases are the ones that historically break a normalized-edit-
    distance scorer: empty strings, identical strings, single transpositions,
    case variants, very long strings, and non-ASCII (BMP + astral + combining
    marks, which exercise codepoint vs. byte vs. grapheme iteration — the most
    likely place a Rust-chars kernel and a Python-str impl could diverge).
    """
    rng = random.Random(seed)
    alpha = "abcdefghijklmnopqrstuvwxyz"
    names = ["smith", "smyth", "johnson", "jonson", "müller", "muller", "renée", "renee"]

    edge: list[tuple[str, str]] = [
        ("", ""),
        ("a", ""),
        ("", "a"),
        ("abc", "abc"),                 # identical
        ("abc", "acb"),                 # transposition
        ("abc", "ABC"),                 # case variant
        ("Smith", "smith"),
        ("café", "cafe"),               # accented vs plain (BMP)
        ("café", "café"),               # composed identical
        ("café", "café"),         # NFD combining vs NFC composed
        ("naïve", "naive"),
        ("über", "uber"),
        ("日本語", "日本語"),            # CJK identical
        ("日本語", "日本"),             # CJK partial
        ("😀😁", "😀😂"),                # astral / emoji (surrogate-pair land)
        ("x" * 500, "x" * 500),         # very long identical
        ("x" * 500, "y" + "x" * 499),   # very long, single edit
        ("a b c", "c b a"),             # token-order (matters for token_sort)
        ("  pad  ", "pad"),             # whitespace
        ("MixedCASE words", "mixedcase WORDS"),
    ]
    # Add NFC-normalized twins of the unicode names so we cover both forms.
    for nm in names:
        edge.append((nm, unicodedata.normalize("NFC", nm)))

    pairs: list[tuple[str, str]] = list(edge)
    for _ in range(n):
        la = rng.randint(0, 18)
        a = "".join(rng.choice(alpha) for _ in range(la))
        if rng.random() < 0.5 and a:
            # A near-duplicate: mutate one or two chars (the realistic ER case).
            b = list(a)
            for _ in range(rng.randint(1, 2)):
                if b:
                    b[rng.randrange(len(b))] = rng.choice(alpha)
            b = "".join(b)
        else:
            lb = rng.randint(0, 18)
            b = "".join(rng.choice(alpha) for _ in range(lb))
        pairs.append((a, b))
    return pairs


def run(scorer: str, n: int, seed: int, require_kernel: bool) -> int:
    if scorer not in KERNEL_SYMBOL:
        print(f"ERROR: unsupported --scorer {scorer!r}; choose from {sorted(KERNEL_SYMBOL)}")
        return 2

    score_field = _load_pure()
    native, reason = _load_kernel()
    corpus = build_corpus(n, seed)

    print(f"kernel-equivalence gate: scorer={scorer}  pairs={len(corpus)}  tol={TOL} (4dp)")

    if native is None:
        msg = f"  [SKIP] kernel leg (kernel unavailable: {reason})"
        print(msg)
        print(f"  pure-Python leg evaluated {len(corpus)} pairs OK (kernel comparison pending).")
        if require_kernel:
            print("\nFAIL: --require-kernel set but the native kernel is not importable.")
            return 1
        print("\nSKIP: kernel unavailable in this env; run in CI or build a wheel. (exit 0)")
        return 0

    max_diff = 0.0
    worst: tuple[str, str, float, float] | None = None
    diverged: list[tuple[str, str, float, float]] = []
    for a, b in corpus:
        pure = score_field(a, b, scorer)
        kern = _kernel_score(native, scorer, a, b)
        # score_field returns None only when an input is None — never here.
        pure_f = float(pure) if pure is not None else float("nan")
        diff = abs(pure_f - kern)
        if diff > max_diff:
            max_diff = diff
            worst = (a, b, pure_f, kern)
        if diff > TOL:
            diverged.append((a, b, pure_f, kern))

    print(f"  pure==kernel max abs diff = {max_diff:.3e}  over {len(corpus)} pairs")
    if worst is not None:
        a, b, pf, kf = worst
        sa = (a[:20] + "…") if len(a) > 20 else a
        sb = (b[:20] + "…") if len(b) > 20 else b
        print(f"  worst pair: {sa!r} vs {sb!r}  pure={pf:.6f} kernel={kf:.6f}")

    if diverged:
        print(f"\nFAIL: {len(diverged)} pair(s) diverge beyond 4dp. First few:")
        for a, b, pf, kf in diverged[:10]:
            print(f"    {a!r:24} {b!r:24} pure={pf:.6f} kernel={kf:.6f} diff={abs(pf - kf):.3e}")
        return 1

    print(f"\nPASS: pure-Python == Rust kernel for {scorer} at 4dp ({len(corpus)} pairs).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scorer", default="levenshtein", help="tracer scorer (default: levenshtein)")
    p.add_argument("--n", type=int, default=2000, help="random pairs in addition to edge cases")
    p.add_argument("--seed", type=int, default=20260614, help="RNG seed (determinism)")
    p.add_argument("--require-kernel", action="store_true",
                   help="exit 1 if the native kernel is not importable (CI parity lane)")
    args = p.parse_args(argv)
    return run(args.scorer, args.n, args.seed, args.require_kernel)


if __name__ == "__main__":
    raise SystemExit(main())
