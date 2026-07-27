"""Deterministic blocker + hard/easy negative-pair synthesis for the
multi-source ER data pipeline (Task 3). Consumed by loaders whose source
data ships only positive (matching) pairs -- e.g. Leipzig + FEBRL -- and by
the synthetic generator. CPU/box-safe: stdlib only, never imports
torch/transformers.

Sampling is bounded to O(n) memory -- it never materializes the full
within-block or cross-block pair product, which would be O(N^2) and OOM
at the ~64k-row scale some loaders (e.g. DBLP-Scholar) feed in."""
from __future__ import annotations

import random
from collections.abc import Callable

_ATTEMPT_MULTIPLIER = 50
_MIN_ATTEMPTS = 100


def blocking_key(entity: dict, block_keys: list[str]) -> str:
    """Deterministic blocking key: the lowercased first whitespace-token of
    the space-joined values of `block_keys` on `entity`. Missing/empty
    fields are tolerated (treated as empty string)."""
    joined = " ".join(str(entity.get(k, "")) for k in block_keys)
    tokens = joined.lower().split()
    return tokens[0] if tokens else ""


def _fill(
    target: int,
    cap: int,
    candidate_fn: Callable[[], tuple[str, str] | None],
    seen: set[tuple[str, str]],
    result: list[tuple[str, str]],
) -> None:
    """Rejection-sample up to `target` distinct pairs into `result`/`seen`,
    trying at most `cap` times total. Mutates `result`/`seen` in place so a
    shortfall-fill call can resume from where an earlier call left off."""
    attempts = 0
    while len(result) < target and attempts < cap:
        attempts += 1
        cand = candidate_fn()
        if cand is None:
            continue
        pair = (cand[0], cand[1]) if cand[0] < cand[1] else (cand[1], cand[0])
        if pair in seen:
            continue
        seen.add(pair)
        result.append(pair)


def synth_negatives(
    entities: dict[str, dict],
    *,
    block_keys: list[str],
    hard_frac: float,
    seed: int,
    n: int,
) -> list[tuple[str, str, str]]:
    """Synthesize up to `n` negative pairs (eid_a, eid_b, tag) from
    `entities` (eid -> field dict). "hard" pairs share a blocking key
    (the deceptive boundary case); "easy" pairs come from different
    blocks. Deterministic given `seed`. No self-pairs, no duplicate pairs.
    Falls back to filling the remainder from the other pool when one pool
    is short rather than erroring.

    Sampling is rejection-based and bounded to O(n) memory/attempts -- it
    never enumerates the full within-block or cross-block pair product."""
    rng = random.Random(seed)
    eids = sorted(entities)

    blocks: dict[str, list[str]] = {}
    for eid in eids:
        key = blocking_key(entities[eid], block_keys)
        blocks.setdefault(key, []).append(eid)

    non_singleton_blocks = [blocks[k] for k in sorted(blocks) if len(blocks[k]) >= 2]

    def hard_candidate() -> tuple[str, str] | None:
        if not non_singleton_blocks:
            return None
        members = rng.choice(non_singleton_blocks)
        a, b = rng.sample(members, 2)
        return (a, b)

    def easy_candidate() -> tuple[str, str] | None:
        if len(eids) < 2:
            return None
        a, b = rng.sample(eids, 2)
        if blocking_key(entities[a], block_keys) == blocking_key(entities[b], block_keys):
            return None
        return (a, b)

    n_hard_target = round(n * hard_frac)
    n_easy_target = n - n_hard_target

    hard_seen: set[tuple[str, str]] = set()
    hard_result: list[tuple[str, str]] = []
    cap = max(_ATTEMPT_MULTIPLIER * n, _MIN_ATTEMPTS)
    _fill(n_hard_target, cap, hard_candidate, hard_seen, hard_result)

    easy_seen: set[tuple[str, str]] = set()
    easy_result: list[tuple[str, str]] = []
    _fill(n_easy_target, cap, easy_candidate, easy_seen, easy_result)

    # Fill shortfall in one pool from the other, capped by a fresh attempt
    # budget (never full enumeration).
    shortfall = n - (len(hard_result) + len(easy_result))
    if shortfall > 0:
        extra_cap = max(_ATTEMPT_MULTIPLIER * shortfall, _MIN_ATTEMPTS)
        _fill(len(easy_result) + shortfall, extra_cap, easy_candidate, easy_seen, easy_result)
        shortfall = n - (len(hard_result) + len(easy_result))
    if shortfall > 0:
        extra_cap = max(_ATTEMPT_MULTIPLIER * shortfall, _MIN_ATTEMPTS)
        _fill(len(hard_result) + shortfall, extra_cap, hard_candidate, hard_seen, hard_result)

    result = [(a, b, "hard") for a, b in hard_result]
    result += [(a, b, "easy") for a, b in easy_result]
    return result
