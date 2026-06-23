"""Performance metrics for the perceptual kernel — throughput + native speedup.

Toggles ``GOLDENMATCH_NATIVE`` so each path is measured under the real dispatch
(`native_enabled("perceptual")` reads the env per call). The native path is
skipped gracefully when the extension isn't built.
"""
from __future__ import annotations

import os
import time

from goldenmatch.core import perceptual


def _time(fn, repeats: int) -> float:
    t0 = time.perf_counter()
    for _ in range(repeats):
        fn()
    return time.perf_counter() - t0


def _both_paths(work, n_units: int, repeats: int) -> dict:
    """Run ``work`` under GOLDENMATCH_NATIVE=0 and =1, report throughput + speedup."""
    prev = os.environ.get("GOLDENMATCH_NATIVE")
    out: dict = {}
    try:
        for mode, label in (("0", "python"), ("1", "native")):
            os.environ["GOLDENMATCH_NATIVE"] = mode
            try:
                dt = _time(work, repeats)
            except RuntimeError:
                out[label] = None  # native=1 but ext not importable
                continue
            out[label] = {"wall_sec": dt, "units_per_sec": repeats * n_units / dt}
    finally:
        if prev is None:
            os.environ.pop("GOLDENMATCH_NATIVE", None)
        else:
            os.environ["GOLDENMATCH_NATIVE"] = prev
    if out.get("python") and out.get("native"):
        out["speedup"] = out["python"]["wall_sec"] / out["native"]["wall_sec"]
    return out


def bench_image_hash(grids: list, repeats: int = 3) -> dict:
    """Throughput of the image pHash over a batch of luma grids (the column path)."""
    return _both_paths(lambda: perceptual.phash_image_batch(grids), len(grids), repeats)


def bench_audio_hash(signals: list[tuple[list, int]], repeats: int = 2) -> dict:
    """Throughput of the audio fingerprint over a batch of (samples, sample_rate)."""
    def work():
        for samples, sr in signals:
            perceptual.fingerprint_audio(samples, sr)

    return _both_paths(work, len(signals), repeats)
