"""Field transform utilities for GoldenMatch."""

from __future__ import annotations

import hashlib
import re

import jellyfish


def apply_transform(value: str | None, transform: str) -> str | None:
    """Apply a single named transform to a string value.

    Args:
        value: The input string, or None.
        transform: The transform name (e.g. "lowercase", "soundex", "substring:0:3").

    Returns:
        The transformed string, or None if value is None.

    Raises:
        ValueError: If the transform name is not recognised.
    """
    if value is None:
        return None

    if transform == "lowercase":
        return value.lower()
    elif transform == "uppercase":
        return value.upper()
    elif transform == "strip":
        return value.strip()
    elif transform == "strip_all":
        return re.sub(r"\s+", "", value)
    elif transform.startswith("substring:"):
        parts = transform.split(":")
        start = int(parts[1])
        end = int(parts[2])
        return value[start:end]
    elif transform == "soundex":
        return jellyfish.soundex(value)
    elif transform == "metaphone":
        return jellyfish.metaphone(value)
    elif transform == "digits_only":
        return re.sub(r"[^0-9]", "", value)
    elif transform == "alpha_only":
        return re.sub(r"[^a-zA-Z]", "", value)
    elif transform == "normalize_whitespace":
        return re.sub(r"\s+", " ", value).strip()
    elif transform == "token_sort":
        tokens = value.strip().split()
        return " ".join(sorted(tokens))
    elif transform.startswith("qgram:"):
        q = int(transform.split(":")[1])
        padded = f"##{value}##"
        grams = sorted(set(padded[i:i + q] for i in range(len(padded) - q + 1)))
        return " ".join(grams[:5])
    elif transform == "first_token":
        tokens = value.strip().split()
        return tokens[0] if tokens else value
    elif transform == "last_token":
        tokens = value.strip().split()
        return tokens[-1] if tokens else value
    elif transform == "bloom_filter" or transform.startswith("bloom_filter:"):
        return _bloom_filter_transform(value, transform)
    else:
        # Plugin transform fallback: consult the registry so refdata-style
        # extensions (legal_form_strip, address_token_normalize, …) work
        # through the same code path as built-ins. Same fall-through model
        # as core.scorer.score_field's plugin scorer path.
        from goldenmatch.plugins.registry import PluginRegistry

        plugin = PluginRegistry.instance().get_transform(transform)
        if plugin is None:
            raise ValueError(f"Unknown transform: {transform!r}")
        return plugin.transform(value)  # pyright: ignore[reportAttributeAccessIssue]


# Bloom-filter (CLK) security level presets: (ngram_size, num_hashes, filter_size).
_BLOOM_SECURITY_LEVELS = {
    "standard": (2, 20, 512),    # bigram, 20 hashes, 512 bits
    "high": (2, 30, 1024),       # bigram, 30 hashes, 1024 bits, per-field HMAC
    "paranoid": (3, 40, 2048),   # trigram, 40 hashes, 2048 bits, balanced padding
}


def _parse_bloom_params(transform: str) -> tuple[int, int, int, str | None, bool]:
    """Parse a bloom_filter transform spec into its parameters.

    Returns (ngram_size, num_hashes, filter_size, hmac_key, balanced). This is
    the ONE parser for the bloom spec -- both the scalar and the batch (native
    or pure-Python) paths consult it so they can never drift.

    Accepts: ``bloom_filter`` | ``bloom_filter:{standard,high,paranoid}`` |
    ``bloom_filter:ngram:k:size[:hmac_key]``.
    """
    if transform == "bloom_filter":
        return 2, 20, 1024, None, False
    if transform.count(":") == 1 and transform.split(":")[1] in _BLOOM_SECURITY_LEVELS:
        level = transform.split(":")[1]
        ngram_size, num_hashes, filter_size = _BLOOM_SECURITY_LEVELS[level]
        hmac_key = "default_field_key" if level in ("high", "paranoid") else None
        return ngram_size, num_hashes, filter_size, hmac_key, level == "paranoid"
    parts = transform.split(":")
    ngram_size = int(parts[1])
    num_hashes = int(parts[2])
    filter_size = int(parts[3])
    hmac_key = parts[4] if len(parts) > 4 else None
    return ngram_size, num_hashes, filter_size, hmac_key, False


