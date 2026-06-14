"""ClusterProfile -- the "what hardware is available" signal for the
distributed-routing rule layer.

Probe-or-descriptor: probe a live Ray context when connected, else accept a
caller-supplied descriptor (CLI / MCP), else single box. The in-memory-vs-
distributed decision for materializing stages keys off DRIVER RAM
(RuntimeProfile.available_ram_gb), NOT cluster_mem_gb -- see the routing rules.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClusterProfile:
    present: bool
    num_nodes: int
    total_cpus: int
    cluster_mem_gb: float
    driver_mem_gb: float
    source: str  # "probe" | "descriptor" | "single_box"


_SINGLE_BOX = ClusterProfile(
    present=False, num_nodes=1, total_cpus=0,
    cluster_mem_gb=0.0, driver_mem_gb=0.0, source="single_box",
)


def _probe(ray_module: Any) -> ClusterProfile | None:
    try:
        if not ray_module.is_initialized():
            return None
        res = ray_module.cluster_resources()
        nodes = [n for n in ray_module.nodes() if n.get("Alive", True)]
    except Exception:
        return None
    cpus = int(res.get("CPU", 0))
    mem_gb = float(res.get("memory", 0)) / (1024 ** 3)
    return ClusterProfile(
        present=True, num_nodes=max(1, len(nodes)), total_cpus=cpus,
        cluster_mem_gb=mem_gb, driver_mem_gb=mem_gb / max(1, len(nodes)),
        source="probe",
    )


def _from_descriptor(d: Mapping[str, Any]) -> ClusterProfile:
    return ClusterProfile(
        present=True,
        num_nodes=int(d.get("num_nodes", 1)),
        total_cpus=int(d.get("total_cpus", 0)),
        cluster_mem_gb=float(d.get("cluster_mem_gb", 0.0)),
        driver_mem_gb=float(d.get("driver_mem_gb", 0.0)),
        source="descriptor",
    )


def capture_cluster_profile(
    *,
    descriptor: Mapping[str, Any] | None = None,
    ray_module: Any | None = "auto",
) -> ClusterProfile:
    """Resolve the cluster context. ``ray_module="auto"`` imports ray lazily;
    pass an explicit module (or ``None``) in tests to control the probe."""
    if ray_module == "auto":
        try:
            import ray as ray_module  # type: ignore
        except Exception:
            ray_module = None
    if ray_module is not None:
        probed = _probe(ray_module)
        if probed is not None:
            return probed
    if descriptor is not None:
        return _from_descriptor(descriptor)
    return _SINGLE_BOX
