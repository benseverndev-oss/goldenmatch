"""Shuffle BYTES per stage, from Spark's REST API.

## Why bytes and not seconds

The open question on the Spark tier is whether GoldenMatch's advantage survives
a real multi-node cluster. The rig cannot answer it: both workers are containers
on ONE host, so a shuffle never crosses a network, and a wall measured there
says nothing about one that does.

Bytes do transfer. Network cost on ANY topology is a function of what crosses
the exchange, so measuring that answers the question without needing the
network -- and without the overlay-network hack, whose own DERP relay fallback
would make a latency measurement meaningless in a way the output would not show.

The prediction this tests: GoldenMatch's counting stage is a `GROUP BY` over
agreement patterns whose output is bounded by `prod(levels + 1)`, and Spark
combines map-side, so what crosses should be ~`partitions x distinct patterns`
regardless of pair count -- kilobytes. Splink re-scans pairs per EM iteration,
so its shuffle should scale with pairs and repeat ~26 times. If that holds, the
advantage widens with cluster size rather than narrowing, and the claim needs no
multi-node wall to stand.

## Why the parsing is a separate function

`summarize()` takes the decoded `/stages` payload and is unit-testable; only
`fetch()` touches the network. A metric that silently reports zero because a
field was renamed is the failure mode here, so `summarize` distinguishes "no
shuffle" from "no stages" and says which.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def summarize(stages: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any]:
    """Totals plus the heaviest stages, from a Spark `/stages` payload.

    `stages` is the decoded list. Keys are Spark's own
    (`shuffleWriteBytes` / `shuffleReadBytes`); a payload missing BOTH on every
    stage yields `fields_present: False` rather than a confident zero, because a
    renamed field and a genuinely shuffle-free job are not the same finding.
    """
    if not stages:
        return {"n_stages": 0, "fields_present": False,
                "shuffle_write_bytes": None, "shuffle_read_bytes": None,
                "top_stages": [],
                "note": "no stages returned -- wrong app id, or the UI was gone"}

    seen_field = False
    rows = []
    for st in stages:
        w = st.get("shuffleWriteBytes")
        r = st.get("shuffleReadBytes")
        if w is not None or r is not None:
            seen_field = True
        rows.append({
            "stage_id": st.get("stageId"),
            "name": (st.get("name") or "")[:80],
            "write_bytes": int(w or 0),
            "read_bytes": int(r or 0),
            "num_tasks": st.get("numTasks"),
        })

    if not seen_field:
        return {"n_stages": len(stages), "fields_present": False,
                "shuffle_write_bytes": None, "shuffle_read_bytes": None,
                "top_stages": [],
                "note": ("no stage carried shuffleWriteBytes/shuffleReadBytes -- "
                         "the field names moved; do NOT read this as zero shuffle")}

    rows.sort(key=lambda r: r["write_bytes"] + r["read_bytes"], reverse=True)
    return {
        "n_stages": len(stages),
        "fields_present": True,
        "shuffle_write_bytes": sum(r["write_bytes"] for r in rows),
        "shuffle_read_bytes": sum(r["read_bytes"] for r in rows),
        "top_stages": rows[:top_n],
    }


def fetch(base_url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Shuffle totals for the newest application at `base_url` (a Spark UI).

    Never raises: this is instrumentation attached to a benchmark, and a
    metrics endpoint that is down must not take the measurement with it. The
    error is RECORDED so an absent number reads as "the probe failed", never as
    "there was no shuffle".
    """
    def _get(path: str) -> Any:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8"))

    try:
        apps = _get("/api/v1/applications")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}", "base_url": base_url}
    if not apps:
        return {"error": "no applications at this UI", "base_url": base_url}

    app_id = apps[0].get("id")
    try:
        stages = _get(f"/api/v1/applications/{app_id}/stages")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:160]}",
                "base_url": base_url, "app_id": app_id}

    out = summarize(stages)
    out["app_id"] = app_id
    out["base_url"] = base_url
    return out