def _prepare_bloom_input(value: str, ngram_size: int, balanced: bool) -> str:
    """All the Unicode-sensitive preprocessing, kept in Python deliberately.

    Lowercase + strip, ``_``-pad to at least one n-gram, then (paranoid only)
    append a deterministic salt to short strings to normalize filter density.
    Keeping this in Python -- rather than in the native kernel -- is what makes
    the kernel byte-exact by construction (Python ``str.lower()``/``str.strip()``
    casing/whitespace rules differ from Rust ``to_lowercase()``/``trim()``).
    """
    padded = value.lower().strip()
    if len(padded) < ngram_size:
        padded = padded.ljust(ngram_size, "_")
    # Balanced padding: pad short strings with deterministic salt.
    if balanced and len(padded) < 8:
        padded = padded + hashlib.sha256(padded.encode()).hexdigest()[:8]
    return padded


def _clk_from_prepared(
    prepared: str,
    ngram_size: int,
    num_hashes: int,
    filter_size: int,
    hmac_key: str | None,
) -> str:
    """Pure-Python reference for the CLK hash loop (the parity oracle for the
    Rust ``bloom_clk_batch`` kernel).

    Takes the already-prepared string from :func:`_prepare_bloom_input` and
    runs the per-ngram / per-hash double loop into a fixed-size bit array,
    returned as a lowercase hex string.
    """
    bits = bytearray(filter_size // 8)
    ngrams = [prepared[i:i + ngram_size] for i in range(len(prepared) - ngram_size + 1)]
    for ngram in ngrams:
        for k in range(num_hashes):
            if hmac_key:
                # Per-field HMAC salting: prevents cross-field correlation attacks.
                import hmac as hmac_mod
                h = hmac_mod.new(
                    f"{hmac_key}:{k}".encode(), ngram.encode(), hashlib.sha256
                ).hexdigest()
            else:
                h = hashlib.sha256(f"{k}:{ngram}".encode()).hexdigest()
            bit_pos = int(h, 16) % filter_size
            bits[bit_pos // 8] |= (1 << (bit_pos % 8))
    return bits.hex()


def _bloom_filter_transform(value: str, transform: str) -> str:
    """Convert a string to a CLK (Cryptographic Longterm Key) as hex string.

    Generates character-level n-grams, hashes each with k hash functions into a
    fixed-size bit array, returns the bit array as a hex string. Byte-identical
    to the pre-refactor implementation -- now a thin compose over the shared
    parse/prepare/hash helpers so the batch path reuses the exact same logic.

    Parameterized: bloom_filter or bloom_filter:ngram:k:size
    Security levels: bloom_filter:standard, bloom_filter:high, bloom_filter:paranoid
    """
    ngram_size, num_hashes, filter_size, hmac_key, balanced = _parse_bloom_params(transform)
    prepared = _prepare_bloom_input(value, ngram_size, balanced)
    return _clk_from_prepared(prepared, ngram_size, num_hashes, filter_size, hmac_key)


def bloom_clk_batch(values: list[str | None], transform: str) -> list[str | None]:
    """Column-level CLK generation -- one bulk call instead of a per-row loop.

    Uses the native ``bloom_clk_batch`` kernel when ``GOLDENMATCH_NATIVE`` gates
    ``"pprl_bloom"`` on, otherwise the pure-Python reference. Output is
    byte-identical either way (asserted in tests/test_native_bloom_parity.py).
    ``None`` inputs pass through as ``None`` (matching ``apply_transform(None)``).
    """
    from goldenmatch.core._native_loader import native_enabled, native_module

    ngram_size, num_hashes, filter_size, hmac_key, balanced = _parse_bloom_params(transform)

    # Non-None positions; None rows pass through and are stitched back at the end.
    idx = [i for i, v in enumerate(values) if v is not None]
    prepared = [_prepare_bloom_input(values[i], ngram_size, balanced) for i in idx]  # type: ignore[arg-type]

    if native_enabled("pprl_bloom"):
        clks = native_module().bloom_clk_batch(
            prepared, ngram_size, num_hashes, filter_size, hmac_key
        )
    else:
        clks = [
            _clk_from_prepared(p, ngram_size, num_hashes, filter_size, hmac_key)
            for p in prepared
        ]

    out: list[str | None] = [None] * len(values)
    for pos, clk in zip(idx, clks):
        out[pos] = clk
    return out


def apply_transforms(value: str | None, transforms: list[str]) -> str | None:
    """Apply a chain of transforms to a string value.

    Args:
        value: The input string, or None.
        transforms: List of transform names to apply in order.

    Returns:
        The transformed string, or None if value is None.
    """
    if value is None:
        return None

    for t in transforms:
        value = apply_transform(value, t)
    return value
