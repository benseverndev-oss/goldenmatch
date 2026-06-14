#!/usr/bin/env python3
"""Assert the goldenmatch native scoring kernel loaded on the Ray cluster WORKERS.

Shipped to the head via `ray submit` from bench-ray-cluster.yml. The scoring
kernel is the dominant distributed stage (#688); without it, NATIVE=auto/1 on
the cluster either silently falls back to pure Python (100x+ slower) or only
raises 20+ min into the run at the first score call. This catches a failed
native build BEFORE the bench, by scheduling tasks across the cluster and
asserting at least one WORKER (not just the head) loaded `goldenmatch._native`.

Exit 0 if a worker has native; exit 1 (with a GH `::error::`) otherwise.
"""
from __future__ import annotations

import socket
import sys


def main() -> int:
    import ray

    ray.init(address="auto")
    head_host = socket.gethostname()

    @ray.remote(num_cpus=1)
    def _probe() -> tuple[str, bool]:
        import socket as _s

        from goldenmatch.core._native_loader import native_available

        return _s.gethostname(), bool(native_available())

    # Fan out enough tasks that some land on workers, not just the head.
    results = ray.get([_probe.remote() for _ in range(16)])

    worker_ok = False
    any_ok = False
    for host, ok in sorted(set(results)):
        where = "head" if host == head_host else "worker"
        print(f"  {where} {host}: native_available={ok}")
        any_ok = any_ok or ok
        if ok and host != head_host:
            worker_ok = True

    if not worker_ok:
        # No worker confirmed native. If only the head has it, the workers
        # (which do the scoring) would still run pure-Python.
        print(
            "::error::goldenmatch._native did NOT load on any Ray WORKER -- "
            "distributed scoring would run pure-Python (100x+ slower). The "
            "native build in cluster-gce.yaml setup_commands failed on the "
            "workers, or GOLDENMATCH_NATIVE=1 isn't exported before ray start."
        )
        return 1

    print("native scoring kernel confirmed on a worker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
