"""GET /api/v1/domains — list available domain rulebooks.

Wraps ``goldenmatch.core.domain_registry.discover_rulebooks`` so the workbench
can let a user pick a domain pack (electronics, people, healthcare, …) before
running autoconfig. Returns enough metadata for a picker — name, signal
count, brand count — without serializing the regex patterns.

The actual domain override is plumbed through ``POST /api/v1/autoconfig``
via the ``domain`` query parameter (see web/routers/autoconfig.py).
"""
from __future__ import annotations

from fastapi import APIRouter

from goldenmatch.core.domain_registry import discover_rulebooks

router = APIRouter(prefix="/api/v1/domains")


@router.get("")
def list_domains() -> list[dict]:
    rbs = discover_rulebooks()
    out: list[dict] = []
    for name, rb in sorted(rbs.items()):
        out.append({
            "name": name,
            "signals": list(rb.signals),
            "signal_count": len(rb.signals),
            "brand_count": len(rb.brand_patterns),
            "identifier_count": len(rb.identifier_patterns),
        })
    return out
