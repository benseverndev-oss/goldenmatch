"""Audio fingerprint noise robustness on realistic broadband audio (ADR 0022,
finding 3).

The bench harness first measured 0.0 noise recall -- but that used *pure tones*,
which are pathological for the Haitsma-Kalker fingerprint: a 3-tone signal leaves
most log-spaced bands near-empty, so each bit is the sign of a ~zero energy
difference = pure noise. On realistic broadband audio the bands carry energy and
the fingerprint *is* noise-robust. This test locks that: at the canonical
Haitsma-Kalker match point (BER <= 0.35, similarity >= 0.65, now the auto-config
default) a moderately noisy copy still matches its source, well clear of unrelated
audio -- which the prior 0.80 default missed.
"""
from __future__ import annotations

import math

from goldenmatch.core import perceptual

_SR = 44100


class _LCG:
    def __init__(self, seed: int) -> None:
        self.s = (seed * 2862933555777941757 + 3037000493) & ((1 << 64) - 1)

    def signed(self, amp: int) -> int:
        self.s = (self.s * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (self.s >> 33) % (2 * amp + 1) - amp


def _broadband(seed: int, length: int = 16000, k: int = 40) -> list[float]:
    """k sinusoids across the 300-2000 Hz analysis band -- realistic spectrum."""
    rng = _LCG(seed * 2654435761 + 1)
    comps = [
        (
            300.0 + (rng.signed(1 << 20) + (1 << 20)) / (1 << 21) * 1700.0,
            (rng.signed(1 << 20) + (1 << 20)) / (1 << 21) * (2 * math.pi),
        )
        for _ in range(k)
    ]
    return [
        sum(math.sin(2 * math.pi * f * (n / _SR) + ph) for f, ph in comps) / k
        for n in range(length)
    ]


def _noisy(sig: list[float], seed: int, snr_db: float = 20.0) -> list[float]:
    rng = _LCG(seed * 211 + 13)
    s_rms = math.sqrt(sum(v * v for v in sig) / len(sig)) or 1e-9
    scale = (s_rms / (10.0 ** (snr_db / 20.0))) * math.sqrt(3.0)
    return [v + (rng.signed(1 << 20) / (1 << 20)) * scale for v in sig]


def _fp(sig: list[float]) -> list[int]:
    return perceptual.fingerprint_audio(sig, _SR)


def _sim(a: list[int], b: list[int]) -> float:
    return 1.0 - perceptual.audio_ber_aligned(a, b)


_THRESHOLD = 0.65  # canonical Haitsma-Kalker BER <= 0.35


def test_broadband_audio_survives_moderate_noise():
    bases = [_broadband(s) for s in range(3)]
    fps = [_fp(b) for b in bases]
    noisy_fps = [_fp(_noisy(b, s)) for s, b in enumerate(bases)]

    # every noisy copy matches its source above the canonical threshold
    match_sims = [_sim(fps[i], noisy_fps[i]) for i in range(len(bases))]
    assert min(match_sims) > _THRESHOLD, match_sims

    # ...and stays clear of unrelated audio (which the threshold rejects)
    unrel_sims = [
        _sim(fps[i], fps[j])
        for i in range(len(bases))
        for j in range(len(bases))
        if i != j
    ]
    assert max(unrel_sims) < _THRESHOLD
    assert min(match_sims) > max(unrel_sims)  # clean separation


def test_broadband_audio_amplitude_and_shift_invariant():
    base = _broadband(7)
    fp = _fp(base)
    assert _sim(fp, _fp([0.5 * v for v in base])) == 1.0  # gain change
    assert _sim(fp, _fp(base[4096:])) == 1.0  # ~2-frame time offset (alignment search)


def test_pure_tone_is_the_noise_artifact():
    # documents WHY finding 3's first read was wrong: a pure tone leaves the bands
    # near-empty, so the SAME noise destroys the fingerprint (BER ~0.5), unlike the
    # broadband case above. This is a dataset artifact, not a kernel limit.
    tone = [
        sum(math.sin(2 * math.pi * f * (n / _SR)) for f in (440.0, 660.0, 880.0)) / 3
        for n in range(16000)
    ]
    sim = _sim(_fp(tone), _fp(_noisy(tone, 1)))
    assert sim < _THRESHOLD  # the tone-noise pair is NOT recoverable (the artifact)
