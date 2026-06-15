#!/usr/bin/env python3
"""Assert the goldenmatch native scoring kernel loaded on EVERY Ray cluster node.

Shipped to the head via `ray submit` from bench-ray-cluster.yml. The scoring
kernel is the dominant distributed stage (#688); without it, NATIVE=1 on the
cluster only raises 20+ min into the run at the first score call. This catches
a failed/absent native install BEFORE the bench.

Probing correctly is the trick: a plain fan-out of `num_cpus=1` tasks PACKS onto
the head (which has plenty of CPUs) and never reaches a worker -- a false
negative that skips the whole bench. So we pin ONE probe to EACH alive node via
NodeAffinitySchedulingStrategy(soft=False) and require every node to report
`goldenmatch._native` loaded. Exit 1 (with a GH `::error::`) on any miss.
"""
from __future__ import annotations

import sys
import time


def main() -> int:
    import ray
    from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

    ray.init(address="auto")

    @ray.remote(num_cpus=1)
    def _probe() -> tuple[str, bool]:
        import socket

        from goldenmatch.core._native_loader import native_available

        return socket.gethostname(), bool(native_available())

    # Workers may be RUNNING (the bench's worker-join check passed) but still
    # finishing setup_commands -> not yet joined the Ray cluster. Give them a
    # short window so we actually probe a worker, then probe whatever is alive.
    deadline = time.time() + 180
    while time.time() < deadline:
        if len([n for n in ray.nodes() if n.get("Alive")]) >= 2:
            break
        time.sleep(15)

    nodes = [n for n in ray.nodes() if n.get("Alive")]
    if not nodes:
        print("::error::no alive Ray nodes -- cannot verify native.")
        return 1

    # Pin one probe to each alive node so workers are actually checked.
    results = ray.get([
        _probe.options(
            scheduling_strategy=NodeAffinitySchedulingStrategy(
                node_id=n["NodeID"], soft=False
            )
        ).remote()
        for n in nodes
    ])

    missing = [host for host, ok in results if not ok]
    for host, ok in results:
        print(f"  node {host}: native_available={ok}")

    if missing:
        print(
            f"::error::goldenmatch._native did NOT load on node(s) {missing} -- "
            "distributed scoring there would run pure-Python (100x+ slower). "
            "Check the goldenmatch-native install in cluster-gce.yaml setup_commands."
        )
        return 1

    print(f"native scoring kernel confirmed on all {len(results)} alive node(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
